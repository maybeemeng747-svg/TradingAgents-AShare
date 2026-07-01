# [IC-TA-004] controller_briefing_payload
"""Tests for IC-TA-004: investment-controller 飞书 briefing payload 与 TA 调度闭环验收.

Covers:
- Pure engine ``build_pre_market_payload`` / ``build_intraday_payload`` /
  ``build_post_market_payload`` on built-in fixtures + synthetic minimal contexts.
- Unified payload schema: scene / summary / ta_requests / watch_items /
  data_warnings / notify_level.
- Scene semantics: 盘前/盘后调度 TA，盘中**不**调度新 TA（只 watch + warn）.
- notify_level classification: P0/P1 → intraday_push, P2/P3 → daily_digest.
- Forbidden strong-word scan: ``scan_forbidden_words`` self-check; no synthesised
  strong action verb in any scene.
- No secrets / webhook config leak.
- READ-ONLY / FAST_RADAR contract via the service wrapper + runtime_tier.
- JSON-serialisable; graceful degradation on empty / missing buckets.
- Markdown preview rendered for Feishu card ingestion.
- Service wrapper resilience (IC context failure degrades gracefully).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.candidate_engine import init_db

from tradingagents.tradeflow.controller_briefing_payload import (
    ALLOWED_SCENES,
    INTRADAY_FIXTURE_CONTEXT,
    NOTIFY_LEVEL_DAILY_DIGEST,
    NOTIFY_LEVEL_INTRADAY_PUSH,
    PAYLOAD_SCHEMA_VERSION,
    PAYLOAD_SOURCE,
    POST_MARKET_FIXTURE_CONTEXT,
    PRE_MARKET_FIXTURE_CONTEXT,
    SCENE_INTRADAY,
    SCENE_POST_MARKET,
    SCENE_PRE_MARKET,
    WARNING_HOLDINGS_RISK_LARGE,
    WARNING_OBSERVATION_INVALIDATED,
    WARNING_REPORT_DATA_GAP,
    build_intraday_payload,
    build_post_market_payload,
    build_pre_market_payload,
    dry_run_all_scene_fixtures,
    render_briefing_markdown,
)
from tradingagents.tradeflow.controller_briefing import FORBIDDEN_STRONG_WORDS
from api.runtime_tier import RuntimeTier, tradeflow_endpoint_tier
from api.services.controller_briefing_payload_service import (
    build_briefing_payload_dry_run,
    run_briefing_scene_fixtures,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

TODAY = datetime.now().strftime("%Y-%m-%d")


@pytest.fixture
def tmp_tf_db(tmp_path):
    db_path = str(tmp_path / "test_ic_ta004_tradeflow.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def tmp_sqla_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from api.database import ImportedPortfolioPositionDB, ReportDB

    engine = create_engine("sqlite:///:memory:")
    ImportedPortfolioPositionDB.__table__.create(engine)
    ReportDB.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ──────────────────────────────────────────────────────────────────────────────
# Schema constants
# ──────────────────────────────────────────────────────────────────────────────

class TestSchemaConstants:

    def test_schema_version_is_1_0(self):
        assert PAYLOAD_SCHEMA_VERSION == "1.0"

    def test_source_identifier(self):
        assert PAYLOAD_SOURCE == "controller_briefing_payload"

    def test_three_scenes_allowed(self):
        assert set(ALLOWED_SCENES) == {SCENE_PRE_MARKET, SCENE_INTRADAY, SCENE_POST_MARKET}

    def test_notify_levels_distinct(self):
        assert NOTIFY_LEVEL_INTRADAY_PUSH != NOTIFY_LEVEL_DAILY_DIGEST


# ──────────────────────────────────────────────────────────────────────────────
# Unified payload structure (all three scenes)
# ──────────────────────────────────────────────────────────────────────────────

REQUIRED_TOP_KEYS = {
    "schema_version", "scene", "as_of", "generated_by", "read_only", "dry_run",
    "summary", "ta_requests", "watch_items", "data_warnings", "notify_level",
    "scene_extras", "notes", "markdown_preview", "forbidden_word_scan",
}


class TestPayloadStructure:

    @pytest.mark.parametrize(
        "builder,scene,ctx",
        [
            (build_pre_market_payload, SCENE_PRE_MARKET, PRE_MARKET_FIXTURE_CONTEXT),
            (build_intraday_payload, SCENE_INTRADAY, INTRADAY_FIXTURE_CONTEXT),
            (build_post_market_payload, SCENE_POST_MARKET, POST_MARKET_FIXTURE_CONTEXT),
        ],
    )
    def test_required_top_level_keys(self, builder, scene, ctx):
        payload = builder(ctx, as_of="2026-07-01 09:00:00")
        missing = REQUIRED_TOP_KEYS - set(payload.keys())
        assert not missing, f"{scene} missing keys: {missing}"
        assert payload["schema_version"] == PAYLOAD_SCHEMA_VERSION
        assert payload["scene"] == scene
        assert payload["generated_by"] == PAYLOAD_SOURCE
        assert payload["read_only"] is True
        assert payload["dry_run"] is True

    @pytest.mark.parametrize(
        "builder,scene,ctx",
        [
            (build_pre_market_payload, SCENE_PRE_MARKET, PRE_MARKET_FIXTURE_CONTEXT),
            (build_intraday_payload, SCENE_INTRADAY, INTRADAY_FIXTURE_CONTEXT),
            (build_post_market_payload, SCENE_POST_MARKET, POST_MARKET_FIXTURE_CONTEXT),
        ],
    )
    def test_summary_is_non_empty_string(self, builder, scene, ctx):
        payload = builder(ctx)
        assert isinstance(payload["summary"], str)
        assert payload["summary"]

    @pytest.mark.parametrize(
        "builder,scene,ctx",
        [
            (build_pre_market_payload, SCENE_PRE_MARKET, PRE_MARKET_FIXTURE_CONTEXT),
            (build_intraday_payload, SCENE_INTRADAY, INTRADAY_FIXTURE_CONTEXT),
            (build_post_market_payload, SCENE_POST_MARKET, POST_MARKET_FIXTURE_CONTEXT),
        ],
    )
    def test_payload_is_json_serialisable(self, builder, scene, ctx):
        payload = builder(ctx)
        # 不抛异常即可
        json.dumps(payload, ensure_ascii=False, default=str)

    @pytest.mark.parametrize(
        "builder,scene,ctx",
        [
            (build_pre_market_payload, SCENE_PRE_MARKET, PRE_MARKET_FIXTURE_CONTEXT),
            (build_intraday_payload, SCENE_INTRADAY, INTRADAY_FIXTURE_CONTEXT),
            (build_post_market_payload, SCENE_POST_MARKET, POST_MARKET_FIXTURE_CONTEXT),
        ],
    )
    def test_every_entry_has_source_and_as_of(self, builder, scene, ctx):
        payload = builder(ctx, as_of="2026-07-01 09:00:00")
        for lane in ("ta_requests", "watch_items", "data_warnings"):
            for it in payload[lane]:
                assert "source" in it, f"{scene}.{lane} entry missing source"
                assert "as_of" in it, f"{scene}.{lane} entry missing as_of"

    @pytest.mark.parametrize(
        "builder,scene,ctx",
        [
            (build_pre_market_payload, SCENE_PRE_MARKET, PRE_MARKET_FIXTURE_CONTEXT),
            (build_intraday_payload, SCENE_INTRADAY, INTRADAY_FIXTURE_CONTEXT),
            (build_post_market_payload, SCENE_POST_MARKET, POST_MARKET_FIXTURE_CONTEXT),
        ],
    )
    def test_every_entry_has_notify_level(self, builder, scene, ctx):
        payload = builder(ctx)
        for lane in ("ta_requests", "watch_items", "data_warnings"):
            for it in payload[lane]:
                assert it["notify_level"] in (
                    NOTIFY_LEVEL_INTRADAY_PUSH, NOTIFY_LEVEL_DAILY_DIGEST
                ), f"{scene}.{lane} entry invalid notify_level: {it.get('notify_level')}"


# ──────────────────────────────────────────────────────────────────────────────
# Scene semantics: 盘前/盘后调度 TA，盘中不调度
# ──────────────────────────────────────────────────────────────────────────────

class TestSceneSemantics:

    def test_pre_market_schedules_ta(self):
        payload = build_pre_market_payload(PRE_MARKET_FIXTURE_CONTEXT)
        # 盘前 fixture 有 2 项 needs_ta（拓普集团 + 寒武纪）
        assert len(payload["ta_requests"]) == 2
        for it in payload["ta_requests"]:
            assert it["reason_call_ta"], "ta_request must carry reason_call_ta"
            assert it["suggested_profile"]
            assert "requires_confirmation" in it

    def test_post_market_schedules_next_day_ta(self):
        payload = build_post_market_payload(POST_MARKET_FIXTURE_CONTEXT)
        # 盘后 fixture 有 1 项 needs_ta（寒武纪）
        assert len(payload["ta_requests"]) == 1
        # 盘后 scene_extras 含 today_performance + report_data_gaps
        assert "today_performance" in payload["scene_extras"]
        assert "report_data_gaps" in payload["scene_extras"]

    def test_intraday_does_not_schedule_ta(self):
        """核心约束：盘中 briefing 不调度新 TA（ta_requests 恒为空）."""
        payload = build_intraday_payload(INTRADAY_FIXTURE_CONTEXT)
        assert payload["ta_requests"] == []
        # 盘中只 watch + warn
        assert "不调度新 TA" in " ".join(payload["notes"])

    def test_intraday_watch_items_from_observation(self):
        payload = build_intraday_payload(INTRADAY_FIXTURE_CONTEXT)
        # 盘中 fixture 观察仓有 3 只
        assert len(payload["watch_items"]) == 3
        states = {it["state"] for it in payload["watch_items"]}
        # in_entry_zone / near_entry / watching 都应被派生
        assert "in_entry_zone" in states

    def test_intraday_holdings_risk_warning(self):
        """盘中持仓大跌应进入 data_warnings."""
        payload = build_intraday_payload(INTRADAY_FIXTURE_CONTEXT)
        warning_types = [w["warning_type"] for w in payload["data_warnings"]]
        assert WARNING_HOLDINGS_RISK_LARGE in warning_types

    def test_pre_market_has_mandate_digest(self):
        payload = build_pre_market_payload(PRE_MARKET_FIXTURE_CONTEXT)
        assert "mandate_digest" in payload["scene_extras"]
        assert "data_health_note" in payload["scene_extras"]


# ──────────────────────────────────────────────────────────────────────────────
# notify_level classification（NOTIFY 去噪规则接入）
# ──────────────────────────────────────────────────────────────────────────────

class TestNotifyLevel:

    def test_p0_p1_go_intraday_push(self):
        payload = build_intraday_payload(INTRADAY_FIXTURE_CONTEXT)
        # in_entry_zone (P0) 与 near_entry (P1) 应进 intraday_push
        push_items = [
            it for it in payload["watch_items"]
            if it["notify_level"] == NOTIFY_LEVEL_INTRADAY_PUSH
        ]
        assert len(push_items) >= 2
        priorities = {it["priority"] for it in push_items}
        assert "P0" in priorities
        assert "P1" in priorities

    def test_p2_p3_go_daily_digest(self):
        payload = build_intraday_payload(INTRADAY_FIXTURE_CONTEXT)
        digest_items = [
            it for it in payload["watch_items"]
            if it["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST
        ]
        # watching (P3) 进日报
        assert len(digest_items) >= 1
        for it in digest_items:
            assert it["priority"] in ("P2", "P3")

    def test_notify_level_summary_counts_match(self):
        payload = build_intraday_payload(INTRADAY_FIXTURE_CONTEXT)
        summary = payload["notify_level"]
        actual_push = sum(
            1 for lane in ("ta_requests", "watch_items", "data_warnings")
            for it in payload[lane]
            if it["notify_level"] == NOTIFY_LEVEL_INTRADAY_PUSH
        )
        actual_digest = sum(
            1 for lane in ("ta_requests", "watch_items", "data_warnings")
            for it in payload[lane]
            if it["notify_level"] == NOTIFY_LEVEL_DAILY_DIGEST
        )
        assert summary["intraday_push_count"] == actual_push
        assert summary["daily_digest_count"] == actual_digest

    def test_briefing_does_not_apply_dedup(self):
        """briefing 只做通道分类，去噪由 notify dry-run 通道负责."""
        payload = build_pre_market_payload(PRE_MARKET_FIXTURE_CONTEXT)
        assert payload["notify_level"]["dedup_applied"] is False


# ──────────────────────────────────────────────────────────────────────────────
# Forbidden word scan（禁用词扫描）
# ──────────────────────────────────────────────────────────────────────────────

class TestForbiddenWordScan:

    @pytest.mark.parametrize(
        "builder,ctx",
        [
            (build_pre_market_payload, PRE_MARKET_FIXTURE_CONTEXT),
            (build_intraday_payload, INTRADAY_FIXTURE_CONTEXT),
            (build_post_market_payload, POST_MARKET_FIXTURE_CONTEXT),
        ],
    )
    def test_fixture_payloads_have_no_forbidden_words(self, builder, ctx):
        payload = builder(ctx)
        scan = payload["forbidden_word_scan"]
        assert scan["found"] is False, f"forbidden words found: {scan['hits']}"
        assert scan["hits"] == []

    def test_scan_detects_injected_strong_word(self):
        payload = build_pre_market_payload(PRE_MARKET_FIXTURE_CONTEXT)
        # 注入强词到引擎合成字段
        payload["summary"] = "建议立即买入"
        from tradingagents.tradeflow.controller_briefing import scan_forbidden_words
        result = scan_forbidden_words(payload)
        assert result["found"] is True

    def test_scanned_words_cover_strong_verbs(self):
        payload = build_pre_market_payload(PRE_MARKET_FIXTURE_CONTEXT)
        scanned = payload["forbidden_word_scan"]["scanned_words"]
        for word in ("立即买入", "清仓", "满仓", "梭哈"):
            assert word in scanned


# ──────────────────────────────────────────────────────────────────────────────
# No secrets / sensitive config leak
# ──────────────────────────────────────────────────────────────────────────────

SENSITIVE_KEYS = {"api_key", "token", "secret", "password", "webhook_url", "webhook"}


class TestNoSecrets:

    @pytest.mark.parametrize(
        "builder,ctx",
        [
            (build_pre_market_payload, PRE_MARKET_FIXTURE_CONTEXT),
            (build_intraday_payload, INTRADAY_FIXTURE_CONTEXT),
            (build_post_market_payload, POST_MARKET_FIXTURE_CONTEXT),
        ],
    )
    def test_payload_has_no_sensitive_keys(self, builder, ctx):
        payload = builder(ctx)

        def _walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    assert k.lower() not in SENSITIVE_KEYS, f"sensitive key found: {k}"
                    _walk(v)
            elif isinstance(obj, list):
                for it in obj:
                    _walk(it)

        _walk(payload)

    @pytest.mark.parametrize(
        "builder,ctx",
        [
            (build_pre_market_payload, PRE_MARKET_FIXTURE_CONTEXT),
            (build_intraday_payload, INTRADAY_FIXTURE_CONTEXT),
            (build_post_market_payload, POST_MARKET_FIXTURE_CONTEXT),
        ],
    )
    def test_payload_contains_no_forbidden_word_string(self, builder, ctx):
        """合成文本（排除 scan 元数据）不含任何强动作词（防御性，覆盖 markdown 预览）."""
        payload = builder(ctx)
        # forbidden_word_scan.scanned_words 是"被扫描词列表"元数据，本身包含
        # 禁用词字面值是正常的（它声明扫描了哪些词），不属于引擎合成内容，排除。
        scan = payload.pop("forbidden_word_scan", {})
        blob = json.dumps(payload, ensure_ascii=False, default=str)
        payload["forbidden_word_scan"] = scan  # 还原，避免影响后续测试
        # TA 报告原始 decision 字段允许 HOLD 等事实值；但强动作词不应出现在合成文本
        for word in ("立即买入", "重仓买入", "立即清仓", "满仓", "梭哈", "立即卖出", "全仓"):
            assert word not in blob, f"strong word {word!r} leaked into payload"


# ──────────────────────────────────────────────────────────────────────────────
# Markdown preview（飞书卡片预览）
# ──────────────────────────────────────────────────────────────────────────────

class TestMarkdownPreview:

    @pytest.mark.parametrize(
        "builder,scene_label,ctx",
        [
            (build_pre_market_payload, "盘前", PRE_MARKET_FIXTURE_CONTEXT),
            (build_intraday_payload, "盘中", INTRADAY_FIXTURE_CONTEXT),
            (build_post_market_payload, "盘后", POST_MARKET_FIXTURE_CONTEXT),
        ],
    )
    def test_markdown_has_scene_header_and_summary(self, builder, scene_label, ctx):
        payload = builder(ctx)
        md = payload["markdown_preview"]
        assert isinstance(md, str)
        assert scene_label in md
        assert payload["summary"] in md
        assert "dry-run" in md

    def test_markdown_contains_notify_level_section(self):
        payload = build_intraday_payload(INTRADAY_FIXTURE_CONTEXT)
        md = payload["markdown_preview"]
        assert "notify_level" in md
        assert "盘中主动提醒" in md
        assert "夜间日报" in md

    def test_render_briefing_markdown_is_stable_on_empty(self):
        """空 payload 也能渲染不抛异常."""
        empty_payload = {
            "scene": SCENE_PRE_MARKET,
            "as_of": "2026-07-01 09:00:00",
            "summary": "盘前 briefing：无待调度项",
            "ta_requests": [],
            "watch_items": [],
            "data_warnings": [],
            "notify_level": {
                "intraday_push_count": 0,
                "daily_digest_count": 0,
                "dedup_applied": False,
            },
            "notes": [],
            "read_only": True,
        }
        md = render_briefing_markdown(empty_payload)
        assert "_（无）_" in md


# ──────────────────────────────────────────────────────────────────────────────
# Empty / degraded context（稳定空结构）
# ──────────────────────────────────────────────────────────────────────────────

class TestEmptyAndDegradedContext:

    @pytest.mark.parametrize(
        "builder,scene",
        [
            (build_pre_market_payload, SCENE_PRE_MARKET),
            (build_intraday_payload, SCENE_INTRADAY),
            (build_post_market_payload, SCENE_POST_MARKET),
        ],
    )
    def test_empty_context_does_not_raise(self, builder, scene):
        payload = builder({}, as_of="2026-07-01 09:00:00")
        assert payload["scene"] == scene
        assert payload["ta_requests"] == []
        assert payload["watch_items"] == []
        # 空上下文也应通过禁用词扫描
        assert payload["forbidden_word_scan"]["found"] is False

    def test_missing_buckets_degrade_gracefully(self):
        ctx = {"is_trading_day": True, "previous_trade_date": "2026-06-25"}
        payload = build_intraday_payload(ctx)
        assert payload["data_warnings"] == []
        assert payload["watch_items"] == []

    def test_data_gap_becomes_warning(self):
        """报告数据缺口应进入 data_warnings."""
        ctx = dict(POST_MARKET_FIXTURE_CONTEXT)
        payload = build_post_market_payload(ctx)
        gap_types = [w["warning_type"] for w in payload["data_warnings"]]
        assert WARNING_REPORT_DATA_GAP in gap_types


# ──────────────────────────────────────────────────────────────────────────────
# dry_run_all_scene_fixtures
# ──────────────────────────────────────────────────────────────────────────────

class TestDryRunAllSceneFixtures:

    def test_returns_three_scenes_and_summary(self):
        report = dry_run_all_scene_fixtures(as_of="2026-07-01 09:00:00")
        assert set(("pre_market", "intraday", "post_market", "summary")).issubset(report.keys())

    def test_summary_has_forbidden_word_scan_passed(self):
        report = dry_run_all_scene_fixtures()
        assert report["summary"]["forbidden_word_scan_passed"] is True

    def test_summary_counts_match(self):
        report = dry_run_all_scene_fixtures()
        s = report["summary"]
        assert s["pre_ta_count"] == len(report["pre_market"]["ta_requests"])
        assert s["intraday_ta_count"] == 0  # 盘中不调度
        assert s["intraday_watch_count"] == len(report["intraday"]["watch_items"])
        assert s["post_ta_count"] == len(report["post_market"]["ta_requests"])

    def test_all_scene_payloads_json_serialisable(self):
        report = dry_run_all_scene_fixtures()
        json.dumps(report, ensure_ascii=False, default=str)


# ──────────────────────────────────────────────────────────────────────────────
# Service wrapper contract
# ──────────────────────────────────────────────────────────────────────────────

class TestServiceWrapper:

    @pytest.mark.parametrize("scene", list(ALLOWED_SCENES))
    def test_service_attaches_runtime_tier_meta(self, tmp_sqla_session, scene):
        payload = build_briefing_payload_dry_run(
            tmp_sqla_session, "user-test", scene=scene,
        )
        meta = payload["runtime_tier_meta"]
        assert meta["runtime_tier"] == "FAST_RADAR"
        assert meta["llm_allowed"] is False
        assert payload["scene"] == scene

    @pytest.mark.parametrize("scene", list(ALLOWED_SCENES))
    def test_service_attaches_context_data_status(self, tmp_sqla_session, scene):
        payload = build_briefing_payload_dry_run(
            tmp_sqla_session, "user-test", scene=scene,
        )
        cds = payload["context_data_status"]
        assert "holdings" in cds
        assert "observation_warehouse" in cds

    def test_service_unknown_scene_raises(self, tmp_sqla_session):
        with pytest.raises(ValueError):
            build_briefing_payload_dry_run(
                tmp_sqla_session, "user-test", scene="invalid_scene",
            )

    def test_service_degrades_when_ic_context_fails(
        self, tmp_sqla_session, monkeypatch
    ):
        """IC context 构建失败时 service 不抛异常，返回稳定空结构."""
        from api.services import controller_briefing_payload_service as svc

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated IC context failure")

        monkeypatch.setattr(svc, "get_investment_controller_context", _boom)
        payload = build_briefing_payload_dry_run(
            tmp_sqla_session, "user-test", scene=SCENE_PRE_MARKET,
        )
        assert payload["scene"] == SCENE_PRE_MARKET
        assert payload["ta_requests"] == []
        assert payload["forbidden_word_scan"]["found"] is False

    def test_service_is_read_only_on_db(self, tmp_sqla_session):
        """service 不写 DB：调用前后行数不变."""
        from api.database import ImportedPortfolioPositionDB, ReportDB

        before_pos = tmp_sqla_session.query(ImportedPortfolioPositionDB).count()
        before_rep = tmp_sqla_session.query(ReportDB).count()
        for scene in ALLOWED_SCENES:
            build_briefing_payload_dry_run(tmp_sqla_session, "user-test", scene=scene)
        after_pos = tmp_sqla_session.query(ImportedPortfolioPositionDB).count()
        after_rep = tmp_sqla_session.query(ReportDB).count()
        assert before_pos == after_pos
        assert before_rep == after_rep


class TestServiceFixtures:

    @pytest.mark.parametrize(
        "kind,expected_key",
        [
            ("pre", "scene"),
            ("intraday", "scene"),
            ("post", "scene"),
            ("all", "summary"),
        ],
    )
    def test_run_fixture_returns_expected_shape(self, kind, expected_key):
        result = run_briefing_scene_fixtures(kind=kind)
        assert expected_key in result
        meta = result["runtime_tier_meta"]
        assert meta["runtime_tier"] == "FAST_RADAR"

    def test_run_fixture_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            run_briefing_scene_fixtures(kind="invalid")


# ──────────────────────────────────────────────────────────────────────────────
# Runtime tier registration
# ──────────────────────────────────────────────────────────────────────────────

class TestRuntimeTierRegistration:

    def test_controller_briefing_payload_is_fast_radar(self):
        tier = tradeflow_endpoint_tier("controller_briefing_payload")
        assert tier == RuntimeTier.FAST_RADAR

    def test_service_meta_matches_fast_radar(self):
        result = run_briefing_scene_fixtures(kind="pre")
        assert result["runtime_tier_meta"]["llm_allowed"] is False
        assert result["runtime_tier_meta"]["requires_confirmation"] is False


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures pass strong-word guard（强守卫）
# ──────────────────────────────────────────────────────────────────────────────

class TestFixturesPassStrongWordGuard:

    def test_every_forbidden_word_actually_detected_by_scan(self):
        from tradingagents.tradeflow.controller_briefing import scan_forbidden_words
        for word in FORBIDDEN_STRONG_WORDS:
            payload = {"summary": f"含 {word} 的合成文本"}
            result = scan_forbidden_words(payload)
            assert result["found"] is True, f"word {word!r} not detected"

    @pytest.mark.parametrize(
        "builder,ctx",
        [
            (build_pre_market_payload, PRE_MARKET_FIXTURE_CONTEXT),
            (build_intraday_payload, INTRADAY_FIXTURE_CONTEXT),
            (build_post_market_payload, POST_MARKET_FIXTURE_CONTEXT),
        ],
    )
    def test_fixture_markdown_preview_has_no_strong_word(self, builder, ctx):
        payload = builder(ctx)
        md = payload["markdown_preview"]
        for word in FORBIDDEN_STRONG_WORDS:
            assert word not in md, f"strong word {word!r} in markdown preview"
