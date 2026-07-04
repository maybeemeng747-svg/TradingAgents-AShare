# [KB-006] local_knowledge_context_api
"""Tests for KB-006: 本地知识库查询 API 与 investment-controller 只读上下文接入.

Coverage:
    - Service ``search_local_knowledge``:
        * symbol / name / themes / tags query paths
        * slim output contract (no full page body, summary <=200 chars)
        * status mapping (HAS_DATA / NORMAL_NO_DATA / STALE / LOW_CONFIDENCE)
        * disable behaviour (env var + explicit kwarg)
        * knowledge root override + env precedence
        * FAILED root → data_status=failed, never raises
        * max_pages clamp + JSON serialisable
    - Service ``collect_local_knowledge_hits``:
        * per-symbol digest shape
        * theme query bucket
        * empty input → skipped
        * disable → skipped + empty items
    - IC context bucket ``local_knowledge_hits``:
        * always present with source/as_of/data_status
        * data_status in allowed set
        * degrades to skipped/missing without raising
        * carries holdings/observation/mandate symbol union
    - Controller hints ``research_review`` lane:
        * fresh hits → background_supplement
        * stale/low hits → needs_research_review
        * no strong action verb leaks
    - API ``GET /v1/knowledge/local/search`` smoke (FastAPI TestClient)
    - Cross-contract safety:
        * assert_no_strong_action_verbs
        * only wiki/investment partition is visible
        * read-only safety (no writes)
        * JSON-serialisable payload
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.tradeflow.candidate_engine import init_db

from api.services.local_knowledge_context_service import (
    ALLOWED_DATA_STATUSES,
    CONTEXT_SOURCE,
    DEFAULT_MAX_PAGES,
    DATA_STATUS_FAILED,
    DATA_STATUS_FRESH,
    DATA_STATUS_MISSING,
    DATA_STATUS_SKIPPED,
    DATA_STATUS_STALE,
    VENDOR,
    assert_no_strong_action_verbs as assert_kb006_no_strong_verbs,
    collect_local_knowledge_hits,
    is_local_knowledge_disabled,
    resolve_knowledge_root,
    search_local_knowledge,
)
from api.services.investment_controller_context import (
    assert_no_strong_action_verbs,
    get_investment_controller_context,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixture knowledge base (only under tmp_path — never touches real ~/Documents)
# ──────────────────────────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


_COMPANY_PAGE = """---
title: 华勤技术603296-超节点进入出货周期
created: 2026-05-25
updated: 2026-06-29
sources:
  - "[[../../raw/x.md|中邮证券-华勤技术超节点]]"
tags: [华勤技术, 超节点, AI服务器]
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器, 超节点, 液冷散热]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 华勤技术（603296）

## 一句话总结

华勤技术超节点进入出货周期，AI服务器增长强劲。

## 风险提示

- 需求不及预期
- 竞争加剧
"""

_STALE_PAGE = """---
title: 某周期股-已过期
created: 2026-04-01
updated: 2026-04-01
sources:
  - "旧研报"
tags: [周期]
symbols: ["600000.SH 浦发银行"]
themes: [银行]
report_type: 公司点评
evidence_level: A
valid_until: 2020-01-01
source_quality: 中
stale_risk: 高
---

# 某周期股

## 一句话总结

已过期的旧观点。

## 风险提示

- 已过期
"""

_LOW_CONF_PAGE = """---
title: 待补充页面
created: 2026-05-01
updated: 2026-05-01
sources: []
tags: [待补充]
symbols: ["000001.SZ 平安银行"]
themes: [银行]
report_type: 综述
evidence_level: C
valid_until: 长期
source_quality: 低
stale_risk: 高
---

# 待补充

⚠️ 此页面内容不完整

## 已知信息

- 推测方向
"""

# A page in a NON-investment partition — must NEVER surface via the API.
_PRIVATE_PAGE = """---
title: 私人笔记
created: 2026-05-01
updated: 2026-05-01
symbols: ["603296.SH 华勤技术"]
themes: [AI服务器]
---

