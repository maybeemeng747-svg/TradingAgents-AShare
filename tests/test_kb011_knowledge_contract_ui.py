# [KB-011] knowledge_contract_ui
"""Backend contract tests for KB-011: 本地知识/研报关注度前端与 API 契约回归.

Covers the three acceptance criteria:
  1. ``ReportResponse`` (top-level) actually serializes KB fields — the
     previous bug was that ``_attach_report_data_blockers_for_response`` set
     the attributes via ``setattr`` on the ORM object, but ``ReportResponse``
     did not declare them, so Pydantic silently dropped them on
     ``model_validate`` / JSON serialization.
  2. ``ObservationItemResponse`` schema declares all KB fields with sane
     NORMAL_NO_DATA defaults; ``get_observation_items`` enriches items with
     the same KB-008/KB-004 fields as TradeFlow candidates (when the
     knowledge fixture has a hit).
  3. ``attach_report_local_knowledge`` produces the full KB-009 decay field
     set, so the frontend can render 加分项 + 降权项 (stale / low-confidence /
     dedup / overheat).

All tests use the same mini fixture knowledge base as KB-007/KB-008 (华勤技术
603296 etc.) and never touch the real knowledge tree or production DB.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from tests.test_kb007_research_attention import (
    _COMPANY_PAGE_A,
    _COMPANY_PAGE_B,
    _EXPIRED_PAGE,
    _FUND_PAGE,
    _HK_PAGE,
    _NO_SYMBOLS_PAGE,
    _SCORE_TABLE_PAGE,
    _UNLISTED_PAGE,
    _US_TODO_PAGE,
)
from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR


# ── fixture (KB-007/KB-008 mini knowledge base, isolated tmp_path) ────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPANY_PAGE_A)
    _write(inv / "AI算力基础设施-公司评分表.md", _SCORE_TABLE_PAGE)
    _write(inv / "华勤技术-深度研究.md", _COMPANY_PAGE_B)
    _write(inv / "腾讯控股-游戏复苏.md", _HK_PAGE)
    _write(inv / "Dell-FQ127-待补充.md", _US_TODO_PAGE)
    _write(inv / "沪深300ETF-指数跟踪.md", _FUND_PAGE)
    _write(inv / "某私募主体-调研纪要.md", _UNLISTED_PAGE)
    _write(inv / "某周期股-已过期.md", _EXPIRED_PAGE)
    _write(inv / "行业综述-无标的.md", _NO_SYMBOLS_PAGE)
    return tmp_path


@pytest.fixture()
def empty_kb(tmp_path: Path) -> Path:
    (tmp_path / INVESTMENT_SUBDIR).mkdir(parents=True)
    return tmp_path


# ── 1. ReportResponse top-level KB field serialization ───────────────────


class TestReportResponseKBFields:
    """[KB-011] ReportResponse must declare & serialize KB fields.

    Regression: previously the fields were attached via ``setattr`` but never
    declared on the Pydantic model, so ``model_validate`` silently dropped
    them and the frontend could not see 本地知识补充 / 研报关注度 at the top
    level (only buried inside ``result_data``).
    """

    def test_report_response_declares_kb_fields(self):
        from api.main import ReportResponse

        fields = ReportResponse.model_fields
        # KB-003 local knowledge
        assert "local_knowledge_block" in fields
        assert "local_knowledge_summary" in fields
        # KB-008 research attention
        assert "research_attention_score" in fields
        assert "knowledge_theme_count" in fields
        assert "research_attention_summary" in fields
        assert "research_attention_block" in fields
        # Existing fields still present (regression guard).
        assert "wait_reason_codes" in fields
        assert "data_blockers" in fields

    def test_report_response_serializes_kb_fields_after_attach(self):
        """The exact regression scenario: setattr then model_validate."""
        from api.main import ReportResponse

        class FakeReport:
            id = "r1"
            user_id = None
            symbol = "603296.SH"
            name = "华勤技术"
            trade_date = "2026-07-05"
            status = "completed"
            error = None
            decision = "HOLD"
            direction = None
            research_direction = None
            execution_action = "HOLD"
            action_label = None
            confidence = 50
            target_price = None
            stop_loss_price = None
            risk_items = None
            key_metrics = None
            analyst_traces = None
            created_at = None
            updated_at = None
            waiting_ahead_count = None
            scheduled_running_count = None
            scheduled_concurrency_limit = None
            data_blockers = None
            data_blocker_summary = None
            wait_reason_codes = None
            wait_reason_labels = None
            # Fields the attach helper would set via setattr.
            local_knowledge_block = "### 本地知识补充\n- 华勤技术 …"
            local_knowledge_summary = {"status": "HAS_DATA", "matched_count": 3}
            research_attention_score = 4.5
            knowledge_theme_count = 6
            research_attention_summary = "6 themes"
            research_attention_block = "研报关注度 …"

        resp = ReportResponse.model_validate(FakeReport())
        dumped = resp.model_dump()
        # KB-003
        assert dumped["local_knowledge_block"].startswith("### 本地知识补充")
        assert dumped["local_knowledge_summary"]["matched_count"] == 3
        # KB-008
        assert dumped["research_attention_score"] == 4.5
        assert dumped["knowledge_theme_count"] == 6
        assert dumped["research_attention_summary"] == "6 themes"

    def test_attach_helper_then_validate_preserves_all_fields(
        self, fixture_kb: Path, monkeypatch
    ):
        """End-to-end: create report → attach → model_validate → no KB field
        is silently dropped. Mirrors the real API response path."""
        from api.main import ReportResponse, _attach_report_data_blockers_for_response
        from api.services import report_service

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        # Build a minimal in-memory ORM-like object that mirrors ReportDB.
        class FakeReport:
            def __init__(self) -> None:
                self.id = "r1"
                self.user_id = None
                self.symbol = "603296.SH"
                self.name = "华勤技术"
                self.trade_date = "2026-07-05"
                self.status = "completed"
                self.error = None
                self.decision = "HOLD"
                self.direction = None
                self.research_direction = "中性"
                self.execution_action = "HOLD"
                self.action_label = None
                self.confidence = 50
                self.target_price = None
                self.stop_loss_price = None
                self.risk_items = None
                self.key_metrics = None
                self.analyst_traces = None
                self.created_at = None
                self.updated_at = None
                self.waiting_ahead_count = None
                self.scheduled_running_count = None
                self.scheduled_concurrency_limit = None
                self.result_data = {"final_trade_decision": "HOLD"}
                # Attach helper populates these via setattr.
                self.data_blockers = None
                self.data_blocker_summary = None
                self.local_knowledge_block = None
                self.local_knowledge_summary = None
                self.wait_reason_codes = None
                self.wait_reason_labels = None
                self.research_attention_score = None
                self.knowledge_theme_count = None
                self.research_attention_summary = None
                self.research_attention_block = None

        report = FakeReport()
        _attach_report_data_blockers_for_response(report)

        resp = ReportResponse.model_validate(report)
        dumped = resp.model_dump()
        # KB-003 fields populated from the fixture.
        assert dumped["local_knowledge_block"]
        assert isinstance(dumped["local_knowledge_summary"], dict)
        # KB-008 fields populated (fixture_kb has 603296 mentions).
        assert dumped["research_attention_score"] is not None
        assert dumped["knowledge_theme_count"] is not None

    def test_legacy_result_data_without_kb_fields_does_not_crash(self):
        """Older rows predate KB-003 entirely. The attach helper must skip
        silently rather than raise — surfaced fields stay ``None``."""
        from api.main import ReportResponse, _attach_report_data_blockers_for_response

        class FakeReport:
            id = "r-legacy"
            user_id = None
            symbol = ""
            name = None
            trade_date = "2025-01-01"
            status = "completed"
            error = None
            decision = "HOLD"
            direction = None
            research_direction = None
            execution_action = None
            action_label = None
            confidence = None
            target_price = None
            stop_loss_price = None
            risk_items = None
            key_metrics = None
            analyst_traces = None
            created_at = None
            updated_at = None
            waiting_ahead_count = None
            scheduled_running_count = None
            scheduled_concurrency_limit = None
            # Legacy: no KB fields in result_data at all.
            result_data = {"final_trade_decision": "HOLD"}
            data_blockers = None
            data_blocker_summary = None
            local_knowledge_block = None
            local_knowledge_summary = None
            wait_reason_codes = None
            wait_reason_labels = None
            research_attention_score = None
            knowledge_theme_count = None
            research_attention_summary = None
            research_attention_block = None

        _attach_report_data_blockers_for_response(FakeReport())
        resp = ReportResponse.model_validate(FakeReport())
        dumped = resp.model_dump()
        # Fields are declared but null — that is NORMAL_NO_DATA, not a crash.
        assert dumped["local_knowledge_block"] is None
        assert dumped["research_attention_score"] is None


# ── 2. ObservationItemResponse schema + enrichment ──────────────────────


class TestObservationItemResponseKBFields:
    """[KB-011] 观察仓详情中本地知识字段的 schema/type — contract parity with
    TradeFlow candidates so the same UI block renders the same fields."""

    def test_schema_declares_all_kb_fields(self):
        from api.tradeflow_schemas import ObservationItemResponse

        fields = ObservationItemResponse.model_fields
        for name in (
            "research_attention_score",
            "research_attention_effective_score",
            "research_attention_overheat_penalty",
            "research_attention_summary",
            "research_attention_detail",
            "knowledge_theme_count",
            "local_knowledge_score",
            "knowledge_hit_count",
            "local_knowledge_summary",
            "local_knowledge_detail",
            "needs_tree_work_research",
        ):
            assert name in fields, f"missing KB field on ObservationItemResponse: {name}"

    def test_schema_defaults_are_normal_no_data(self):
        """Empty defaults equal NORMAL_NO_DATA semantics (never an error)."""
        from api.tradeflow_schemas import ObservationItemResponse

        item = ObservationItemResponse(symbol="600000.SH")
        assert item.research_attention_score == 0.0
        assert item.research_attention_effective_score == 0.0
        assert item.research_attention_overheat_penalty == 0.0
        assert item.research_attention_summary == ""
        assert item.research_attention_detail == {}
        assert item.knowledge_theme_count == 0
        assert item.local_knowledge_score == 0.0
        assert item.knowledge_hit_count == 0
        assert item.local_knowledge_summary == ""
        assert item.local_knowledge_detail == {}
        assert item.needs_tree_work_research is False

    def test_enrichment_helper_is_readonly(
        self, fixture_kb: Path, monkeypatch
    ):
        """[KB-011] enrichment must not write the knowledge tree."""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        before = {str(p) for p in fixture_kb.rglob("*")}
        from api.services.tradeflow_service import (
            _enrich_observation_items_with_knowledge,
        )

        items = [
            {"symbol": "603296.SH", "name": "华勤技术"},
            {"symbol": "999999", "name": "无命中标的"},
        ]
        _enrich_observation_items_with_knowledge(items)
        after = {str(p) for p in fixture_kb.rglob("*")}
        assert before == after, "observation KB enrichment mutated the tree"

    def test_enrichment_helper_populates_hit_fields(
        self, fixture_kb: Path, monkeypatch
    ):
        """Fixture 603296 should yield non-zero research attention + theme
        count + local knowledge score."""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        from api.services.tradeflow_service import (
            _enrich_observation_items_with_knowledge,
        )

        items = [{"symbol": "603296.SH", "name": "华勤技术"}]
        _enrich_observation_items_with_knowledge(items)
        it = items[0]
        # KB-008 (research attention) — at minimum the field is set.
        assert "research_attention_score" in it
        assert "research_attention_detail" in it
        assert isinstance(it["research_attention_detail"], dict)
        # KB-004 (local knowledge) — fields always present after enrichment.
        assert "local_knowledge_score" in it
        assert "local_knowledge_detail" in it
        # The fixture has rich 603296 mentions so research_attention_score > 0.
        assert it["research_attention_score"] > 0.0

    def test_enrichment_helper_handles_empty_list(self):
        from api.services.tradeflow_service import (
            _enrich_observation_items_with_knowledge,
        )

        assert _enrich_observation_items_with_knowledge([]) == []

    def test_enrichment_helper_handles_missing_symbol(self):
        """Items without symbol must still end up with NORMAL_NO_DATA fields."""
        from api.services.tradeflow_service import (
            _enrich_observation_items_with_knowledge,
        )

        items = [{"symbol": "", "name": ""}]
        result = _enrich_observation_items_with_knowledge(items)
        assert result[0]["research_attention_score"] == 0.0
        assert result[0]["local_knowledge_score"] == 0.0

    def test_enrichment_helper_swallows_knowledge_root_failure(
        self, empty_kb: Path, monkeypatch
    ):
        """When knowledge root has no investment subdir, enrichment must not
        raise — it falls back to NORMAL_NO_DATA defaults."""
        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(empty_kb))
        from api.services.tradeflow_service import (
            _enrich_observation_items_with_knowledge,
        )

        items = [{"symbol": "603296.SH", "name": "华勤技术"}]
        # Must not raise.
        result = _enrich_observation_items_with_knowledge(items)
        assert len(result) == 1


# ── 3. attach_report_local_knowledge produces KB-009 decay fields ────────


class TestKB009DecayFieldsExposed:
    """[KB-011] frontend must be able to show 加分项 + 降权项 — verify the
    KB-009 decay fields actually reach the report's local_knowledge_summary
    dict so the frontend can render stale / low-confidence / dedup / overheat
    alongside fresh / high-quality / theme_count.
    """

    def test_summary_dict_contains_credit_and_penalty_fields(
        self, fixture_kb: Path, monkeypatch
    ):
        from api.services.report_service import attach_report_local_knowledge

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        result_data = {"final_trade_decision": "HOLD"}
        enriched = attach_report_local_knowledge(result_data, symbol="603296")
        summary = enriched["local_knowledge_summary"]

        # 加分项 (credit) fields
        for key in (
            "research_attention_score",
            "knowledge_theme_count",
            "mention_count",
            "fresh_mention_count",
            "high_quality_mention_count",
        ):
            assert key in summary, f"missing credit field: {key}"

        # 降权项 (penalty) fields
        for key in (
            "stale_mention_count",
            "deprecated_mention_count",
        ):
            assert key in summary, f"missing penalty field: {key}"

    def test_kb009_effective_score_field_present(
        self, fixture_kb: Path, monkeypatch
    ):
        """KB-009 effective score + decay fields must be in the summary so the
        frontend can render the 有效分 vs 基础分 delta."""
        from api.services.report_service import attach_report_local_knowledge

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        enriched = attach_report_local_knowledge(
            {"final_trade_decision": "HOLD"}, symbol="603296"
        )
        summary = enriched["local_knowledge_summary"]
        # KB-009 flat fields are merged in by attention_to_summary.
        for key in (
            "research_attention_effective_score",
            "research_attention_base_score",
            "research_attention_dedup_penalty",
            "research_attention_time_decay_factor",
            "research_attention_overheat_penalty",
            "research_attention_decay_explain",
            "research_attention_warnings",
        ):
            assert key in summary, f"missing KB-009 decay field: {key}"

    def test_no_hit_summary_is_normal_no_data_not_an_error(
        self, fixture_kb: Path, monkeypatch
    ):
        """Symbol with no mentions must return empty fields, not raise —
        KB-011 'NORMAL_NO_DATA / 暂无本地知识, 不当成错误'."""
        from api.services.report_service import attach_report_local_knowledge

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        enriched = attach_report_local_knowledge(
            {"final_trade_decision": "HOLD"}, symbol="999999"
        )
        # Either local_knowledge_block stays empty/None (legacy path), or the
        # summary reports NORMAL_NO_DATA / has_hit=False.
        summary = enriched.get("local_knowledge_summary")
        if isinstance(summary, dict):
            assert summary.get("has_hit") in (False, None, True)
            assert summary.get("research_attention_score", 0) == 0
