from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from tradingagents.dataflows.tushare_capability import (
    CATEGORY_FINANCIAL,
    CATEGORY_GOVERNANCE,
    CATEGORY_MARKET,
    ENDPOINT_SPECS,
    EXCLUDED_ENDPOINTS,
    PERMISSION_ALLOWED,
    PERMISSION_DENIED,
    PERMISSION_UNKNOWN,
    STATE_HAS_DATA,
    STATE_NORMAL_NO_DATA,
    STATE_NOT_QUERIED,
    STATE_PERMISSION_DENIED,
    STATE_QUERY_FAILED,
    STATE_RATE_LIMITED,
    TOKEN_SOURCE_DOTENV,
    TOKEN_SOURCE_INHERITED_ENV,
    TOKEN_STATUS_HAS_KEY,
    annotate_row_cap_hits,
    build_matrix,
    build_params,
    canonical_frame_payload,
    classify_error_message,
    contains_credential_header_hint,
    is_transient_network_error,
    not_queried_record,
    payload_sha256,
    probe_endpoint,
    render_markdown,
    sanitize_error_text,
    with_fallback_date,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "tushare_capability"
SCRIPT = ROOT / "scripts" / "audit_tushare_capability.py"

TOKEN = "T" + "a1b2c3d4e5f6" * 4

PROBE_CONTEXT = {
    "ts_code": "603629.SH",
    "exchange": "SSE",
    "probe_date": "20260814",
    "fallback_date": "20260813",
    "window_start": "20260801",
    "statement_start": "20250101",
}


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _income_frame() -> pd.DataFrame:
    return _frame(
        [
            {"ts_code": "603629.SH", "end_date": "20260630", "ann_date": "20260826", "revenue": 100.5},
            {"ts_code": "603629.SH", "end_date": "20250331", "ann_date": "20250428", "revenue": 80.0},
        ]
    )


class TestClassification:
    @pytest.mark.parametrize(
        "message,expected",
        [
            ("抱歉，您没有访问该接口的权限", STATE_PERMISSION_DENIED),
            ("抱歉，您没有权限查询该数据", STATE_PERMISSION_DENIED),
            ("权限不足，无法访问", STATE_PERMISSION_DENIED),
            ("抱歉，您的积分不足，无法访问该接口", STATE_PERMISSION_DENIED),
            ("Sorry, you don't have permission to access this api", STATE_PERMISSION_DENIED),
            ("抱歉，您每分钟最多访问该接口5次", STATE_RATE_LIMITED),
            ("抱歉，您每小时最多访问该接口20次", STATE_RATE_LIMITED),
            ("系统检测到您的访问频率过高", STATE_RATE_LIMITED),
            ("too many requests", STATE_RATE_LIMITED),
            ("Connection to api.tushare.pro timed out", STATE_QUERY_FAILED),
            ("remote end closed connection without response", STATE_QUERY_FAILED),
        ],
    )
    def test_error_message_classification(self, message, expected):
        assert classify_error_message(message) == expected

    def test_rate_limit_takes_precedence_over_permission_words(self):
        assert (
            classify_error_message("访问频率过高，请稍后再试，您没有访问该接口的权限")
            == STATE_RATE_LIMITED
        )

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Connection to api.tushare.pro timed out. (read timeout=30)", True),
            ("remote end closed connection without response", True),
            ("SSLError: certificate verify failed", True),
            ("抱歉，您没有访问该接口的权限", False),
        ],
    )
    def test_transient_network_detection(self, message, expected):
        assert is_transient_network_error(message) is expected