# 我的私人投资想法

立即买入华勤技术，满仓干。
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """Mini Tree Work knowledge base (only in tmp_path)."""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "华勤技术603296-超节点.md", _COMPANY_PAGE)
    _write(inv / "某周期股-已过期.md", _STALE_PAGE)
    _write(inv / "待补充页面.md", _LOW_CONF_PAGE)
    # Place a private page OUTSIDE wiki/investment to verify partition isolation.
    _write(tmp_path / "inbox" / "私人笔记.md", _PRIVATE_PAGE)
    return tmp_path


@pytest.fixture()
def empty_kb(tmp_path: Path) -> Path:
    (tmp_path / INVESTMENT_SUBDIR).mkdir(parents=True)
    return tmp_path


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures for IC context (SQLA + tradeflow DB)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_tf_db(tmp_path):
    db_path = str(tmp_path / "test_kb006_tradeflow.db")
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


# ══════════════════════════════════════════════════════════════════════════════
# 1. Config helpers
# ══════════════════════════════════════════════════════════════════════════════


class TestConfigHelpers:
    def test_disable_explicit_true(self):
        assert is_local_knowledge_disabled(disabled=True) is True

    def test_disable_explicit_false_overrides_env(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_CONTEXT_DISABLED", "1")
        assert is_local_knowledge_disabled(disabled=False) is False

    def test_disable_env_truthy(self):
        for val in ("1", "true", "yes", "on", "Y", "T"):
            env = {"KNOWLEDGE_CONTEXT_DISABLED": val}
            assert is_local_knowledge_disabled(env=env) is True, val

    def test_disable_env_falsy(self):
        for val in ("0", "", "false", "no", "off", "maybe"):
            env = {"KNOWLEDGE_CONTEXT_DISABLED": val}
            assert is_local_knowledge_disabled(env=env) is False, val

    def test_disable_secondary_env_var(self):
        env = {"KNOWLEDGE_LOCAL_DISABLED": "1"}
        assert is_local_knowledge_disabled(env=env) is True

    def test_disable_no_env(self):
        assert is_local_knowledge_disabled(env={}) is False

    def test_resolve_root_explicit_wins(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_ROOT", "/env/path")
        assert resolve_knowledge_root(knowledge_root="/explicit") == "/explicit"

    def test_resolve_root_env_var(self):
        env = {"KNOWLEDGE_ROOT": "/env/path"}
        assert resolve_knowledge_root(env=env) == "/env/path"

    def test_resolve_root_secondary_env_var(self):
        env = {"AUTO_DEV_KNOWLEDGE_ROOT": "/auto/path"}
        assert resolve_knowledge_root(env=env) == "/auto/path"

    def test_resolve_root_falls_back_to_default(self, monkeypatch):
        # No env, no kwarg → falls back to ~/Documents/knowledge via KB-001.
        monkeypatch.delenv("KNOWLEDGE_ROOT", raising=False)
        monkeypatch.delenv("AUTO_DEV_KNOWLEDGE_ROOT", raising=False)
        root = resolve_knowledge_root()
        assert "knowledge" in root


# ══════════════════════════════════════════════════════════════════════════════
# 2. search_local_knowledge — happy paths
# ══════════════════════════════════════════════════════════════════════════════


class TestSearchHappyPath:
    def test_symbol_query_returns_hit(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert payload["source"] == CONTEXT_SOURCE
        assert payload["as_of"]
        assert payload["data_status"] == DATA_STATUS_FRESH
        assert payload["vendor"] == VENDOR
        assert payload["endpoint"] == "wiki/investment"
        assert payload["read_only"] is True
        assert payload["hit_count"] >= 1
        assert payload["has_fresh_hit"] is True
        assert payload["fresh_hit_count"] >= 1
        # KB-004 score digest present.
        score = payload["score"]
        assert "local_knowledge_score" in score
        assert score["has_hit"] is True
        # Hits carry slim fields.
        hit = payload["hits"][0]
        assert hit["rel_path"]
        assert hit["title"]
        assert "summary_snippet" in hit
        assert "is_stale" in hit
        assert "is_low_confidence" in hit
        assert "is_to_be_supplemented" in hit
        assert "confidence" in hit
        assert "updated_at" in hit

    def test_name_query_matches(self, fixture_kb):
        payload = search_local_knowledge(
            name="华勤技术",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert payload["hit_count"] >= 1
        assert any("华勤" in h["title"] for h in payload["hits"])

    def test_themes_query_matches(self, fixture_kb):
        payload = search_local_knowledge(
            themes=["AI服务器"],
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert payload["hit_count"] >= 1
        assert "AI服务器" in payload["themes"]

    def test_tags_query_matches(self, fixture_kb):
        payload = search_local_knowledge(
            tags=["超节点"],
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert payload["hit_count"] >= 1

    def test_query_carries_query_dict(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            themes=["AI服务器"],
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert payload["query"] == {"symbol": "603296", "themes": ["AI服务器"]}

    def test_runtime_meta_present_by_default(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
        )
        meta = payload["runtime_tier_meta"]
        assert meta["runtime_tier"] == "FAST_RADAR"
        assert meta["llm_allowed"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 3. search_local_knowledge — slim output / no full text
# ══════════════════════════════════════════════════════════════════════════════


class TestSlimOutputContract:
    def test_summary_snippet_capped(self, fixture_kb, monkeypatch):
        # Force a long summary by patching the underlying extractor result.
        from tradingagents.dataflows import local_knowledge_provider as prov

        long_text = "x" * 500
        orig_build = prov._build_match

        def _patched_build(rel_path, abs_path, page_audit, frontmatter, matched_by):
            match = orig_build(rel_path, abs_path, page_audit, frontmatter, matched_by)
            match.summary = long_text
            return match

        monkeypatch.setattr(prov, "_build_match", _patched_build)
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        for hit in payload["hits"]:
            assert len(hit["summary_snippet"]) <= 203  # 200 + ellipsis

    def test_no_full_body_field_in_hits(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        for hit in payload["hits"]:
            # Forbidden: any field that could carry the full page body.
            assert "body" not in hit
            assert "raw" not in hit
            assert "content" not in hit
            assert "text" not in hit
            assert "markdown" not in hit

    def test_risks_list_capped(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        for hit in payload["hits"]:
            assert len(hit["risks"]) <= 5

    def test_summary_lines_capped_at_top(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert len(payload["summary_lines"]) <= 3


# ══════════════════════════════════════════════════════════════════════════════
# 4. search_local_knowledge — partition isolation
# ══════════════════════════════════════════════════════════════════════════════


class TestPartitionIsolation:
    def test_private_inbox_page_never_surfaced(self, fixture_kb):
        """The private inbox page mentions 603296 but lives outside
        ``wiki/investment`` — it must never appear in hits."""
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        for hit in payload["hits"]:
            assert "inbox" not in hit["rel_path"]
            assert hit["rel_path"].startswith("wiki/investment") or "investment" in hit["rel_path"]

    def test_endpoint_field_says_investment(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert payload["endpoint"] == "wiki/investment"


# ══════════════════════════════════════════════════════════════════════════════
# 5. search_local_knowledge — status mapping
# ══════════════════════════════════════════════════════════════════════════════


class TestStatusMapping:
    def test_no_query_returns_missing(self, fixture_kb):
        payload = search_local_knowledge(
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert payload["data_status"] == DATA_STATUS_MISSING
        assert payload["hit_count"] == 0
        assert payload["has_fresh_hit"] is False
        assert payload["errors"]

    def test_no_match_returns_missing(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="999999",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert payload["data_status"] == DATA_STATUS_MISSING
        assert payload["hit_count"] == 0

    def test_root_missing_returns_failed(self, tmp_path):
        bogus = str(tmp_path / "does_not_exist")
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=bogus,
            include_runtime_meta=False,
        )
        # KB-003 returns FAILED when root doesn't exist.
        assert payload["data_status"] == DATA_STATUS_FAILED
        assert payload["hit_count"] == 0

    def test_only_stale_hit_returns_stale(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="600000",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert payload["data_status"] == DATA_STATUS_STALE
        assert payload["stale_hit_count"] >= 1
        assert payload["fresh_hit_count"] == 0
        assert payload["has_fresh_hit"] is False

    def test_only_low_conf_hit_returns_stale(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="000001",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        # LOW_CONFIDENCE maps to STALE data_status per the IC vocabulary.
        assert payload["data_status"] == DATA_STATUS_STALE
        assert payload["low_confidence_hit_count"] >= 1


# ══════════════════════════════════════════════════════════════════════════════
# 6. search_local_knowledge — disable behaviour
# ══════════════════════════════════════════════════════════════════════════════


class TestDisableBehaviour:
    def test_disabled_kwarg_returns_skipped(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            disabled=True,
            include_runtime_meta=False,
        )
        assert payload["data_status"] == DATA_STATUS_SKIPPED
        assert payload["hit_count"] == 0
        assert payload["errors"]
        assert payload["knowledge_root"] == ""

    def test_disabled_env_returns_skipped(self, fixture_kb, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_CONTEXT_DISABLED", "1")
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert payload["data_status"] == DATA_STATUS_SKIPPED

    def test_disabled_kwarg_false_overrides_env(self, fixture_kb, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_CONTEXT_DISABLED", "1")
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            disabled=False,
            include_runtime_meta=False,
        )
        assert payload["data_status"] == DATA_STATUS_FRESH


# ══════════════════════════════════════════════════════════════════════════════
# 7. search_local_knowledge — max_pages clamp
# ══════════════════════════════════════════════════════════════════════════════


class TestMaxPagesClamp:
    def test_default_max_pages(self):
        from api.services.local_knowledge_context_service import _clamp_max_pages

        assert _clamp_max_pages(None) == DEFAULT_MAX_PAGES

    def test_negative_falls_back_to_default(self):
        from api.services.local_knowledge_context_service import _clamp_max_pages

        assert _clamp_max_pages(-1) == DEFAULT_MAX_PAGES
        assert _clamp_max_pages(0) == DEFAULT_MAX_PAGES

    def test_invalid_falls_back_to_default(self):
        from api.services.local_knowledge_context_service import _clamp_max_pages

        assert _clamp_max_pages("abc") == DEFAULT_MAX_PAGES  # type: ignore[arg-type]

    def test_clamped_to_absolute_max(self):
        from api.services.local_knowledge_context_service import (
            _ABSOLUTE_MAX_PAGES,
            _clamp_max_pages,
        )

        assert _clamp_max_pages(99999) == _ABSOLUTE_MAX_PAGES

    def test_valid_value_preserved(self):
        from api.services.local_knowledge_context_service import _clamp_max_pages

        assert _clamp_max_pages(3) == 3


# ══════════════════════════════════════════════════════════════════════════════
# 8. collect_local_knowledge_hits — IC bucket helper
# ══════════════════════════════════════════════════════════════════════════════


class TestCollectLocalKnowledgeHits:
    def test_per_symbol_digest_shape(self, fixture_kb):
        bucket = collect_local_knowledge_hits(
            symbols=["603296", "600000"],
            knowledge_root=str(fixture_kb),
            disabled=False,
        )
        assert bucket["source"] == CONTEXT_SOURCE
        assert bucket["as_of"]
        assert bucket["data_status"] in ALLOWED_DATA_STATUSES
        assert bucket["read_only"] is True
        assert bucket["symbol_count"] == 2
        assert bucket["fresh_symbol_count"] == 1  # 603296 fresh
        assert bucket["stale_symbol_count"] >= 1  # 600000 stale
        items = bucket["items"]
        assert len(items) == 2
        fresh_item = next(i for i in items if i["symbol"] == "603296")
        assert fresh_item["has_fresh_hit"] is True
        assert fresh_item["hit_count"] >= 1
        assert fresh_item["top_hits"]
        assert "score" in fresh_item
        stale_item = next(i for i in items if i["symbol"] == "600000")
        assert stale_item["has_fresh_hit"] is False
        assert stale_item["stale_hit_count"] >= 1

    def test_theme_query_bucket(self, fixture_kb):
        bucket = collect_local_knowledge_hits(
            symbols=["603296"],
            themes=["AI服务器"],
            knowledge_root=str(fixture_kb),
            disabled=False,
        )
        assert bucket["theme_query"] is not None
        assert bucket["theme_query"]["hit_count"] >= 1
        assert bucket["theme_count"] == 1

    def test_empty_input_returns_missing(self, fixture_kb):
        bucket = collect_local_knowledge_hits(
            symbols=[],
            knowledge_root=str(fixture_kb),
            disabled=False,
        )
        assert bucket["data_status"] == DATA_STATUS_MISSING
        assert bucket["symbol_count"] == 0
        assert bucket["items"] == []

    def test_disabled_returns_skipped(self, fixture_kb):
        bucket = collect_local_knowledge_hits(
            symbols=["603296"],
            knowledge_root=str(fixture_kb),
            disabled=True,
        )
        assert bucket["data_status"] == DATA_STATUS_SKIPPED
        assert bucket["items"] == []
        assert bucket["symbol_count"] == 0

    def test_disabled_env_returns_skipped(self, fixture_kb, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_LOCAL_DISABLED", "1")
        bucket = collect_local_knowledge_hits(
            symbols=["603296"],
            knowledge_root=str(fixture_kb),
        )
        assert bucket["data_status"] == DATA_STATUS_SKIPPED

    def test_dedupes_symbols(self, fixture_kb):
        bucket = collect_local_knowledge_hits(
            symbols=["603296", "603296", "603296"],
            knowledge_root=str(fixture_kb),
            disabled=False,
        )
        assert bucket["symbol_count"] == 1

    def test_no_symbols_but_themes_returns_fresh(self, fixture_kb):
        bucket = collect_local_knowledge_hits(
            symbols=[],
            themes=["AI服务器"],
            knowledge_root=str(fixture_kb),
            disabled=False,
        )
        assert bucket["data_status"] == DATA_STATUS_FRESH
        assert bucket["theme_query"] is not None


# ══════════════════════════════════════════════════════════════════════════════
# 9. IC context bucket — local_knowledge_hits
# ══════════════════════════════════════════════════════════════════════════════


class TestICContextBucket:
    def test_bucket_always_present(self, tmp_sqla_session, tmp_tf_db, monkeypatch, tmp_path):
        monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path / "empty_kb"))
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        assert "local_knowledge_hits" in result
        bucket = result["local_knowledge_hits"]
        assert bucket["source"] == CONTEXT_SOURCE
        assert bucket["as_of"]
        assert bucket["data_status"] in ALLOWED_DATA_STATUSES
        assert bucket["read_only"] is True

    def test_no_data_degrades_to_skipped(self, tmp_sqla_session, tmp_tf_db, tmp_path, monkeypatch):
        # Isolate from any saved mandate report + real ~/Documents/knowledge.
        monkeypatch.setattr(
            "api.services.tradeflow_service.get_mandate_daily_report",
            lambda **kw: {"status": "no_data"},
        )
        monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path / "empty_kb"))
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        bucket = result["local_knowledge_hits"]
        # No holdings/observation/candidates → skipped (not missing).
        assert bucket["data_status"] == DATA_STATUS_SKIPPED
        assert bucket["items"] == []
        assert bucket["symbol_count"] == 0

    def test_disabled_env_skips_bucket(self, tmp_sqla_session, tmp_tf_db, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_CONTEXT_DISABLED", "1")
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        bucket = result["local_knowledge_hits"]
        assert bucket["data_status"] == DATA_STATUS_SKIPPED
        assert bucket["items"] == []

    def test_holdings_symbols_drive_lookup(
        self, tmp_sqla_session, tmp_tf_db, fixture_kb, monkeypatch
    ):
        from api.database import ImportedPortfolioPositionDB
        from api.services import investment_controller_context as ic_ctx

        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        now = datetime.now(timezone.utc)
        tmp_sqla_session.add(ImportedPortfolioPositionDB(
            id=uuid.uuid4().hex,
            user_id="ic_user",
            source="manual",
            symbol="603296.SH",
            security_name="华勤技术",
            current_position=100.0,
            available_position=100.0,
            average_cost=50.0,
            market_value=5000.0,
            current_position_pct=10.0,
            trade_points_json=[],
            trade_points_count=0,
            last_imported_at=now,
        ))
        tmp_sqla_session.commit()

        # Force the knowledge root to the fixture path.
        monkeypatch.setenv("KNOWLEDGE_ROOT", str(fixture_kb))
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        bucket = result["local_knowledge_hits"]
        assert bucket["symbol_count"] >= 1
        symbols_in_bucket = {i["symbol"] for i in bucket["items"]}
        assert "603296.SH" in symbols_in_bucket
        fresh = next(i for i in bucket["items"] if i["symbol"] == "603296.SH")
        assert fresh["has_fresh_hit"] is True


# ══════════════════════════════════════════════════════════════════════════════
# 10. Controller hints — research_review lane
# ══════════════════════════════════════════════════════════════════════════════


class TestControllerHintsResearchReview:
    def test_research_review_lane_always_present(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        hints = result["controller_hints"]
        assert "research_review" in hints
        assert isinstance(hints["research_review"], list)

    def test_fresh_hit_emits_background_supplement(
        self, tmp_sqla_session, tmp_tf_db, fixture_kb, monkeypatch
    ):
        from api.database import ImportedPortfolioPositionDB
        from api.services import investment_controller_context as ic_ctx

        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        now = datetime.now(timezone.utc)
        tmp_sqla_session.add(ImportedPortfolioPositionDB(
            id=uuid.uuid4().hex,
            user_id="ic_user",
            source="manual",
            symbol="603296.SH",
            security_name="华勤技术",
            current_position=100.0,
            available_position=100.0,
            average_cost=50.0,
            market_value=5000.0,
            current_position_pct=10.0,
            trade_points_json=[],
            trade_points_count=0,
            last_imported_at=now,
        ))
        tmp_sqla_session.commit()
        monkeypatch.setenv("KNOWLEDGE_ROOT", str(fixture_kb))

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        review = result["controller_hints"]["research_review"]
        background = [r for r in review if r["suggested_next_step"] == "background_supplement"]
        assert any(r["symbol"] == "603296.SH" for r in background)

    def test_stale_hit_emits_needs_research_review(
        self, tmp_sqla_session, tmp_tf_db, fixture_kb, monkeypatch
    ):
        from api.database import ImportedPortfolioPositionDB
        from api.services import investment_controller_context as ic_ctx

        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        now = datetime.now(timezone.utc)
        tmp_sqla_session.add(ImportedPortfolioPositionDB(
            id=uuid.uuid4().hex,
            user_id="ic_user",
            source="manual",
            symbol="600000.SH",
            security_name="浦发银行",
            current_position=100.0,
            available_position=100.0,
            average_cost=10.0,
            market_value=1000.0,
            current_position_pct=10.0,
            trade_points_json=[],
            trade_points_count=0,
            last_imported_at=now,
        ))
        tmp_sqla_session.commit()
        monkeypatch.setenv("KNOWLEDGE_ROOT", str(fixture_kb))

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        review = result["controller_hints"]["research_review"]
        needs_review = [r for r in review if r["suggested_next_step"] == "needs_research_review"]
        assert any(r["symbol"] == "600000.SH" for r in needs_review)

    def test_no_hit_does_not_emit_hint(
        self, tmp_sqla_session, tmp_tf_db, fixture_kb, monkeypatch
    ):
        from api.database import ImportedPortfolioPositionDB
        from api.services import investment_controller_context as ic_ctx

        monkeypatch.setattr(ic_ctx, "_fetch_live_quotes", lambda symbols: {})
        now = datetime.now(timezone.utc)
        tmp_sqla_session.add(ImportedPortfolioPositionDB(
            id=uuid.uuid4().hex,
            user_id="ic_user",
            source="manual",
            symbol="999999.SH",
            security_name="无匹配",
            current_position=100.0,
            available_position=100.0,
            average_cost=10.0,
            market_value=1000.0,
            current_position_pct=10.0,
            trade_points_json=[],
            trade_points_count=0,
            last_imported_at=now,
        ))
        tmp_sqla_session.commit()
        monkeypatch.setenv("KNOWLEDGE_ROOT", str(fixture_kb))

        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        review = result["controller_hints"]["research_review"]
        assert not any(r["symbol"] == "999999.SH" for r in review)


# ══════════════════════════════════════════════════════════════════════════════
# 11. Cross-contract safety
# ══════════════════════════════════════════════════════════════════════════════


class TestCrossContractSafety:
    def test_search_payload_json_serializable(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        # Must not raise.
        json.dumps(payload, ensure_ascii=False)

    def test_ic_context_json_serializable(self, tmp_sqla_session, tmp_tf_db):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        json.dumps(result, ensure_ascii=False)

    def test_assert_no_strong_action_verbs_search(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        assert_kb006_no_strong_verbs(payload)

    def test_assert_no_strong_action_verbs_ic_context(
        self, tmp_sqla_session, tmp_tf_db
    ):
        result = get_investment_controller_context(
            tmp_sqla_session, "ic_user", tf_db_path=tmp_tf_db
        )
        assert_no_strong_action_verbs(result)

    def test_no_sensitive_field_keys_in_search(self, fixture_kb):
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
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

        _walk(payload)

    def test_private_page_strong_verbs_not_surfaced(self, fixture_kb):
        """The private inbox page contains '立即买入' / '满仓'.

        Since the API must NOT surface non-investment partitions, the strong
        verbs must never appear in the synthesised search payload."""
        payload = search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        text = json.dumps(payload, ensure_ascii=False)
        assert "立即买入" not in text
        assert "满仓" not in text


# ══════════════════════════════════════════════════════════════════════════════
# 12. API endpoint smoke (FastAPI TestClient)
# ══════════════════════════════════════════════════════════════════════════════


def _auth_with_user(client) -> tuple[str, str]:
    from api.database import UserDB, get_db_ctx, init_db
    from api.services import auth_service

    init_db()
    email = auth_service.normalize_email(f"kb006-{uuid.uuid4().hex[:8]}@test.com")
    now = datetime.now(timezone.utc)
    with get_db_ctx() as db:
        user = auth_service.get_user_by_email(db, email)
        if not user:
            user = UserDB(
                id=str(uuid.uuid4()),
                email=email,
                is_active=True,
                created_at=now,
                updated_at=now,
                last_login_at=now,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
    return user.id, auth_service.create_access_token(user)


class TestApiEndpoint:
    def test_search_endpoint_returns_payload(self, fixture_kb, monkeypatch):
        from fastapi.testclient import TestClient
        from api.main import app

        # Force knowledge root to fixture path so the endpoint finds the wiki.
        monkeypatch.setenv("KNOWLEDGE_ROOT", str(fixture_kb))

        client = TestClient(app, raise_server_exceptions=False)
        _, token = _auth_with_user(client)
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(
            "/v1/knowledge/local/search",
            headers=headers,
            params={"symbol": "603296"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == CONTEXT_SOURCE
        assert body["data_status"] == DATA_STATUS_FRESH
        assert body["hit_count"] >= 1
        assert body["runtime_tier_meta"]["runtime_tier"] == "FAST_RADAR"

    def test_search_endpoint_themes_param(self, fixture_kb, monkeypatch):
        from fastapi.testclient import TestClient
        from api.main import app

        monkeypatch.setenv("KNOWLEDGE_ROOT", str(fixture_kb))

        client = TestClient(app, raise_server_exceptions=False)
        _, token = _auth_with_user(client)
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(
            "/v1/knowledge/local/search",
            headers=headers,
            params={"themes": "AI服务器"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["hit_count"] >= 1

    def test_search_endpoint_no_query_returns_missing(self, fixture_kb, monkeypatch):
        from fastapi.testclient import TestClient
        from api.main import app

        monkeypatch.setenv("KNOWLEDGE_ROOT", str(fixture_kb))

        client = TestClient(app, raise_server_exceptions=False)
        _, token = _auth_with_user(client)
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(
            "/v1/knowledge/local/search",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_status"] == DATA_STATUS_MISSING

    def test_search_endpoint_disabled_env(self, fixture_kb, monkeypatch):
        from fastapi.testclient import TestClient
        from api.main import app

        monkeypatch.setenv("KNOWLEDGE_ROOT", str(fixture_kb))
        monkeypatch.setenv("KNOWLEDGE_CONTEXT_DISABLED", "1")

        client = TestClient(app, raise_server_exceptions=False)
        _, token = _auth_with_user(client)
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(
            "/v1/knowledge/local/search",
            headers=headers,
            params={"symbol": "603296"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_status"] == DATA_STATUS_SKIPPED

    def test_search_endpoint_requires_auth(self, fixture_kb, monkeypatch):
        from fastapi.testclient import TestClient
        from api.main import app

        monkeypatch.setenv("KNOWLEDGE_ROOT", str(fixture_kb))

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/v1/knowledge/local/search",
            params={"symbol": "603296"},
        )
        # Without auth header → 401/422/403, definitely not 200.
        assert resp.status_code != 200

    def test_runtime_tier_registered_as_fast(self):
        from api.runtime_tier import (
            RuntimeTier,
            tradeflow_endpoint_tier,
        )

        assert (
            tradeflow_endpoint_tier("local_knowledge_search")
            == RuntimeTier.FAST_RADAR
        )


# ══════════════════════════════════════════════════════════════════════════════
# 13. Read-only safety (no writes)
# ══════════════════════════════════════════════════════════════════════════════


class TestReadOnlySafety:
    def test_search_does_not_write_to_kb(self, fixture_kb):
        """Record mtimes of all wiki files; ensure search doesn't change them."""
        inv = fixture_kb / INVESTMENT_SUBDIR
        before = {p: p.stat().st_mtime_ns for p in inv.rglob("*.md")}

        search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )

        after = {p: p.stat().st_mtime_ns for p in inv.rglob("*.md")}
        assert before == after

    def test_search_does_not_create_files(self, fixture_kb):
        inv = fixture_kb / INVESTMENT_SUBDIR
        before = {str(p) for p in inv.rglob("*")}
        search_local_knowledge(
            symbol="603296",
            knowledge_root=str(fixture_kb),
            include_runtime_meta=False,
        )
        after = {str(p) for p in inv.rglob("*")}
        assert before == after

    def test_collect_hits_does_not_write_to_kb(self, fixture_kb):
        inv = fixture_kb / INVESTMENT_SUBDIR
        before = {p: p.stat().st_mtime_ns for p in inv.rglob("*.md")}

        collect_local_knowledge_hits(
            symbols=["603296", "600000"],
            themes=["AI服务器"],
            knowledge_root=str(fixture_kb),
            disabled=False,
        )

        after = {p: p.stat().st_mtime_ns for p in inv.rglob("*.md")}
        assert before == after
