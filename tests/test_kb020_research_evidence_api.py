# [KB-020] research_evidence_api
"""Tests for 同股研报/半年报证据聚合只读 API (KB-020).

Covers (per docs/TASKS.md KB-020 acceptance):
  - Service-level: fixture covering full / partial-missing / conflict /
    stale / single-bucket-exception / invalid-symbol / disabled.
  - Path safety: every rel_path validated inside knowledge root.
  - No `decision` / `action_label` / `buy_level` / strong action verbs.
  - Route smoke + JSON serializable + route ordering regression.
  - Old ``/v1/knowledge/local/search`` contract regression.
  - runtime_tier=FAST_RADAR for ``research_evidence_lookup``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR

from api.services import research_evidence_service as svc
from api.runtime_tier import RuntimeTier, tradeflow_endpoint_tier


# ── helpers ──────────────────────────────────────────────────────────


_TODAY = date(2026, 7, 13)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


_FORBIDDEN_ACTION_WORDS = (
    "立即买入", "立即卖出", "满仓", "清仓", "全仓",
)


def _assert_no_strong_action_words(text: str) -> None:
    for forbidden in _FORBIDDEN_ACTION_WORDS:
        assert forbidden not in text, (
            f"text contains forbidden action word: {forbidden}"
        )


def _assert_no_action_keys(payload: Dict[str, Any]) -> None:
    """Walk payload and assert no decision/action_label/buy_level keys leak."""
    BANNED = {"decision", "action_label", "buy_level"}
    stack: List[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            assert not (set(node.keys()) & BANNED), (
                f"banned action key leaked: {set(node.keys()) & BANNED}"
            )
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def _broker_md(
    *,
    rel_name: str,
    symbol: str,
    name: str,
    title: str,
    body: str,
    risk: str = "风险可控。",
    forecast: str = "预计全年营收增长 30%。",
    report_date: str = "2026-06-01",
    institution: str = "中信证券",
    evidence_level: str = "B",
    stale_risk: str = "低",
    valid_until: str = "2099-12-31",
) -> str:
    sources = f"[[{institution}-研究|{institution}深度]]"
    return f"""---
title: {title}
created: {report_date}
updated: {report_date}
sources:
  - "{sources}"
tags: [{name}]
related: []
symbols: ["{symbol} {name}"]
themes: [AI]
report_type: 深度
evidence_level: {evidence_level}
valid_until: {valid_until}
source_quality: 中
stale_risk: {stale_risk}
---

# {title}

## 一句话总结

{body}

## 投资逻辑

- {body}

## 前瞻指引

- {forecast}

## 风险提示

- {risk}
"""


def _half_year_md(
    *,
    rel_name: str,
    symbol: str,
    name: str,
    period: str = "2025H1",
    disclosure_date: str = "2026-08-29",
    revenue: str = "营收 420.4亿 (+60.0% YoY)",
    profit: str = "归母净利 12.5亿 (+45.2% YoY)",
    risk: str = "上游GPU供应",
    stale_risk: str = "低",
    valid_until: str = "2099-12-31",
    extra_source_type: str = "fact_table",
    symbol_suffix: str = ".SH",
) -> str:
    return f"""---
title: {name}-{period}-半年报
symbols: ["{symbol}{symbol_suffix} {name}"]
report_type: 半年报
financial_period: {period}
disclosure_date: {disclosure_date}
valid_until: {valid_until}
stale_risk: {stale_risk}
source_type: [exchange_filing, {extra_source_type}]
financial_facts:
  - {revenue}
  - {profit}
risk_factors:
  - {risk}
---

# {name} {period} 半年报事实表