class TestSanitization:
    def test_token_substring_removed(self):
        message = f"query failed with token {TOKEN} for endpoint income"
        cleaned = sanitize_error_text(message, TOKEN)
        assert TOKEN not in cleaned
        assert "[REDACTED]" in cleaned

    def test_authorization_and_cookie_headers_removed(self):
        message = "request failed Authorization: Bearer abc123 Cookie: sid=xyz token=zzz"
        cleaned = sanitize_error_text(message, "")
        assert "abc123" not in cleaned
        assert "sid=xyz" not in cleaned
        assert "zzz" not in cleaned
        assert "Authorization" in cleaned

    def test_opaque_blob_removed(self):
        message = "upstream error blob=8f3c1d94a7b65e02f18d9a4c7b3e5f60817293a4"
        cleaned = sanitize_error_text(message, "")
        assert "8f3c1d94a7b65e02f18d9a4c7b3e5f60817293a4" not in cleaned

    def test_output_capped(self):
        assert len(sanitize_error_text("x" * 5000)) <= 300

    def test_serialized_credential_header_hint_detected(self):
        # 001A-R1A final review P1#2: quoted/dict-serialized headers must be
        # detectable even when the value is too short for token/blob rules.
        assert contains_credential_header_hint('{"Cookie": "short-secret"}') == ["cookie"]
        assert (
            contains_credential_header_hint("headers={'Authorization': 'Bearer x'}")
            == ["authorization"]
        )
        assert contains_credential_header_hint(
            '{"Cookie": "a", "Authorization": "b"}'
        ) == ["authorization", "cookie"]
        # JSON-encoded artifacts escape the quotes; detection must survive.
        assert contains_credential_header_hint('{\\"Cookie\\": \\"sid\\"}') == ["cookie"]

    def test_plain_credential_header_hint_detected(self):
        assert contains_credential_header_hint("Authorization: Bearer abc") == ["authorization"]
        assert contains_credential_header_hint("Cookie: sid=xyz") == ["cookie"]

    def test_credential_header_hint_no_false_positives(self):
        assert contains_credential_header_hint("no credentials in this text") == []
        assert contains_credential_header_hint("cookie_type column value") == []
        assert contains_credential_header_hint("") == []


class TestRegistry:
    def test_endpoint_scope_matches_task_definition(self):
        specs = {spec.endpoint: spec for spec in ENDPOINT_SPECS}
        assert len(specs) == 24
        financial = sorted(s for s, spec in specs.items() if spec.category == CATEGORY_FINANCIAL)
        governance = sorted(s for s, spec in specs.items() if spec.category == CATEGORY_GOVERNANCE)
        market = sorted(s for s, spec in specs.items() if spec.category == CATEGORY_MARKET)
        assert financial == [
            "balancesheet",
            "cashflow",
            "dividend",
            "express",
            "fina_audit",
            "fina_indicator",
            "fina_mainbz",
            "forecast",
            "income",
        ]
        assert governance == [
            "pledge_detail",
            "pledge_stat",
            "repurchase",
            "share_float",
            "stk_holdernumber",
            "stk_holdertrade",
            "top10_floatholders",
            "top10_holders",
        ]
        assert market == [
            "adj_factor",
            "block_trade",
            "daily",
            "daily_basic",
            "margin",
            "moneyflow",
            "top_list",
        ]

    def test_excluded_list_covers_task_exclusions(self):
        excluded_text = json.dumps(EXCLUDED_ENDPOINTS, ensure_ascii=False)
        for keyword in ("top_inst", "VIP", "实时行情", "分钟行情", "新闻", "公告全文", "券商研报", "董秘"):
            assert keyword in excluded_text
        in_scope = {spec.endpoint for spec in ENDPOINT_SPECS}
        assert not any(item["endpoint"] in in_scope for item in EXCLUDED_ENDPOINTS)

    def test_param_kinds_produce_expected_params(self):
        specs = {spec.endpoint: spec for spec in ENDPOINT_SPECS}
        assert build_params(specs["income"], PROBE_CONTEXT) == {
            "ts_code": "603629.SH",
            "start_date": "20250101",
            "end_date": "20260814",
        }
        assert build_params(specs["daily"], PROBE_CONTEXT) == {
            "ts_code": "603629.SH",
            "start_date": "20260801",
            "end_date": "20260814",
        }
        assert build_params(specs["margin"], PROBE_CONTEXT) == {
            "trade_date": "20260814",
            "exchange": "SSE",
        }
        assert build_params(specs["top_list"], PROBE_CONTEXT) == {
            "ts_code": "603629.SH",
            "trade_date": "20260814",
        }
        # 001A-R1: repurchase must be a minimal single ts_code query, not a
        # full-market announcement-window fetch.
        repurchase = specs["repurchase"]
        assert repurchase.scope == "symbol"
        assert build_params(repurchase, PROBE_CONTEXT) == {"ts_code": "603629.SH"}

    def test_fallback_date_replaces_only_trade_date(self):
        updated = with_fallback_date({"ts_code": "603629.SH", "trade_date": "20260814"}, "20260813")
        assert updated == {"ts_code": "603629.SH", "trade_date": "20260813"}


