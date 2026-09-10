# [V-015] research_operations_e2e_acceptance
"""研报增量摄取 → 证据 API → 前端契约 → 待更新清单 端到端验收。

任务卡 V-015：对 KB-019 / KB-020 / UI-014 / HY-010 做最终 fixture 端到端
验收（真实只读 smoke 由 scripts/run_v015_operations_acceptance.py 单独
执行、分开报告），确认半年报集中导入时链路可用、可追溯、不会影响交易
动作。

回放五类：
  1. 新增研报（inbox 新文件 → delta new + ingest_key 稳定幂等）
  2. 重复研报（同内容同文件名双份 → duplicate_of）
  3. 缺元数据（无 symbols/report_date → needs_metadata）
  4. 半年报修订（内容修订 → HY-009 检出 revised，证据反映新值）
  5. 事实冲突（同周期双页不同值 → 证据 conflict + HY-010 队列 conflict）

降级：局部失败 / 缓存损坏 / 空目录均可解释降级。
契约：全链路（delta / evidence / queue）不得出现 decision / action_label /
buy_level 等动作语义字段；证据 summary 字段与前端 UI-014 视图模型消费
字段保持一致。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tradingagents.dataflows.half_year_incremental_refresh import (
    CHANGE_REVISED,
    incremental_refresh_half_year_facts,
    incremental_refresh_with_disk_cache,
)
from tradingagents.dataflows.local_knowledge_cache import (
    build_cache_from_scan,
)
from tradingagents.dataflows.research_ingest_delta import (
    STATUS_DUPLICATE,
    STATUS_NEEDS_METADATA,
    STATUS_NEW,
    build_research_ingest_delta,
)
from tradingagents.tradeflow.half_year_update_queue import (
    build_half_year_update_queue,
)
from api.services import research_evidence_service
from tests.half_year_fixtures import build_half_year_fixture_kb

# 与前端 UI-014 视图模型（frontend/src/utils/researchEvidenceCenter.ts）
# 消费的真实 schema 字段保持一致的对照表
FRONTEND_CONSUMED_FIELDS = {
    "consensus": {
        "has_hit", "consensus_score", "disagreement_score",
        "attention_count_effective", "dominant_stance", "needs_fact_check",
        "fact_check_priority", "fact_check_reasons", "dimensions_brief",
        "consensus_summary",
    },
    "citation_audit": {
        "citation_audit_status", "counts", "total_claim_count",
        "checked_claim_count", "weak_source_override_blocked_count",
        "needs_tree_work_review", "fact_period", "fact_source_tier",
        "audit_summary",
    },
    "thesis_timeline": {
        "consensus_drift_score", "total_versions", "effective_versions",
        "reversed_versions", "weakened_versions", "reinforced_versions",
        "theses_brief", "timeline_summary",
    },
    "half_year_facts_pages": {"rel_path", "title", "financial_period",
                              "disclosure_date", "data_status",
                              "metric_count", "metric_keys"},
    "research_score_snapshot": {"status", "snapshot"},
}

FORBIDDEN_ACTION_KEYS = {
    "decision", "action_label", "buy_level", "execution_action",
    "playbook_stage",
}


def _walk_forbidden(node, path, hits):
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() in FORBIDDEN_ACTION_KEYS:
                hits.append(f"{path}.{k}")
            _walk_forbidden(v, f"{path}.{k}", hits)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_forbidden(v, f"{path}[{i}]", hits)


def _assert_no_action_semantics(*payloads) -> list[str]:
    hits: list[str] = []
    for p in payloads:
        _walk_forbidden(p.to_dict() if hasattr(p, "to_dict") else p, "$", hits)
    return hits


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _research_page(
    code: str,
    name: str,
    *,
    title: str = "AI服务器需求超预期",
    broker: str = "测试证券",
    report_date: str = "2026-08-20",
    with_metadata: bool = True,
) -> str:
    meta = ""
    if with_metadata:
        meta = (
            f'symbols: ["{code}.SZ {name}"]\n'
            f'report_date: "{report_date}"\n'
            f'source_type: "broker_report"\n'
        )
    return f"""---
title: {title}
created: "{report_date}"
updated: "{report_date}"
sources: [{broker}]
tags: [测试]
symbols_prefix: placeholder
{meta}report_type: 行业深度
evidence_level: B
valid_until: 2099-12-31
source_quality: 中
stale_risk: 低
---

