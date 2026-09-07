"""TA-MF-01: market-facts contract v1 candidate tests.

Covers the frozen contract artifacts under docs/contracts/:

1. every positive shared fixture validates against its package schema;
2. every negative fixture (``invalid.*``) is rejected;
3. MANIFEST.json sha256 digests match the actual bytes;
4. schema enums stay in lockstep with the eight-state code contract;
5. semantic invariants that JSON Schema alone cannot express
   (availability vs item states, series alignment, as_of future filtering,
   credential-free fixtures).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from tests.market_facts import schema_lite
from tradingagents.dataflows.tushare_query_contract import (
    ALL_QUERY_STATES,
    QUERY_STATES,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "docs" / "contracts" / "schemas"
FIXTURE_DIR = SCHEMA_DIR / "fixtures"
CONTRACT_DOC = REPO_ROOT / "docs" / "contracts" / "market-facts-v1.md"

CONTRACT_VERSION = "1.0.0-candidate.1"
PACKAGE_SCHEMA_NAMES = {
    "trading_calendar": "trading-calendar.schema.json",
    "market_regime": "market-regime.schema.json",
    "strategy_inputs": "strategy-inputs.schema.json",
    "event_calendar": "event-calendar.schema.json",
    "company_facts": "company-facts.schema.json",
    "governance_risk": "governance-risk.schema.json",
}
FAIL_STATES = {"QUERY_FAILED", "PERMISSION_DENIED", "RATE_LIMITED", "NOT_QUERIED", "FIELD_MISSING"}


# ---------------------------------------------------------------------------
# load helpers (module scope cache)

def _manifest() -> dict:
    return json.loads((SCHEMA_DIR / "MANIFEST.json").read_text(encoding="utf-8"))


def _schemas() -> dict[str, dict]:
    cache = getattr(_schemas, "_cache", None)
    if cache is None:
        cache = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
        }
        _schemas._cache = cache
    return cache


def _fixtures() -> dict[str, dict]:
    cache = getattr(_fixtures, "_cache", None)
    if cache is None:
        cache = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(FIXTURE_DIR.glob("*.json"))
        }
        _fixtures._cache = cache
    return cache


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _manifest()


# ---------------------------------------------------------------------------
# 1+2. fixture schema validation

class TestFixtureSchemaValidation:
    def test_manifest_lists_every_schema_and_fixture(self, manifest):
        schema_files = {p.name for p in SCHEMA_DIR.glob("*.schema.json")}
        listed = {entry["file"] for entry in manifest["schemas"]}
        assert listed == schema_files
        fixture_files = {f"fixtures/{p.name}" for p in FIXTURE_DIR.glob("*.json")}
        listed_fixtures = {entry["file"] for entry in manifest["fixtures"]}
        assert listed_fixtures == fixture_files

    def test_all_schemas_declare_supported_subset_only(self):
        for name, schema in _schemas().items():
            with pytest.raises(schema_lite.UnsupportedSchema):
                broken = dict(schema)
                broken["notActuallySupported"] = {"format": "date-time"}
                schema_lite.validate({}, broken)
            assert schema["$schema"].startswith("http://json-schema.org/draft-07")

    @pytest.mark.parametrize("entry", [e for e in _manifest()["fixtures"] if e["expect_valid"]])
    def test_positive_fixtures_validate(self, entry):
        schema = _schemas()[entry["schema"]]
        body = json.loads((SCHEMA_DIR / entry["file"]).read_text(encoding="utf-8"))
        errors = schema_lite.validate(body, schema)
        assert not errors, f"{entry['file']}: {errors}"

    @pytest.mark.parametrize("entry", [e for e in _manifest()["fixtures"] if not e["expect_valid"]])
    def test_negative_fixtures_are_rejected(self, entry):
        schema = _schemas()[entry["schema"]]
        body = json.loads((SCHEMA_DIR / entry["file"]).read_text(encoding="utf-8"))
        errors = schema_lite.validate(body, schema)
        assert errors, f"{entry['file']} must be rejected but validated cleanly"

    def test_envelope_schema_accepts_every_positive_fixture_package_header(self):
        envelope_schema = _schemas()["market-facts-envelope.schema.json"]
        for name, body in _fixtures().items():
            if name.startswith("invalid."):
                continue
            errors = schema_lite.validate(body, envelope_schema)
            assert not errors, f"{name}: generic envelope mismatch: {errors[:3]}"

    def test_all_eight_states_covered_across_fixtures(self, manifest):
        seen: set[str] = set()
        for entry in manifest["fixtures"]:
            seen.update(entry["states_covered"])
        assert seen == set(QUERY_STATES)
        assert seen == ALL_QUERY_STATES


# ---------------------------------------------------------------------------
# 3. manifest digests

class TestManifestDigests:
    def test_schema_sha256_matches_bytes(self, manifest):
        for entry in manifest["schemas"]:
            payload = (SCHEMA_DIR / entry["file"]).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]

    def test_fixture_sha256_matches_bytes(self, manifest):
        for entry in manifest["fixtures"]:
            payload = (SCHEMA_DIR / entry["file"]).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]

    def test_manifest_pins_candidate_version(self, manifest):
        assert manifest["contract_version"] == CONTRACT_VERSION
        assert manifest["contract_status"] == "CANDIDATE"
        for name, schema in _schemas().items():
            assert schema["properties"]["schema_version"]["const"] == CONTRACT_VERSION, name


# ---------------------------------------------------------------------------
# 4. lockstep with code constants

class TestEnumLockstep:
    def test_schema_query_state_enum_matches_code_contract(self):
        for name, schema in _schemas().items():
            enum = schema["definitions"]["queryState"]["enum"]
            assert sorted(enum) == sorted(QUERY_STATES), name

    def test_package_schemas_pin_their_package(self):
        for package, filename in PACKAGE_SCHEMA_NAMES.items():
            schema = _schemas()[filename]
            assert schema["properties"]["package"]["const"] == package

    def test_contract_doc_declares_same_packages(self):
        text = CONTRACT_DOC.read_text(encoding="utf-8")
        for package in PACKAGE_SCHEMA_NAMES:
            assert package in text, package
        for state in QUERY_STATES:
            assert state in text, state


# ---------------------------------------------------------------------------
# 5. semantic invariants beyond schema

def _iter_endpoint_records(body: dict):
    for item in body["items"]:
        payload = item.get("payload")
        if isinstance(payload, dict) and "endpoints" in payload:
            for record in payload["endpoints"].values():
                yield item, record


class TestSemanticInvariants:
    @staticmethod
    def _states(body: dict) -> set[str]:
        states = {item["state"] for item in body["items"]}
        for _, record in _iter_endpoint_records(body):
            states.add(record["state"])
        return states

    def test_availability_matches_item_states(self, manifest):
        for entry in manifest["fixtures"]:
            if not entry["expect_valid"]:
                continue
            body = json.loads((SCHEMA_DIR / entry["file"]).read_text(encoding="utf-8"))
            states = self._states(body)
            has_usable = bool(states & {"HAS_DATA", "NORMAL_NO_DATA"})
            has_blocker = bool(states - {"HAS_DATA", "NORMAL_NO_DATA"}) or bool(body["gaps"])
            availability = body["availability"]
            if availability == "READY":
                assert not has_blocker and has_usable, entry["file"]
            elif availability == "PARTIAL":
                assert has_usable and has_blocker, entry["file"]
            else:
                assert availability == "UNAVAILABLE"
                assert not has_usable, entry["file"]

    def test_failed_items_carry_no_numbers_or_dates(self, manifest):
        for entry in manifest["fixtures"]:
            if not entry["expect_valid"]:
                continue
            body = json.loads((SCHEMA_DIR / entry["file"]).read_text(encoding="utf-8"))
            for item in body["items"]:
                if item["state"] in FAIL_STATES:
                    assert item["payload"] is None
                    for field in ("data_date", "ann_date", "actual_disclosure_date", "report_period"):
                        assert item[field] is None, f"{entry['file']}.{item['subject']}.{field}"

    def test_strategy_series_arrays_are_aligned(self):
        body = _fixtures()["strategy-inputs.insufficient-samples.json"]
        payload = body["items"][0]["payload"]
        series = payload["series"]
        lengths = {key: len(value) for key, value in series.items()}
        assert len(set(lengths.values())) == 1, lengths
        assert series["date"] == sorted(series["date"])
        assert payload["adjustment"] == "QFQ"

    def test_insufficient_sample_indicator_withholds_value(self):
        body = _fixtures()["strategy-inputs.insufficient-samples.json"]
        by_name = {ind["name"]: ind for ind in body["computation"]["indicators"]}
        ma20 = by_name["ma_20"]
        assert ma20["required_samples"] == 20 and ma20["actual_samples"] == 14
        assert ma20["value"] is None
        gap_codes = {gap["code"] for gap in body["gaps"]}
        assert "INSUFFICIENT_SAMPLES" in gap_codes

    def test_company_facts_as_of_filter_drops_undisclosed_rows(self):
        body = _fixtures()["company-facts.has-data.json"]
        income = body["items"][0]["payload"]["endpoints"]["income"]
        assert income["row_count"] == 3
        assert income["eligible_row_count"] == 2
        assert len(income["records"]) == 2
        ann_dates = {record["ann_date"] for record in income["records"]}
        assert "20261028" not in ann_dates

    def test_event_revision_chain_is_expressible(self):
        body = _fixtures()["event-calendar.has-data.json"]
        events = body["items"][0]["payload"]["events"]
        ids = {event["event_id"] for event in events}
        corrections = [event for event in events if event["revision_of"]]
        assert corrections
        for event in corrections:
            assert event["revision_of"] in ids

    def test_no_event_does_not_claim_no_event_exists(self):
        body = _fixtures()["event-calendar.normal-no-data.json"]
        text = json.dumps(body, ensure_ascii=False)
        assert "不证明无事件" in text

    def test_fixtures_are_credential_free(self, manifest):
        secret_pattern = re.compile(
            r"(?i)(sk-[a-z0-9]{8,}|api[_-]?key\s*[:=]|token\s*[:=]\s*['\"]?[a-z0-9]{16,}|"
            r"BEGIN (RSA|EC|OPENSSH) PRIVATE KEY)"
        )
        for entry in manifest["fixtures"]:
            text = (SCHEMA_DIR / entry["file"]).read_text(encoding="utf-8")
            assert not secret_pattern.search(text), entry["file"]
        for entry in manifest["schemas"]:
            text = (SCHEMA_DIR / entry["file"]).read_text(encoding="utf-8")
            assert not secret_pattern.search(text), entry["file"]

    def test_sanitized_errors_reference_no_upstream_credentials(self):
        body = _fixtures()["governance-risk.query-failed.json"]
        text = json.dumps(body, ensure_ascii=False)
        assert "sanitized" in text
        assert "600519" not in text  # different subject entirely