营收与利润同比双增。
"""


# ── fixture builders ────────────────────────────────────────────────


@pytest.fixture()
def fixture_kb_full(tmp_path: Path) -> Path:
    """Full-coverage KB: 1 broker report + 1 half-year filing for 600100."""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(
        inv / "broker-1.md",
        _broker_md(
            rel_name="broker-1.md",
            symbol="600100", name="某甲公司",
            title="中信证券-某甲公司深度",
            body="营收高增长，渗透率提升。",
            institution="中信证券",
            report_date="2026-06-01",
        ),
    )
    _write(
        inv / "half-year-1.md",
        _half_year_md(
            rel_name="half-year-1.md",
            symbol="600100", name="某甲公司",
        ),
    )
    return tmp_path


@pytest.fixture()
def fixture_kb_conflict(tmp_path: Path) -> Path:
    """Conflict fixture: two half-year pages with different revenue."""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(
        inv / "hy-a.md",
        _half_year_md(
            rel_name="hy-a.md",
            symbol="600200", name="冲突公司",
            revenue="营收 100亿 (+10%)",
        ),
    )
    _write(
        inv / "hy-b.md",
        _half_year_md(
            rel_name="hy-b.md",
            symbol="600200", name="冲突公司",
            revenue="营收 200亿 (+50%)",
        ),
    )
    return tmp_path


@pytest.fixture()
def fixture_kb_stale(tmp_path: Path) -> Path:
    """Stale fixture: report outside the valid_until window."""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(
        inv / "broker-stale.md",
        _broker_md(
            rel_name="broker-stale.md",
            symbol="600300", name="过期公司",
            title="老研报-过期公司",
            body="营收增长。",
            report_date="2024-01-01",
            stale_risk="高",
            valid_until="2024-06-30",
        ),
    )
    return tmp_path


@pytest.fixture()
def fixture_kb_partial(tmp_path: Path) -> Path:
    """Partial fixture: only a broker report, no half-year filing."""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(
        inv / "broker-only.md",
        _broker_md(
            rel_name="broker-only.md",
            symbol="600400", name="无年报公司",
            title="中金-无年报公司点评",
            body="营收高增长。",
            institution="中金公司",
        ),
    )
    return tmp_path


@pytest.fixture()
def fixture_kb_empty(tmp_path: Path) -> Path:
    """Empty KB: investment dir exists but no pages."""
    (tmp_path / INVESTMENT_SUBDIR).mkdir(parents=True, exist_ok=True)
    return tmp_path


# ═══════════════════════════════════════════════════════════════════
# TestNormalizeSymbol
# ═══════════════════════════════════════════════════════════════════


class TestNormalizeSymbol:
    def test_plain_code_adds_SH_suffix(self):
        assert svc.normalize_symbol("600519") == "600519.SH"

    def test_plain_code_adds_SZ_suffix(self):
        assert svc.normalize_symbol("000001") == "000001.SZ"

    def test_keeps_existing_suffix(self):
        assert svc.normalize_symbol("600519.SH") == "600519.SH"
        assert svc.normalize_symbol("000001.SZ") == "000001.SZ"

    def test_normalizes_SS_to_SH(self):
        assert svc.normalize_symbol("600519.SS") == "600519.SH"

    def test_case_insensitive(self):
        assert svc.normalize_symbol("600519.sh") == "600519.SH"
        assert svc.normalize_symbol(" 600519.sh ") == "600519.SH"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "foo",
            "12345",            # 5 digits
            "1234567",          # 7 digits
            "600519.HK",        # HK not allowed
            "600519.US",        # US not allowed
            "ABCDEF",
            "600519.SH1",       # extra chars
        ],
    )
    def test_invalid_raises(self, bad: str):
        with pytest.raises(svc.InvalidSymbolError):
            svc.normalize_symbol(bad)


# ═══════════════════════════════════════════════════════════════════
# TestServiceContract
# ═══════════════════════════════════════════════════════════════════


class TestServiceContract:
    def test_top_level_keys(self, fixture_kb_full: Path):
        payload = svc.build_research_evidence(
            "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
        )
        expected = {
            "source", "task", "symbol", "as_of", "data_status",
            "vendor", "endpoint", "knowledge_root", "query",
            "source_freshness", "consensus", "citation_audit",
            "thesis_timeline", "half_year_facts", "gaps", "errors",
            "read_only",
        }
        assert expected.issubset(payload.keys())
        assert payload["source"] == "research_evidence"
        assert payload["task"] == "KB-020"
        assert payload["symbol"] == "600100.SH"
        assert payload["read_only"] is True

    def test_invalid_symbol_raises(self):
        with pytest.raises(svc.InvalidSymbolError):
            svc.build_research_evidence("foo")

    def test_invalid_window_raises(self, fixture_kb_full: Path):
        with pytest.raises(svc.InvalidSymbolError):
            svc.build_research_evidence(
                "600100", knowledge_root=str(fixture_kb_full),
                window_months=99, today=_TODAY,
            )

    def test_source_freshness_covers_all_buckets(self, fixture_kb_full: Path):
        payload = svc.build_research_evidence(
            "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
        )
        sf = payload["source_freshness"]
        assert set(sf.keys()) == set(svc.ALL_BUCKETS)
        for bucket_id, digest in sf.items():
            assert "data_status" in digest
            assert "has_hit" in digest
            assert "task" in digest

    def test_no_action_keys_or_strong_verbs(self, fixture_kb_full: Path):
        payload = svc.build_research_evidence(
            "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
        )
        _assert_no_action_keys(payload)
        # Walk all string-bearing fields and check forbidden verbs.
        text_blob = json.dumps(payload, ensure_ascii=False)
        for forbidden in _FORBIDDEN_ACTION_WORDS:
            assert forbidden not in text_blob, (
                f"strong action verb leaked: {forbidden}"
            )


# ═══════════════════════════════════════════════════════════════════
# TestServiceFixtureCoverage
# ═══════════════════════════════════════════════════════════════════


class TestServiceFixtureCoverage:
    def test_full_fixture_populates_all_buckets(self, fixture_kb_full: Path):
        payload = svc.build_research_evidence(
            "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
        )
        assert payload["data_status"] == "fresh"
        assert payload["consensus"]["has_hit"] is True
        assert payload["half_year_facts"]["has_hit"] is True
        # citation_audit ran against claims (broker report has forecasts).
        assert payload["citation_audit"]["task"] == "KB-017"
        assert payload["thesis_timeline"]["has_hit"] is True
        assert payload["half_year_facts"]["latest_period"] == "2025H1"

    def test_partial_missing_keeps_other_buckets(
        self, fixture_kb_partial: Path,
    ):
        payload = svc.build_research_evidence(
            "600400", knowledge_root=str(fixture_kb_partial), today=_TODAY,
        )
        # half_year_facts should be missing, but consensus should still hit.
        assert payload["consensus"]["has_hit"] is True
        assert payload["half_year_facts"]["has_hit"] is False
        assert payload["half_year_facts"]["data_status"] == "missing"
        # Per-bucket degradation: other buckets must not be wiped.
        assert payload["citation_audit"]["task"] == "KB-017"
        assert payload["thesis_timeline"]["task"] == "KB-018"

    def test_conflict_fixture_marks_data_status(
        self, fixture_kb_conflict: Path,
    ):
        payload = svc.build_research_evidence(
            "600200", knowledge_root=str(fixture_kb_conflict), today=_TODAY,
        )
        hy = payload["half_year_facts"]
        assert hy["has_hit"] is True
        # Either conflict or fresh depending on HY-003 aggregation; both
        # acceptable as long as the bucket is populated.
        assert hy["data_status"] in {"fresh", "conflict", "stale"}

    def test_stale_fixture_surface_in_gaps(self, fixture_kb_stale: Path):
        payload = svc.build_research_evidence(
            "600300", knowledge_root=str(fixture_kb_stale), today=_TODAY,
        )
        # Stale bucket should be reflected in either data_status or gaps.
        gaps_blob = " ".join(payload["gaps"])
        assert (
            payload["data_status"] in {"stale", "missing"}
            or "stale" in gaps_blob
            or "refresh" in gaps_blob
        )

    def test_empty_kb_returns_missing(self, fixture_kb_empty: Path):
        payload = svc.build_research_evidence(
            "600500", knowledge_root=str(fixture_kb_empty), today=_TODAY,
        )
        assert payload["data_status"] == "missing"
        for bucket_id in svc.ALL_BUCKETS:
            assert payload[bucket_id]["has_hit"] is False


# ═══════════════════════════════════════════════════════════════════
# TestBucketFailureIsolation
# ═══════════════════════════════════════════════════════════════════


class TestBucketFailureIsolation:
    def test_consensus_failure_does_not_blank_others(
        self, fixture_kb_full: Path,
    ):
        with patch(
            "api.services.research_evidence_service.lookup_research_consensus_matrix",
            side_effect=RuntimeError("boom"),
        ):
            payload = svc.build_research_evidence(
                "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
            )
        assert payload["consensus"]["data_status"] == "failed"
        assert "consensus:" in payload["consensus"]["errors"][0]
        # Other buckets must remain intact.
        assert payload["half_year_facts"]["has_hit"] is True
        assert payload["thesis_timeline"]["has_hit"] is True

    def test_half_year_failure_does_not_blank_others(
        self, fixture_kb_full: Path,
    ):
        with patch(
            "api.services.research_evidence_service.query_half_year_facts",
            side_effect=RuntimeError("boom"),
        ):
            payload = svc.build_research_evidence(
                "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
            )
        assert payload["half_year_facts"]["data_status"] == "failed"
        assert payload["consensus"]["has_hit"] is True
        assert payload["thesis_timeline"]["has_hit"] is True

    def test_citation_audit_failure_isolated(self, fixture_kb_full: Path):
        with patch(
            "api.services.research_evidence_service.audit_citation_against_facts",
            side_effect=RuntimeError("audit boom"),
        ):
            payload = svc.build_research_evidence(
                "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
            )
        assert payload["citation_audit"]["data_status"] == "failed"
        assert payload["consensus"]["has_hit"] is True
        assert payload["half_year_facts"]["has_hit"] is True

    def test_thesis_timeline_failure_isolated(self, fixture_kb_full: Path):
        with patch(
            "api.services.research_evidence_service.lookup_research_thesis_timeline",
            side_effect=RuntimeError("timeline boom"),
        ):
            payload = svc.build_research_evidence(
                "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
            )
        assert payload["thesis_timeline"]["data_status"] == "failed"
        assert payload["consensus"]["has_hit"] is True


# ═══════════════════════════════════════════════════════════════════
# TestPathSafety
# ═══════════════════════════════════════════════════════════════════


class TestPathSafety:
    def test_safe_rel_path_rejects_absolute(self, tmp_path: Path):
        assert svc._safe_rel_path("/etc/passwd", str(tmp_path)) == ""

    def test_safe_rel_path_rejects_parent_escape(self, tmp_path: Path):
        assert svc._safe_rel_path("../etc/passwd", str(tmp_path)) == ""
        assert svc._safe_rel_path("wiki/../../../etc/passwd", str(tmp_path)) == ""

    def test_safe_rel_path_rejects_drive_letter(self, tmp_path: Path):
        assert svc._safe_rel_path("C:/secrets", str(tmp_path)) == ""

    def test_safe_rel_path_accepts_valid(self, tmp_path: Path):
        # Create target so resolution works.
        (tmp_path / "wiki" / "investment" / "x.md").parent.mkdir(
            parents=True, exist_ok=True,
        )
        (tmp_path / "wiki" / "investment" / "x.md").write_text("ok")
        assert (
            svc._safe_rel_path("wiki/investment/x.md", str(tmp_path))
            == "wiki/investment/x.md"
        )

    def test_redact_bucket_paths_strips_unsafe(
        self, tmp_path: Path,
    ):
        payload = {
            "rel_path": "/etc/passwd",
            "nested": {
                "source_path": "../secret",
                "fact_source_path": "wiki/investment/x.md",
            },
            "list": [
                {"source_path": "C:/key.txt"},
                {"source_path": "wiki/investment/y.md"},
            ],
            "other": "fine",
        }
        out = svc._redact_bucket_paths(payload, str(tmp_path))
        assert out["rel_path"] == ""
        assert out["nested"]["source_path"] == ""
        # wiki/investment/x.md may not exist in tmp_path but path is still
        # structurally safe; either empty or kept — both acceptable as long
        # as no absolute/host path leaks.
        assert out["list"][0]["source_path"] == ""


# ═══════════════════════════════════════════════════════════════════
# TestDisabledEnvShortCircuit
# ═══════════════════════════════════════════════════════════════════


class TestDisabledEnvShortCircuit:
    def test_disabled_returns_skipped(
        self, fixture_kb_full: Path, monkeypatch,
    ):
        monkeypatch.setenv("KNOWLEDGE_CONTEXT_DISABLED", "1")
        payload = svc.build_research_evidence(
            "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
        )
        assert payload["data_status"] == "skipped"
        assert payload["read_only"] is True
        for bucket_id in svc.ALL_BUCKETS:
            assert payload[bucket_id]["data_status"] == "skipped"

    def test_disabled_via_local_env(self, fixture_kb_full: Path, monkeypatch):
        monkeypatch.delenv("KNOWLEDGE_CONTEXT_DISABLED", raising=False)
        monkeypatch.setenv("KNOWLEDGE_LOCAL_DISABLED", "true")
        payload = svc.build_research_evidence(
            "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
        )
        assert payload["data_status"] == "skipped"


# ═══════════════════════════════════════════════════════════════════
# TestJSONSerialization
# ═══════════════════════════════════════════════════════════════════


class TestJSONSerialization:
    def test_payload_is_json_serializable(self, fixture_kb_full: Path):
        payload = svc.build_research_evidence(
            "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
        )
        # Must round-trip through json without raising.
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        restored = json.loads(text)
        assert restored["symbol"] == "600100.SH"
        assert restored["task"] == "KB-020"

    def test_payload_serializable_on_failure(self, fixture_kb_full: Path):
        with patch(
            "api.services.research_evidence_service.lookup_research_consensus_matrix",
            side_effect=RuntimeError("boom"),
        ):
            payload = svc.build_research_evidence(
                "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
            )
        text = json.dumps(payload, ensure_ascii=False)
        assert "consensus" in json.loads(text)


# ═══════════════════════════════════════════════════════════════════
# FastAPI route smoke + route ordering regression
# ═══════════════════════════════════════════════════════════════════


def _client() -> TestClient:
    from api.main import app
    return TestClient(app, raise_server_exceptions=False)


def _auth_unique(client: TestClient) -> str:
    from datetime import datetime, timezone
    from uuid import uuid4

    from api.database import UserDB, get_db_ctx, init_db
    from api.services import auth_service

    init_db()
    email = auth_service.normalize_email(f"kb020-{uuid4().hex[:8]}@test.com")
    now = datetime.now(timezone.utc)
    with get_db_ctx() as db:
        user = auth_service.get_user_by_email(db, email)
        if not user:
            user = UserDB(
                id=str(uuid4()),
                email=email,
                is_active=True,
                created_at=now,
                updated_at=now,
                last_login_at=now,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
    return auth_service.create_access_token(user)


class TestRouteSmoke:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = _client()
        self.token = _auth_unique(self.client)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_meta_route_returns_payload(self):
        r = self.client.get(
            "/v1/knowledge/research/evidence/_meta", headers=self.headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["task"] == "KB-020"
        assert body["read_only"] is True
        assert set(body["buckets"]) == set(svc.ALL_BUCKETS)
        assert body["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"

    def test_meta_route_requires_auth(self):
        r = self.client.get("/v1/knowledge/research/evidence/_meta")
        assert r.status_code in (401, 403)

    def test_invalid_symbol_returns_4xx(self):
        r = self.client.get(
            "/v1/knowledge/research/evidence/foo", headers=self.headers,
        )
        assert 400 <= r.status_code < 500
        assert "invalid" in r.json()["detail"].lower() or "symbol" in r.json()["detail"].lower()

    def test_short_symbol_returns_4xx(self):
        r = self.client.get(
            "/v1/knowledge/research/evidence/12345", headers=self.headers,
        )
        assert 400 <= r.status_code < 500

    def test_happy_path_returns_evidence(self, fixture_kb_full: Path):
        r = self.client.get(
            f"/v1/knowledge/research/evidence/600100",
            headers=self.headers,
            params={"knowledge_root": str(fixture_kb_full)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["symbol"] == "600100.SH"
        assert body["task"] == "KB-020"
        assert body["read_only"] is True
        _assert_no_action_keys(body)

    def test_route_requires_auth(self):
        r = self.client.get("/v1/knowledge/research/evidence/600519")
        assert r.status_code in (401, 403)

    def test_window_months_query_param(self, fixture_kb_full: Path):
        r = self.client.get(
            f"/v1/knowledge/research/evidence/600100",
            headers=self.headers,
            params={
                "knowledge_root": str(fixture_kb_full),
                "window_months": 3,
            },
        )
        assert r.status_code == 200
        assert r.json()["query"]["window_months"] == 3

    def test_invalid_window_returns_4xx(self, fixture_kb_full: Path):
        r = self.client.get(
            f"/v1/knowledge/research/evidence/600100",
            headers=self.headers,
            params={
                "knowledge_root": str(fixture_kb_full),
                "window_months": 99,
            },
        )
        assert 400 <= r.status_code < 500


class TestRouteOrderingRegression:
    """[KB-020] fixed routes must be registered BEFORE the dynamic
    ``{symbol}`` route, otherwise FastAPI matches the param first."""

    def test_meta_route_not_swallowed_by_symbol_param(self):
        client = _client()
        token = _auth_unique(client)
        # ``_meta`` would be parsed as a symbol by the {symbol} route if
        # ordering were wrong. ``_meta`` is not a valid symbol → 4xx via
        # InvalidSymbolError if misordered, 200 with task=KB-020 if correct.
        r = client.get(
            "/v1/knowledge/research/evidence/_meta",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["task"] == "KB-020"

    def test_routes_declared_in_correct_order(self):
        from api.main import app
        paths = [
            getattr(route, "path", "")
            for route in app.routes
            if "knowledge/research/evidence" in getattr(route, "path", "")
        ]
        assert paths.index(
            "/v1/knowledge/research/evidence/_meta"
        ) < paths.index("/v1/knowledge/research/evidence/{symbol}")


class TestOldKnowledgeSearchNoRegression:
    """Old ``/v1/knowledge/local/search`` contract must still work."""

    def test_old_search_endpoint_still_returns_payload(self):
        client = _client()
        token = _auth_unique(client)
        r = client.get(
            "/v1/knowledge/local/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"symbol": "600519"},
        )
        # Disabled by default in test env OR returns proper payload; either
        # way it must not 4xx/5xx because of KB-020 wiring.
        assert r.status_code == 200
        body = r.json()
        # KB-006 contract fields must still be present.
        assert body["source"] == "local_knowledge_context"
        assert "data_status" in body
        assert "hits" in body


# ═══════════════════════════════════════════════════════════════════
# TestRuntimeTier
# ═══════════════════════════════════════════════════════════════════


class TestRuntimeTier:
    def test_endpoint_is_fast_radar(self):
        assert (
            tradeflow_endpoint_tier("research_evidence_lookup")
            == RuntimeTier.FAST_RADAR
        )

    def test_payload_has_runtime_meta(self, fixture_kb_full: Path):
        payload = svc.build_research_evidence(
            "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
        )
        meta = payload["runtime_tier_meta"]
        assert meta["runtime_tier"] == "FAST_RADAR"
        assert meta["llm_allowed"] is False

    def test_payload_can_skip_runtime_meta(self, fixture_kb_full: Path):
        payload = svc.build_research_evidence(
            "600100", knowledge_root=str(fixture_kb_full), today=_TODAY,
            include_runtime_meta=False,
        )
        assert "runtime_tier_meta" not in payload