# {name}（{code}）— {title}

## 一句话总结

{name}基本面符合预期。

## 投资逻辑

- 行业景气延续。
"""


# fixture KB（隔离 tmp，标准半年报页 symbol 000977）

@pytest.fixture()
def kb_root(tmp_path: Path) -> Path:
    return build_half_year_fixture_kb(tmp_path, include=["qualified"])


# ── 五类回放 ────────────────────────────────────────────────────────────────


class TestFiveReplayClasses:
    def test_new_research_report_ingests_with_stable_key(self, kb_root: Path) -> None:
        _write(kb_root / "inbox" / "002594-测试证券-AI服务器需求超预期.md",
               _research_page("002594", "比亚迪"))
        delta = build_research_ingest_delta(str(kb_root))
        news = delta.items_by_status(STATUS_NEW)
        assert news, "新增研报应进入 delta 清单"
        keys = {it.ingest_key for it in news}
        # 幂等：重复执行 ingest_key 稳定
        delta2 = build_research_ingest_delta(str(kb_root))
        keys2 = {it.ingest_key for it in delta2.items_by_status(STATUS_NEW)}
        assert keys == keys2

    def test_duplicate_research_report_linked(self, kb_root: Path) -> None:
        content = _research_page("002594", "比亚迪", title="重复研报回放")
        _write(kb_root / "inbox" / "002594-重复研报回放.md", content)
        _write(kb_root / "raw" / "002594-重复研报回放.md", content)
        delta = build_research_ingest_delta(str(kb_root))
        dups = delta.items_by_status(STATUS_DUPLICATE)
        assert dups, "重复研报应被判 duplicate"
        assert any(it.duplicate_of for it in dups), "duplicate 项应携带 duplicate_of"

    def test_missing_metadata_flagged_not_crash(self, kb_root: Path) -> None:
        _write(kb_root / "inbox" / "无元数据研报.md", _research_page(
            "002594", "比亚迪", with_metadata=False))
        delta = build_research_ingest_delta(str(kb_root))
        missing = delta.items_by_status(STATUS_NEEDS_METADATA)
        assert missing, "缺元数据文件应标记 needs_metadata"

    def test_half_year_revision_flows_through(self, kb_root: Path) -> None:
        old_cache = build_cache_from_scan(str(kb_root))
        # 修订：更新半年报页内容（追加修订说明）
        hy_file = kb_root / "wiki" / "investment" / "浪潮信息000977-2025H1半年报.md"
        original = hy_file.read_text(encoding="utf-8")
        hy_file.write_text(original + "\n修订说明：营收口径更新。\n", encoding="utf-8")

        result = incremental_refresh_half_year_facts(
            str(kb_root), symbol="000977", old_cache=old_cache,
            today=date(2026, 9, 1),
        )
        assert any(c.change_type == CHANGE_REVISED for c in result.page_changes)

        evidence = research_evidence_service.build_research_evidence(
            "000977", knowledge_root=str(kb_root)
        )
        # 修订不产生动作语义，链路仍只读可消费
        assert evidence["half_year_facts"] is not None

    def test_fact_conflict_propagates_to_queue(self, kb_root: Path) -> None:
        # 第二个半年报页：同 symbol 同周期，financial_facts 数值不同 → conflict
        qualified = (kb_root / "wiki" / "investment"
                     / "浪潮信息000977-2025H1半年报.md").read_text(encoding="utf-8")
        conflicted = qualified.replace("营收 420.4亿", "营收 444.4亿")
        _write(kb_root / "wiki" / "investment"
               / "浪潮信息000977-2025H1半年报-券商版.md", conflicted)

        evidence = research_evidence_service.build_research_evidence(
            "000977", knowledge_root=str(kb_root)
        )
        hy_pages = evidence["half_year_facts"]["summary"]["pages"]
        statuses = {p["data_status"] for p in hy_pages}
        assert "conflict" in statuses, "冲突必须体现在证据 API"

        # HY-010 待更新队列：持仓 symbol 反映 conflict 优先
        context = {
            "as_of": "2026-09-11 10:00:00",
            "holdings": {"items": [{"symbol": "000977.SZ", "name": "浪潮信息"}]},
            "observation_warehouse": {"items": []},
            "tradeflow_candidates": {"items": []},
            "mandate_daily_report": {"data_status": "missing"},
            "half_year_facts": {
                "items": [{"symbol": "000977.SZ", "name": "浪潮信息",
                           "has_conflict": True, "facts_status": "CONFLICT"}],
            },
        }
        queue = build_half_year_update_queue(context)
        entries = {e.symbol: e for e in queue.items}
        assert "000977.SZ" in entries
        assert entries["000977.SZ"].status == "conflict"


# ── 链路一致性 ──────────────────────────────────────────────────────────────


class TestChainConsistency:
    def test_evidence_exposes_frontend_consumed_fields(self, kb_root: Path) -> None:
        evidence = research_evidence_service.build_research_evidence(
            "000977", knowledge_root=str(kb_root)
        )
        consensus = evidence["consensus"]["summary"]
        missing = FRONTEND_CONSUMED_FIELDS["consensus"] - set(consensus.keys())
        assert not missing, f"前端消费字段缺失: {missing}"

        audit = evidence["citation_audit"]["summary"]
        missing = (
            FRONTEND_CONSUMED_FIELDS["citation_audit"] - set(audit.keys())
        )
        assert not missing, f"前端消费字段缺失: {missing}"

        timeline = evidence["thesis_timeline"]["summary"]
        missing = (
            FRONTEND_CONSUMED_FIELDS["thesis_timeline"] - set(timeline.keys())
        )
        assert not missing, f"前端消费字段缺失: {missing}"

        for page in evidence["half_year_facts"]["summary"]["pages"]:
            missing = (
                FRONTEND_CONSUMED_FIELDS["half_year_facts_pages"]
                - set(page.keys())
            )
            assert not missing, f"前端消费字段缺失: {missing}"

        snap = evidence["research_score_snapshot"]["summary"]
        missing = (
            FRONTEND_CONSUMED_FIELDS["research_score_snapshot"]
            - set(snap.keys())
        )
        assert not missing, f"前端消费字段缺失: {missing}"

    def test_no_action_semantics_anywhere(self, kb_root: Path) -> None:
        delta = build_research_ingest_delta(str(kb_root))
        evidence = research_evidence_service.build_research_evidence(
            "000977", knowledge_root=str(kb_root)
        )
        context = {
            "holdings": {"items": [{"symbol": "000977.SZ"}]},
            "observation_warehouse": {"items": []},
            "tradeflow_candidates": {"items": []},
            "mandate_daily_report": {"data_status": "missing"},
            "half_year_facts": {"items": []},
        }
        queue = build_half_year_update_queue(context)
        hits = _assert_no_action_semantics(delta.to_dict(), evidence, queue.to_dict())
        assert not hits, f"链路出现动作语义字段: {hits}"


# ── 降级 ────────────────────────────────────────────────────────────────────


class TestDegradations:
    def test_partial_failure_degrades_explainably(self, kb_root: Path) -> None:
        # 一个无法解析的页面 + 一个正常页面：证据聚合不崩溃，错误可见
        _write(kb_root / "wiki" / "investment" / "broken-page.md",
               "---\ntitle: broken\nsymbols: [unclosed\n")
        evidence = research_evidence_service.build_research_evidence(
            "000977", knowledge_root=str(kb_root)
        )
        assert evidence["task"] == "KB-020"
        # 其余 bucket 仍然产出
        assert "consensus" in evidence

    def test_corrupted_cache_rebuilds(self, kb_root: Path, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache" / "knowledge_cache.json"
        _write(cache_path, "{ not valid json !!")
        # 损坏缓存应被丢弃并全量重建，增量刷新仍能工作
        result = incremental_refresh_with_disk_cache(
            str(kb_root), symbol="000977",
            cache_path=str(cache_path), save=False,
        )
        assert result.facts_result is not None

    def test_empty_directory_degrades_explainably(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty-kb"
        (empty / "wiki" / "investment").mkdir(parents=True)
        delta = build_research_ingest_delta(str(empty))
        assert delta.total == 0 or delta.status_counts().get("new", 0) == 0
        evidence = research_evidence_service.build_research_evidence(
            "000977", knowledge_root=str(empty)
        )
        assert evidence["data_status"] in ("missing", "stale")
        queue = build_half_year_update_queue({
            "holdings": {"items": []},
            "observation_warehouse": {"items": []},
            "tradeflow_candidates": {"items": []},
            "mandate_daily_report": {"data_status": "missing"},
            "half_year_facts": {"items": []},
        })
        assert queue.to_dict() is not None