class TestProbe:
    def test_has_data_record(self):
        spec = next(s for s in ENDPOINT_SPECS if s.endpoint == "income")
        record = probe_endpoint(
            lambda endpoint, **kw: _income_frame(), spec, PROBE_CONTEXT, token=TOKEN
        )
        assert record["state"] == STATE_HAS_DATA
        assert record["permission_status"] == PERMISSION_ALLOWED
        assert record["row_count"] == 2
        assert record["empty_vs_error"] == "data_returned"
        assert record["time_range"] == {"field": "ann_date", "min": "20250428", "max": "20260826"}
        payload = canonical_frame_payload(_income_frame())
        assert record["response_sha256"] == hashlib.sha256(payload.encode()).hexdigest()
        assert record["response_sha256_scope"] == "frame"
        assert record["attempts"] == 1
        assert record["error"] is None

    def test_normal_no_data_record(self):
        spec = next(s for s in ENDPOINT_SPECS if s.endpoint == "dividend")
        record = probe_endpoint(
            lambda endpoint, **kw: pd.DataFrame(columns=["ts_code", "end_date"]),
            spec,
            PROBE_CONTEXT,
            token=TOKEN,
        )
        assert record["state"] == STATE_NORMAL_NO_DATA
        assert record["permission_status"] == PERMISSION_ALLOWED
        assert record["empty_vs_error"] == "empty_frame_without_error"
        assert record["row_count"] == 0
        assert record["time_range"] is None
        assert record["response_sha256"] == payload_sha256("[]")

    def test_permission_denied_record_is_sanitized_and_never_retried(self):
        calls: list[str] = []

        def query(endpoint, **kwargs):
            calls.append(endpoint)
            raise Exception(f"抱歉，您没有访问该接口的权限 token={TOKEN}")

        spec = next(s for s in ENDPOINT_SPECS if s.endpoint == "margin")
        record = probe_endpoint(query, spec, PROBE_CONTEXT, token=TOKEN)
        assert record["state"] == STATE_PERMISSION_DENIED
        assert record["permission_status"] == PERMISSION_DENIED
        assert record["empty_vs_error"] == "permission_error_raised"
        assert TOKEN not in record["error"]
        assert record["attempts"] == 1
        assert calls == ["margin"]

    def test_rate_limited_record_is_never_retried(self):
        calls: list[str] = []

        def query(endpoint, **kwargs):
            calls.append(endpoint)
            raise Exception("抱歉，您每分钟最多访问该接口5次")

        spec = next(s for s in ENDPOINT_SPECS if s.endpoint == "margin")
        record = probe_endpoint(query, spec, PROBE_CONTEXT, token=TOKEN)
        assert record["state"] == STATE_RATE_LIMITED
        assert record["permission_status"] == PERMISSION_ALLOWED
        assert record["rate_limit"]["verified_in_run"] is True
        assert record["attempts"] == 1
        assert calls == ["margin"]

    def test_transient_failure_retries_once_with_reason(self):
        attempts: list[int] = []

        def query(endpoint, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise TimeoutError("Connection to api.tushare.pro timed out")
            return _income_frame()

        spec = next(s for s in ENDPOINT_SPECS if s.endpoint == "income")
        record = probe_endpoint(query, spec, PROBE_CONTEXT, token=TOKEN)
        assert record["state"] == STATE_HAS_DATA
        assert record["attempts"] == 2
        assert record["retry_reason"] == "transient_network_error_single_retry"
        assert record["first_attempt_state"] == STATE_QUERY_FAILED

    def test_persistent_failure_not_retried(self):
        calls: list[str] = []

        def query(endpoint, **kwargs):
            calls.append(endpoint)
            raise ValueError("unexpected payload shape")

        spec = next(s for s in ENDPOINT_SPECS if s.endpoint == "income")
        record = probe_endpoint(query, spec, PROBE_CONTEXT, token=TOKEN)
        assert record["state"] == STATE_QUERY_FAILED
        assert record["permission_status"] == PERMISSION_UNKNOWN
        assert record["empty_vs_error"] == "generic_error_raised"
        assert record["attempts"] == 1
        assert calls == ["income"]

    def test_date_bound_empty_retries_on_fallback_date(self):
        seen: list[dict] = []

        def query(endpoint, **kwargs):
            seen.append(dict(kwargs))
            if kwargs.get("trade_date") == "20260814":
                return pd.DataFrame(columns=["trade_date", "exchange"])
            return _frame([{"trade_date": "20260813", "exchange": "SSE", "rzye": 1.0}])

        spec = next(s for s in ENDPOINT_SPECS if s.endpoint == "margin")
        record = probe_endpoint(query, spec, PROBE_CONTEXT, token=TOKEN)
        assert record["state"] == STATE_HAS_DATA
        assert record["first_attempt_state"] == STATE_NORMAL_NO_DATA
        assert record["attempts"] == 2
        assert record["retry_reason"] == "probe_date_no_rows_retry_on_fallback_trading_date"
        assert seen[0]["trade_date"] == "20260814"
        assert seen[1]["trade_date"] == "20260813"

    def test_non_date_bound_empty_not_retried(self):
        calls: list[str] = []

        def query(endpoint, **kwargs):
            calls.append(endpoint)
            return pd.DataFrame()

        spec = next(s for s in ENDPOINT_SPECS if s.endpoint == "top10_holders")
        record = probe_endpoint(query, spec, PROBE_CONTEXT, token=TOKEN)
        assert record["state"] == STATE_NORMAL_NO_DATA
        assert record["attempts"] == 1
        assert calls == ["top10_holders"]

    def test_not_queried_record(self):
        spec = next(s for s in ENDPOINT_SPECS if s.endpoint == "income")
        record = not_queried_record(spec, PROBE_CONTEXT, "runner aborted before probe")
        assert record["state"] == STATE_NOT_QUERIED
        assert record["permission_status"] == PERMISSION_UNKNOWN
        assert record["attempts"] == 1

    def test_repurchase_probe_sends_single_ts_code_only(self):
        seen: list[dict] = []

        def query(endpoint, **kwargs):
            seen.append({"endpoint": endpoint, **kwargs})
            return _frame(
                [{"ts_code": "603629.SH", "ann_date": "20260810", "proc": "实施", "amount": 1.0}]
            )

        spec = next(s for s in ENDPOINT_SPECS if s.endpoint == "repurchase")
        record = probe_endpoint(query, spec, PROBE_CONTEXT, token=TOKEN)
        assert seen == [{"endpoint": "repurchase", "ts_code": "603629.SH"}]
        assert record["state"] == STATE_HAS_DATA
        assert record["scope"] == "symbol"
        assert record["row_count"] == 1
        assert record.get("row_cap_suspected") is not True


class TestCanonicalPayload:
    def test_nan_and_none_normalized(self):
        frame = _frame(
            [
                {"a": float("nan"), "b": None, "c": 1.5, "d": "20260814", "e": 3},
                {"a": 2.0, "b": "x", "c": float("inf"), "d": None, "e": True},
            ]
        )
        payload = json.loads(canonical_frame_payload(frame))
        assert payload[0]["a"] is None
        assert payload[0]["b"] is None
        assert payload[1]["c"] is None
        assert payload[0]["e"] == 3
        assert payload[1]["e"] is True

    def test_empty_frame_payload(self):
        assert canonical_frame_payload(pd.DataFrame()) == "[]"
        assert canonical_frame_payload(None) == "[]"


class TestMatrix:
    def _records(self):
        records = []
        for spec in ENDPOINT_SPECS:
            if spec.endpoint == "income":
                records.append(
                    probe_endpoint(
                        lambda endpoint, **kw: _income_frame(), spec, PROBE_CONTEXT, token=TOKEN
                    )
                )
            elif spec.endpoint == "margin":
                records.append(
                    probe_endpoint(
                        lambda endpoint, **kw: (_ for _ in ()).throw(
                            Exception(f"抱歉，您没有访问该接口的权限 {TOKEN}")
                        ),
                        spec,
                        PROBE_CONTEXT,
                        token=TOKEN,
                    )
                )
            else:
                records.append(
                    probe_endpoint(
                        lambda endpoint, **kw: pd.DataFrame(), spec, PROBE_CONTEXT, token=TOKEN
                    )
                )
        return records

    def test_matrix_summary_and_evidence(self):
        records = self._records()
        matrix = build_matrix(
            PROBE_CONTEXT,
            records,
            token_status=TOKEN_STATUS_HAS_KEY,
            state_evidence={
                STATE_PERMISSION_DENIED: {"fixture": "tests/fixtures/tushare_capability/permission_denied.json"},
                STATE_NORMAL_NO_DATA: {"fixture": "tests/fixtures/tushare_capability/normal_no_data.json"},
                STATE_QUERY_FAILED: {"fixture": "tests/fixtures/tushare_capability/query_failed.json"},
                STATE_RATE_LIMITED: {"fixture": "tests/fixtures/tushare_capability/rate_limited.json"},
            },
        )
        assert matrix["token_status"] == TOKEN_STATUS_HAS_KEY
        assert matrix["summary"]["probed_endpoints"] == 24
        assert matrix["summary"]["by_state"][STATE_HAS_DATA] == 1
        assert matrix["summary"]["by_state"][STATE_NORMAL_NO_DATA] == 22
        assert matrix["summary"]["by_state"][STATE_PERMISSION_DENIED] == 1
        assert matrix["summary"]["by_permission"][PERMISSION_DENIED] == 1
        evidence = matrix["state_evidence"]
        assert evidence[STATE_PERMISSION_DENIED]["observed_in_real_run"] is True
        assert evidence[STATE_PERMISSION_DENIED]["real_run_examples"] == ["margin"]
        assert evidence[STATE_RATE_LIMITED]["observed_in_real_run"] is False
        assert evidence[STATE_RATE_LIMITED]["real_run_examples"] == []
        assert evidence[STATE_QUERY_FAILED]["fixture"].endswith("query_failed.json")
        assert len(matrix["excluded"]) >= 8

    def test_matrix_json_has_no_token(self):
        matrix = build_matrix(PROBE_CONTEXT, self._records(), token_status=TOKEN_STATUS_HAS_KEY)
        assert TOKEN not in json.dumps(matrix, ensure_ascii=False)

    def test_matrix_records_token_source_and_env_override(self):
        matrix = build_matrix(
            PROBE_CONTEXT,
            self._records(),
            token_status=TOKEN_STATUS_HAS_KEY,
            token_source=TOKEN_SOURCE_DOTENV,
            token_env_override_applied=True,
        )
        assert matrix["schema_version"] == "1.1"
        assert matrix["token_source"] == TOKEN_SOURCE_DOTENV
        assert matrix["token_env_override_applied"] is True
        markdown = render_markdown(matrix)
        assert "Token 来源：`dotenv_file`" in markdown
        assert "已被显式 .env 值覆盖" in markdown
        default = build_matrix(PROBE_CONTEXT, self._records(), token_status=TOKEN_STATUS_HAS_KEY)
        assert default["token_source"] is None
        assert default["token_env_override_applied"] is None
        assert "Token 来源" not in render_markdown(default)

    def test_row_cap_annotation_is_scope_agnostic(self):
        records = self._records()
        for record in records:
            if record["endpoint"] == "income":
                record["row_count"] = 2000
        matrix = build_matrix(PROBE_CONTEXT, records, token_status=TOKEN_STATUS_HAS_KEY)
        annotate_row_cap_hits(matrix)
        income = next(r for r in matrix["endpoints"] if r["endpoint"] == "income")
        assert income["row_cap_suspected"] is True
        assert any("行返回上限" in note for note in matrix["notes"])

    def test_markdown_renders_without_secrets(self):
        matrix = build_matrix(PROBE_CONTEXT, self._records(), token_status=TOKEN_STATUS_HAS_KEY)
        markdown = render_markdown(matrix)
        assert "Tushare 2000 积分真实权限矩阵" in markdown
        assert "明确排除" in markdown
        assert "状态证据" in markdown
        assert TOKEN not in markdown
        for spec in ENDPOINT_SPECS:
            assert spec.endpoint in markdown


class TestFixtureEvidence:
    @pytest.mark.parametrize("fixture_name", sorted(path.name for path in FIXTURES.glob("*.json")))
    def test_fixture_classifies_to_expected_state(self, fixture_name):
        payload = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
        if payload.get("error_message"):
            assert classify_error_message(payload["error_message"]) == payload["expected_state"]
            assert "token" not in json.dumps(payload, ensure_ascii=False).lower()
        else:
            frame = pd.DataFrame(
                payload.get("frame_records", []),
                columns=payload.get("frame_columns", []),
            )
            assert frame.empty
            assert payload["expected_state"] == STATE_NORMAL_NO_DATA


def _load_audit_module():
    """Import the audit CLI as a module without invoking ``__main__``."""
    spec = importlib.util.spec_from_file_location("audit_tushare_capability", SCRIPT)
    assert spec and spec.loader, "unable to build spec for audit module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestScript:
    def test_dry_run_lists_plan_without_network(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dry-run",
                "--no-dotenv",
                "--output-dir",
                str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "TUSHARE_TOKEN": "dummy-token-for-dry-run"},
            cwd=str(ROOT),
        )
        assert result.returncode == 0
        assert "HAS_KEY" in result.stdout
        assert "token_source=inherited_environment" in result.stdout
        assert "603629.SH" in result.stdout
        assert result.stdout.count("category=") == 24
        assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())

    def test_missing_dotenv_rejects_inherited_env_token(self, tmp_path):
        # 001A-R1A final review P1#1: a typo'd --dotenv path must fail closed
        # instead of silently probing with the inherited environment token.
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dry-run",
                "--dotenv",
                str(tmp_path / "typo.env"),
                "--output-dir",
                str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            env={
                "PATH": "/usr/bin:/bin",
                "TUSHARE_TOKEN": "inherited-token-must-not-be-used",
            },
            cwd=str(ROOT),
        )
        assert result.returncode == 2
        assert "NO_KEY" in result.stdout
        assert "--no-dotenv" in result.stdout
        assert "inherited-token-must-not-be-used" not in result.stdout
        assert not (tmp_path / "out").exists()

    def test_no_key_fails_closed_without_probes(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--no-dotenv", "--output-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
            cwd=str(ROOT),
        )
        assert result.returncode == 2
        assert "NO_KEY" in result.stdout
        assert not any(tmp_path.iterdir())


class TestScriptRepairsR1:
    """001A-R1: token source attribution, BJ exchange, display path, leak fail-closed."""

    def test_dotenv_token_overrides_inherited_env(self, tmp_path):
        dotenv_file = tmp_path / "custom.env"
        dotenv_file.write_text("TUSHARE_TOKEN=token-from-dotenv\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dry-run",
                "--dotenv",
                str(dotenv_file),
                "--output-dir",
                str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "TUSHARE_TOKEN": "token-from-inherited-env"},
            cwd=str(ROOT),
        )
        assert result.returncode == 0
        assert "token_source=dotenv_file" in result.stdout
        assert "env_override_applied=true" in result.stdout
        assert "token-from-dotenv" not in result.stdout
        assert "token-from-inherited-env" not in result.stdout

    def test_resolve_token_precedence_matrix(self, tmp_path):
        audit = _load_audit_module()
        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text("TUSHARE_TOKEN=dotenv-token\n", encoding="utf-8")
        resolved = audit.resolve_token(
            environ={"TUSHARE_TOKEN": "env-token"}, dotenv_path=dotenv_file
        )
        assert resolved == ("dotenv-token", TOKEN_SOURCE_DOTENV, True)
        same = audit.resolve_token(
            environ={"TUSHARE_TOKEN": "dotenv-token"}, dotenv_path=dotenv_file
        )
        assert same == ("dotenv-token", TOKEN_SOURCE_DOTENV, False)
        inherited = audit.resolve_token(
            environ={"TUSHARE_TOKEN": "env-token"}, dotenv_path=dotenv_file, allow_dotenv=False
        )
        assert inherited == ("env-token", TOKEN_SOURCE_INHERITED_ENV, False)
        empty_dotenv = tmp_path / "empty.env"
        empty_dotenv.write_text("OTHER=1\n", encoding="utf-8")
        # 001A-R1A final review P1#1: token-less .env + inherited env token
        # must fail closed instead of falling back to the inherited token.
        rejected = audit.resolve_token(
            environ={"TUSHARE_TOKEN": "env-token"}, dotenv_path=empty_dotenv
        )
        assert rejected == ("", "", False)
        missing = audit.resolve_token(environ={}, dotenv_path=tmp_path / "absent.env")
        assert missing == ("", "", False)

    def test_exchange_mapping_covers_bj_and_fails_closed_on_unknown(self):
        audit = _load_audit_module()
        assert audit.exchange_for_ts_code("603629.SH") == "SSE"
        assert audit.exchange_for_ts_code("000001.SZ") == "SZSE"
        # 001A-R1B round2 review P2: the repository/Tushare identifier for the
        # Beijing Stock Exchange is BSE (matches instrument_identity.infer_exchange).
        assert audit.exchange_for_ts_code("430047.BJ") == "BSE"
        with pytest.raises(ValueError):
            audit.exchange_for_ts_code("603629.XX")

    def test_display_path_never_crashes_outside_root(self, tmp_path):
        audit = _load_audit_module()
        outside = tmp_path / "deep" / "archive"
        assert audit.display_path(outside) == str(outside)
        inside = audit.ROOT / "docs" / "task_runs" / "x"
        assert audit.display_path(inside) == str(Path("docs/task_runs/x"))

    def test_write_outputs_fail_closed_on_real_leak(self, tmp_path):
        audit = _load_audit_module()
        records = [
            not_queried_record(spec, PROBE_CONTEXT, "fixture")
            for spec in ENDPOINT_SPECS
        ]
        records[0]["error"] = f"sanitizer bypass leaked token {TOKEN}"
        matrix = build_matrix(PROBE_CONTEXT, records, token_status=TOKEN_STATUS_HAS_KEY)
        written, leaks = audit.write_outputs(matrix, tmp_path, token=TOKEN)
        assert written == []
        assert {name for name, _ in leaks} >= {"tushare_permission_matrix.json"}
        assert any(reason == "token_substring" for _, reason in leaks)
        assert not tmp_path.exists() or not any(tmp_path.iterdir())

    def test_scan_detects_serialized_credential_headers(self):
        audit = _load_audit_module()
        leaks = audit.scan_artifacts_for_secret(
            {
                "tushare_permission_matrix.json": (
                    'upstream rejected headers={"Cookie": "short-secret", '
                    '"Authorization": "Bearer xyz"}'
                )
            },
            "",
        )
        assert sorted(leaks) == [
            ("tushare_permission_matrix.json", "authorization_header"),
            ("tushare_permission_matrix.json", "cookie_header"),
        ]

    def test_scan_detects_plain_credential_headers(self):
        audit = _load_audit_module()
        assert audit.scan_artifacts_for_secret({"m": "Authorization: Bearer x"}, "") == [
            ("m", "authorization_header")
        ]
        assert audit.scan_artifacts_for_secret({"m": "Cookie: sid=1"}, "") == [
            ("m", "cookie_header")
        ]
        assert audit.scan_artifacts_for_secret({"m": "clean error text"}, "") == []

    def test_write_outputs_fail_closed_on_serialized_header_leak(self, tmp_path):
        # 001A-R1A final review P1#2: a serialized header dict surviving
        # sanitize_error_text must trip the pre-write scan (value too short
        # for token/blob rules, quote defeats the old substring check).
        audit = _load_audit_module()
        records = [
            not_queried_record(spec, PROBE_CONTEXT, "fixture")
            for spec in ENDPOINT_SPECS
        ]
        records[0]["error"] = 'probe failed headers={"Cookie": "sid-short"}'
        matrix = build_matrix(PROBE_CONTEXT, records, token_status=TOKEN_STATUS_HAS_KEY)
        written, leaks = audit.write_outputs(matrix, tmp_path, token="")
        assert written == []
        assert ("tushare_permission_matrix.json", "cookie_header") in leaks
        assert not tmp_path.exists() or not any(tmp_path.iterdir())

    def test_write_outputs_clean_matrix_writes_all_artifacts(self, tmp_path):
        audit = _load_audit_module()
        records = [
            not_queried_record(spec, PROBE_CONTEXT, "fixture")
            for spec in ENDPOINT_SPECS
        ]
        matrix = build_matrix(
            PROBE_CONTEXT,
            records,
            token_status=TOKEN_STATUS_HAS_KEY,
            token_source=TOKEN_SOURCE_DOTENV,
            token_env_override_applied=True,
        )
        written, leaks = audit.write_outputs(
            matrix, tmp_path, token=TOKEN, token_source=TOKEN_SOURCE_DOTENV, env_override_applied=True
        )
        assert leaks == []
        assert [path.name for path in written] == [
            "tushare_permission_matrix.json",
            "tushare_permission_matrix.md",
            "run-receipt.md",
        ]
        receipt = (tmp_path / "run-receipt.md").read_text(encoding="utf-8")
        assert "Token 来源" in receipt and "dotenv_file" in receipt
        assert "env_override_applied=true" in receipt
        assert "repurchase 单股查询记录" in receipt
        matrix_json = json.loads((tmp_path / "tushare_permission_matrix.json").read_text(encoding="utf-8"))
        repurchase = next(r for r in matrix_json["endpoints"] if r["endpoint"] == "repurchase")
        assert repurchase["params"] == {"ts_code": "603629.SH"}
        assert TOKEN not in receipt

    def test_main_returns_3_and_writes_nothing_when_probe_leaks_token(self, tmp_path, monkeypatch):
        audit = _load_audit_module()

        def fake_probe(query_fn, spec, probe_context, *, token="", now=None):
            record = not_queried_record(spec, probe_context, "fixture")
            if spec.endpoint == "income":
                record["error"] = f"dirty error containing {TOKEN}"
            return record

        monkeypatch.setattr(audit, "make_query_fn", lambda token: object())
        monkeypatch.setattr(audit, "probe_endpoint", fake_probe)
        out_dir = tmp_path / "custom-outside-root"
        rc = audit.main(
            ["--no-dotenv", "--sleep-seconds", "0", "--output-dir", str(out_dir)],
            environ={"PATH": "/usr/bin:/bin", "TUSHARE_TOKEN": TOKEN},
        )
        assert rc == 3
        assert not out_dir.exists()

    def test_main_success_writes_artifacts_to_custom_dir(self, tmp_path, monkeypatch):
        audit = _load_audit_module()

        def fake_probe(query_fn, spec, probe_context, *, token="", now=None):
            return not_queried_record(spec, probe_context, "fixture probe")

        monkeypatch.setattr(audit, "make_query_fn", lambda token: object())
        monkeypatch.setattr(audit, "probe_endpoint", fake_probe)
        out_dir = tmp_path / "custom-outside-root"
        rc = audit.main(
            ["--no-dotenv", "--sleep-seconds", "0", "--output-dir", str(out_dir)],
            environ={"PATH": "/usr/bin:/bin", "TUSHARE_TOKEN": TOKEN},
        )
        assert rc == 0
        assert (out_dir / "tushare_permission_matrix.json").is_file()
        assert (out_dir / "run-receipt.md").is_file()


class TestScriptRepairsR1B:
    """001A-R1B (round2 review): dotenv interpolation provenance bypass and BSE mapping."""

    def test_resolve_token_rejects_non_literal_dotenv_token(self, tmp_path):
        # 001A-R1B round2 P1: TUSHARE_TOKEN=${FOREIGN_TOKEN} in .env must not
        # be interpolated from the process environment; non-literal values are
        # rejected fail-closed instead of being attributed to dotenv_file.
        audit = _load_audit_module()
        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text(
            "TUSHARE_TOKEN=${FOREIGN_TOKEN}\n", encoding="utf-8"
        )
        with pytest.raises(audit.TokenNotLiteralError):
            audit.resolve_token(
                environ={"FOREIGN_TOKEN": "foreign-secret-token"},
                dotenv_path=dotenv_file,
            )

    def test_interpolated_dotenv_token_never_attributed_to_dotenv_file(self, tmp_path):
        # Defense-in-depth companion: even if the rejection guard were removed,
        # dotenv parsing itself must stay non-interpolating so the raw value
        # never silently resolves to the inherited environment value.
        audit = _load_audit_module()
        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text(
            "TUSHARE_TOKEN=${FOREIGN_TOKEN}\n", encoding="utf-8"
        )
        with pytest.raises(audit.TokenNotLiteralError):
            audit.resolve_token(
                environ={},
                dotenv_path=dotenv_file,
            )
        from dotenv import dotenv_values

        raw = dotenv_values(dotenv_file, interpolate=False)
        assert raw.get("TUSHARE_TOKEN") == "${FOREIGN_TOKEN}"

    def test_cli_interpolated_dotenv_token_fails_closed(self, tmp_path):
        dotenv_file = tmp_path / "custom.env"
        dotenv_file.write_text(
            "TUSHARE_TOKEN=${FOREIGN_TOKEN}\n", encoding="utf-8"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dry-run",
                "--dotenv",
                str(dotenv_file),
                "--output-dir",
                str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            env={
                "PATH": "/usr/bin:/bin",
                "FOREIGN_TOKEN": "foreign-secret-token",
            },
            cwd=str(ROOT),
        )
        assert result.returncode == 2
        assert "non-literal" in result.stdout
        assert "foreign-secret-token" not in result.stdout
        assert "token_source=dotenv_file" not in result.stdout
        assert not (tmp_path / "out").exists()

    def test_cli_literal_dotenv_token_still_resolves(self, tmp_path):
        # No regression: plain literal tokens in .env keep working after the
        # switch to dotenv_values(interpolate=False).
        dotenv_file = tmp_path / "custom.env"
        dotenv_file.write_text(
            "TUSHARE_TOKEN=plain-literal-token\n", encoding="utf-8"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dry-run",
                "--dotenv",
                str(dotenv_file),
                "--output-dir",
                str(tmp_path / "out"),
            ],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
            cwd=str(ROOT),
        )
        assert result.returncode == 0
        assert "token_source=dotenv_file" in result.stdout
        assert "plain-literal-token" not in result.stdout

    def test_margin_probe_context_uses_bse_for_bj_symbol(self):
        # 001A-R1B round2 P2: a .BJ audit must probe margin with the
        # repository/Tushare exchange identifier BSE, not the invalid BJSE.
        audit = _load_audit_module()
        ts_code = audit.normalize_symbol("430047.BJ")
        assert audit.exchange_for_ts_code(ts_code) == "BSE"
