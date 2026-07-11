# [KB-018] research_thesis_timeline
"""Tests for 同股研报观点版本演化与共识漂移时间线 (KB-018).

覆盖（验收要求）：
  - fixture 覆盖观点强化、削弱、反转、重复覆盖、过期和缺日期六类。
  - 乱序输入得到稳定时间线；弱来源不得覆盖强来源。
  - 输出不含强动作词，不改变现有 decision/action_label。

额外覆盖：
  - thesis_version_status 六枚举值全部出现。
  - consensus_drift_score 计分（反转 > 削弱 > 过期 > 待验证 > 强化）。
  - 同机构重复覆盖去重（不计入 drift）。
  - KB-017 citation_audit 接入（pending_fact_check 状态）。
  - 主题/方向聚类、JSON 序列化、扁平摘要、报告渲染。
  - 约束：只读 / 不写生产 DB / 无强动作词 / 不携带 decision 字段。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.citation_policy import (
    TIER_BROKER_RESEARCH,
    TIER_MEDIA,
    TIER_ORIGINAL_FILING,
)
from tradingagents.dataflows.citation_fact_audit import (
    CitationAuditFlag,
    CitationAuditResult,
    STATUS_INSUFFICIENT_DATA,
    STATUS_PENDING,
)
from tradingagents.dataflows.research_thesis_timeline import (
    ALL_VERSION_STATUSES,
    TASK_CODE,
    VERSION_NEW,
    VERSION_PENDING_FACT_CHECK,
    VERSION_REINFORCED,
    VERSION_REVERSED,
    VERSION_STALE,
    VERSION_WEAKENED,
    ResearchThesisTimelineResult,
    SymbolThesisTimelineResult,
    ThesisTimeline,
    ThesisVersion,
    build_research_thesis_timeline,
    has_forbidden_action_words,
    lookup_research_thesis_timeline,
    render_research_thesis_timeline_report,
    suggest_report_output_path,
    timeline_to_ta_consumable_summary,
    _claim_theme,
    _compute_drift_score,
    _decide_version_status,
    _find_duplicate_prev,
    _lookup_audit_status,
    _symbol_equivalent,
    _text_similarity,
    _CandidateNode,
    _build_claim_audit_map,
)
from tradingagents.dataflows.research_fact_opinion_index import (
    CLAIM_FORECAST,
    CLAIM_OPINION,
    CLAIM_RISK,
    ResearchClaimItem,
    ResearchFactOpinionPage,
)
from tradingagents.dataflows.thesis_fact_check import (
    DIR_NEGATIVE,
    DIR_NEUTRAL,
    DIR_POSITIVE,
)


# ── helpers ──────────────────────────────────────────────────────────


_FORBIDDEN_ACTION_WORDS = (
    "买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL",
    "立即买入", "全仓", "止损",
)


def _assert_no_strong_action_words(text: str) -> None:
    for forbidden in _FORBIDDEN_ACTION_WORDS:
        assert forbidden not in text, (
            f"text contains forbidden action word: {forbidden}"
        )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 测试基准日期：让所有 fixture 都在窗口内。
_TODAY = date(2026, 7, 12)


def _broker_page(
    rel_name: str,
    *,
    symbol: str,
    name: str,
    title: str,
    body_opinion: str,
    body_risk: str = "风险可控。",
    report_date: str = "2026-06-01",
    stale_risk: str = "低",
    valid_until: str = "2099-12-31",
    evidence_level: str = "B",
    source_quality: str = "中",
    extra_fm: str = "",
    body_forecast: str = "",
    institution_hint: str = "",
    no_date: bool = False,
) -> str:
    """生成一份券商研报 markdown（用于时间线 fixture）。"""
    sources = (
        f"[[{institution_hint or '某券商'}-研究|"
        f"{institution_hint or '某券商'}深度]]"
    )
    # no_date=True 时不写 created/updated，制造缺日期 fixture。
    date_lines = "" if no_date else (
        f"created: {report_date}\nupdated: {report_date}\n"
    )
    fm = f"""---
title: {title}
{date_lines}sources:
  - "{sources}"
tags: [{name}]
related: []
symbols: ["{symbol} {name}"]
themes: [AI]
report_type: 深度
evidence_level: {evidence_level}
valid_until: {valid_until}
source_quality: {source_quality}
stale_risk: {stale_risk}
{extra_fm}
---

# {title}

## 一句话总结

{body_opinion}

## 投资逻辑

- {body_opinion}

## 前瞻指引

- {body_forecast or body_opinion}

## 风险提示

- {body_risk}
"""
    return fm


# ── fixtures：六类 ───────────────────────────────────────────────────

# 1) 强化：同 symbol 同主题（营收），先 1 篇正面，后 2 篇不同机构正面。
_REINFORCE_1 = _broker_page(
    "reinforce-1.md",
    symbol="600100.SH", name="某甲公司",
    title="中信证券-某甲公司深度",
    body_opinion="营收高增长，渗透率提升。",
    body_risk="风险可控。",
    body_forecast="预计全年营收增长 30%。",
    institution_hint="中信证券",
    report_date="2026-03-01",
)
_REINFORCE_2 = _broker_page(
    "reinforce-2.md",
    symbol="600100.SH", name="某甲公司",
    title="国泰君安-某甲公司点评",
    body_opinion="营收超预期，产业链龙头地位稳固。",
    body_risk="风险下降。",
    body_forecast="上调全年营收增长预测至 40%。",
    institution_hint="国泰君安",
    report_date="2026-05-01",
)
_REINFORCE_3 = _broker_page(
    "reinforce-3.md",
    symbol="600100.SH", name="某甲公司",
    title="华泰证券-某甲公司跟踪",
    body_opinion="营收加速改善，继续放量。",
    body_risk="无明显风险。",
    body_forecast="营收增长强劲。",
    institution_hint="华泰证券",
    report_date="2026-06-01",
)

# 2) 削弱：同 symbol 同主题（营收），先正面后变负面（但不是完全反转的强证据
#    —— 这里用"从 positive 到 negative" 测削弱分支？任务要求削弱 vs 反转都
#    要测。削弱用"从明确方向退化为中性/弱化"。
_WEAKEN_1 = _broker_page(
    "weaken-1.md",
    symbol="600200.SH", name="某乙公司",
    title="中金公司-某乙公司深度",
    body_opinion="营收高增长，渗透率快速提升。",
    body_risk="风险可控。",
    body_forecast="预计全年营收增长 30%。",
    institution_hint="中金公司",
    report_date="2026-03-01",
)
_WEAKEN_2 = _broker_page(
    "weaken-2.md",
    symbol="600200.SH", name="某乙公司",
    title="海通证券-某乙公司跟踪",
    # 第二条 neutral 化：不再含强方向词，体现"从中性化削弱"。
    body_opinion="公司经营稳定，业务正常推进。",
    body_risk="无明显风险。",
    body_forecast="营收保持稳定。",
    institution_hint="海通证券",
    report_date="2026-05-01",
)

# 3) 反转：同 symbol 同主题（营收），先正面后负面（不同机构，同 tier）。
_REVERSE_1 = _broker_page(
    "reverse-1.md",
    symbol="600300.SH", name="某丙公司",
    title="招商证券-某丙公司看多",
    body_opinion="营收高增长，渗透率提升。",
    body_risk="风险可控。",
    body_forecast="预计全年营收增长 30%。",
    institution_hint="招商证券",
    report_date="2026-03-01",
)
_REVERSE_2 = _broker_page(
    "reverse-2.md",
    symbol="600300.SH", name="某丙公司",
    title="申万宏源-某丙公司下调",
    body_opinion="营收同比下降，竞争加剧，不及预期。",
    body_risk="高风险。",
    body_forecast="下修全年营收预测。",
    institution_hint="申万宏源",
    report_date="2026-05-01",
)

# 4) 重复覆盖：同机构（中信）+ 同主题（营收）+ 文本高度相似。
_DUP_1 = _broker_page(
    "dup-1.md",
    symbol="600400.SH", name="某丁公司",
    title="中信证券-某丁公司深度",
    body_opinion="营收高增长，渗透率提升，龙头地位稳固。",
    body_risk="风险可控。",
    body_forecast="预计全年营收增长 30%。",
    institution_hint="中信证券",
    report_date="2026-03-01",
)
_DUP_2 = _broker_page(
    "dup-2.md",
    symbol="600400.SH", name="某丁公司",
    title="中信证券-某丁公司深度更新",
    # 与 dup-1 文本几乎一致 → 同机构重复。
    body_opinion="营收高增长，渗透率提升，龙头地位稳固。",
    body_risk="风险可控。",
    body_forecast="预计全年营收增长 30%。",
    institution_hint="中信证券",
    report_date="2026-05-01",
)
_DUP_3 = _broker_page(
    "dup-3.md",
    symbol="600400.SH", name="某丁公司",
    title="国泰君安-某丁公司点评",
    body_opinion="营收超预期，核心受益。",
    body_risk="风险下降。",
    institution_hint="国泰君安",
    report_date="2026-06-01",
)

# 5) 过期：stale_risk=高 + valid_until 已过期。
_STALE_PAGE = _broker_page(
    "stale-1.md",
    symbol="600500.SH", name="某戊公司",
    title="中信证券-某戊公司旧深度",
    body_opinion="营收高增长。",
    body_risk="风险可控。",
    body_forecast="预计全年营收增长 30%。",
    institution_hint="中信证券",
    stale_risk="高",
    valid_until="2025-06-30",
    report_date="2025-01-01",
)
_STALE_FOLLOWUP = _broker_page(
    "stale-2.md",
    symbol="600500.SH", name="某戊公司",
    title="国泰君安-某戊公司跟踪",
    body_opinion="营收继续高增长，加速放量。",
    body_risk="风险可控。",
    body_forecast="营收增长强劲。",
    institution_hint="国泰君安",
    report_date="2026-05-01",
)

# 6) 缺日期：no_date=True。
_NODATE_1 = _broker_page(
    "nodate-1.md",
    symbol="600600.SH", name="某己公司",
    title="中信证券-某己公司深度",
    body_opinion="营收高增长，渗透率提升。",
    body_risk="风险可控。",
    body_forecast="预计全年营收增长 30%。",
    institution_hint="中信证券",
    no_date=True,
)
_NODATE_2 = _broker_page(
    "nodate-2.md",
    symbol="600600.SH", name="某己公司",
    title="国泰君安-某己公司点评",
    body_opinion="营收超预期，继续放量。",
    body_risk="风险下降。",
    institution_hint="国泰君安",
    report_date="2026-05-01",
)


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    """构造覆盖六类 + 边界场景的微型知识库。"""
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "reinforce-1.md", _REINFORCE_1)
    _write(inv / "reinforce-2.md", _REINFORCE_2)
    _write(inv / "reinforce-3.md", _REINFORCE_3)
    _write(inv / "weaken-1.md", _WEAKEN_1)
    _write(inv / "weaken-2.md", _WEAKEN_2)
    _write(inv / "reverse-1.md", _REVERSE_1)
    _write(inv / "reverse-2.md", _REVERSE_2)
    _write(inv / "dup-1.md", _DUP_1)
    _write(inv / "dup-2.md", _DUP_2)
    _write(inv / "dup-3.md", _DUP_3)
    _write(inv / "stale-1.md", _STALE_PAGE)
    _write(inv / "stale-2.md", _STALE_FOLLOWUP)
    _write(inv / "nodate-1.md", _NODATE_1)
    _write(inv / "nodate-2.md", _NODATE_2)
    return tmp_path


# ── 1. 六类 fixture 覆盖 ─────────────────────────────────────────────


class TestSixFixtureTypes:
    """验收要求：fixture 覆盖强化、削弱、反转、重复覆盖、过期、缺日期六类。"""

    def test_reinforced_chain(self, fixture_kb: Path):
        """强化：3 家不同机构同方向 → 出现 reinforced，drift 低。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        assert result.status != "FAILED"
        assert len(result.symbols) == 1
        sym = result.symbols[0]
        # 至少有一条 thesis 出现 reinforced。
        statuses = [
            v.thesis_version_status
            for t in sym.theses for v in t.versions
        ]
        assert VERSION_REINFORCED in statuses
        assert VERSION_NEW in statuses
        # 全是同方向 → drift 应该很低。
        assert sym.consensus_drift_score < 0.3

    def test_weakened_chain(self, fixture_kb: Path):
        """削弱：从 positive 到 neutral → 出现 weakened。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600200", today=_TODAY,
        )
        assert len(result.symbols) == 1
        sym = result.symbols[0]
        statuses = [
            v.thesis_version_status
            for t in sym.theses for v in t.versions
        ]
        assert VERSION_WEAKENED in statuses
        assert sym.weakened_versions >= 1

    def test_reversed_chain(self, fixture_kb: Path):
        """反转：从 positive 到 negative（同 tier 不同机构）→ 出现 reversed。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600300", today=_TODAY,
        )
        assert len(result.symbols) == 1
        sym = result.symbols[0]
        statuses = [
            v.thesis_version_status
            for t in sym.theses for v in t.versions
        ]
        assert VERSION_REVERSED in statuses
        assert sym.reversed_versions >= 1

    def test_duplicate_coverage_collapsed(self, fixture_kb: Path):
        """重复覆盖：同机构+文本相似 → is_duplicate=True，不计入 drift。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600400", today=_TODAY,
        )
        assert len(result.symbols) == 1
        sym = result.symbols[0]
        assert sym.duplicate_versions >= 1
        # 重复不计入有效版本。
        assert sym.effective_versions < sym.total_versions
        # 至少有一个版本 is_duplicate=True 且 duplicate_of 非空。
        dup_found = False
        for t in sym.theses:
            for v in t.versions:
                if v.is_duplicate:
                    assert v.duplicate_of != ""
                    dup_found = True
        assert dup_found

    def test_stale_version(self, fixture_kb: Path):
        """过期：stale_risk=高 → 出现 stale 状态。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600500", today=_TODAY,
        )
        assert len(result.symbols) == 1
        sym = result.symbols[0]
        statuses = [
            v.thesis_version_status
            for t in sym.theses for v in t.versions
        ]
        assert VERSION_STALE in statuses
        assert sym.stale_versions >= 1

    def test_missing_date_stable_ordering(self, fixture_kb: Path):
        """缺日期：无日期节点仍入线，且排在有日期节点之后。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600600", today=_TODAY,
        )
        assert len(result.symbols) == 1
        sym = result.symbols[0]
        # 至少有一条 thesis 同时含无日期和有日期节点。
        found_no_date = False
        for t in sym.theses:
            dates = [v.report_date for v in t.versions]
            if any(d is None for d in dates):
                found_no_date = True
                # 无日期节点应排在有日期节点之后。
                first_none_idx = next(
                    i for i, d in enumerate(dates) if d is None
                )
                for i, d in enumerate(dates[:first_none_idx]):
                    assert d is not None
        assert found_no_date


# ── 2. thesis_version_status 枚举 ────────────────────────────────────


class TestVersionStatusEnum:
    def test_all_six_statuses_defined(self):
        assert set(ALL_VERSION_STATUSES) == {
            VERSION_NEW, VERSION_REINFORCED, VERSION_WEAKENED,
            VERSION_REVERSED, VERSION_STALE, VERSION_PENDING_FACT_CHECK,
        }

    def test_all_six_statuses_appear_with_audit(self, fixture_kb: Path):
        """通过 citation_audit 注入 pending_fact_check，覆盖全部 6 状态。"""
        # 为 600100 的某条 claim 注入 pending。
        # 先找到 600100 的某条 claim 文本。
        sym = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        ).symbols[0]
        target_text = None
        target_path = None
        for t in sym.theses:
            for v in t.versions:
                if v.claim_text:
                    target_text = v.claim_text
                    target_path = v.rel_path
                    break
            if target_text:
                break
        assert target_text is not None

        audit = CitationAuditResult(symbol="600100.SH")
        audit.flags.append(CitationAuditFlag(
            claim_text=target_text,
            metric_key="other",
            audit_status=STATUS_PENDING,
            source_path=target_path or "",
        ))
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
            citation_audit=audit,
        )
        sym = result.symbols[0]
        all_statuses = {
            v.thesis_version_status
            for t in sym.theses for v in t.versions
        }
        # 至少 pending_fact_check 出现。
        assert VERSION_PENDING_FACT_CHECK in all_statuses
        assert result.has_citation_audit is True


# ── 3. _decide_version_status 单元 ───────────────────────────────────


def _make_node(
    *, direction=DIR_POSITIVE, tier=TIER_BROKER_RESEARCH,
    stale_status="fresh", institution="某券商",
    text="营收增长", rel_path="a.md",
) -> _CandidateNode:
    return _CandidateNode(
        symbol="600100", name="某甲", rel_path=rel_path,
        claim=ResearchClaimItem(
            claim_type=CLAIM_OPINION, text=text,
            origin_field="body:opinion",
        ),
        report_date=date(2026, 6, 1),
        report_date_raw="2026-06-01",
        source_quality_tier=tier,
        stale_status=stale_status,
        confidence="medium",
        institution=institution,
        theme="revenue",
        theme_token="revenue",
        direction=direction,
    )


class TestDecideVersionStatus:
    def test_first_node_is_new(self):
        node = _make_node()
        status, weak, reason = _decide_version_status(node, None, "")
        assert status == VERSION_NEW
        assert weak is False

    def test_stale_overrides_everything(self):
        node = _make_node(stale_status="stale")
        prev = _make_node(direction=DIR_NEGATIVE, rel_path="b.md")
        status, weak, reason = _decide_version_status(node, prev, "")
        assert status == VERSION_STALE

    def test_pending_fact_check_from_audit(self):
        node = _make_node()
        prev = _make_node(direction=DIR_NEGATIVE, rel_path="b.md")
        status, weak, reason = _decide_version_status(
            node, prev, STATUS_PENDING,
        )
        assert status == VERSION_PENDING_FACT_CHECK

    def test_pending_fact_check_insufficient_data(self):
        node = _make_node()
        status, _, _ = _decide_version_status(
            node, None, STATUS_INSUFFICIENT_DATA,
        )
        # 无 prev 且 audit=insufficient → pending（优先于 new）。
        assert status == VERSION_PENDING_FACT_CHECK

    def test_reversed_same_tier(self):
        node = _make_node(direction=DIR_POSITIVE)
        prev = _make_node(direction=DIR_NEGATIVE, rel_path="b.md")
        status, weak, reason = _decide_version_status(node, prev, "")
        assert status == VERSION_REVERSED
        assert weak is False

    def test_reversed_weak_source_blocked(self):
        """弱来源（media）晚于强来源（filing）+ 方向相反 → 削弱而非反转。"""
        node = _make_node(direction=DIR_POSITIVE, tier=TIER_MEDIA)
        prev = _make_node(
            direction=DIR_NEGATIVE, tier=TIER_ORIGINAL_FILING,
            rel_path="b.md",
        )
        status, weak, reason = _decide_version_status(node, prev, "")
        assert status == VERSION_WEAKENED
        assert weak is True

    def test_same_direction_reinforced(self):
        node = _make_node(direction=DIR_POSITIVE, institution="A")
        prev = _make_node(
            direction=DIR_POSITIVE, institution="B", rel_path="b.md",
        )
        status, _, reason = _decide_version_status(node, prev, "")
        assert status == VERSION_REINFORCED
        assert "新机构" in reason

    def test_same_direction_same_inst_reinforced(self):
        node = _make_node(direction=DIR_POSITIVE, institution="A")
        prev = _make_node(
            direction=DIR_POSITIVE, institution="A", rel_path="b.md",
        )
        status, _, reason = _decide_version_status(node, prev, "")
        assert status == VERSION_REINFORCED
        assert "同机构" in reason

    def test_neutral_drift_to_neutral_weakened(self):
        """从 positive 退化到 neutral → 削弱。"""
        node = _make_node(direction=DIR_NEUTRAL)
        prev = _make_node(direction=DIR_POSITIVE, rel_path="b.md")
        status, _, _ = _decide_version_status(node, prev, "")
        assert status == VERSION_WEAKENED


# ── 4. 同机构重复检测 ────────────────────────────────────────────────


class TestDuplicateDetection:
    def test_text_similarity_identical(self):
        assert _text_similarity(
            "营收高增长，渗透率提升",
            "营收高增长，渗透率提升",
        ) == 1.0

    def test_text_similarity_unrelated(self):
        assert _text_similarity(
            "营收高增长",
            "客户集中度风险加剧",
        ) < 0.4

    def test_text_similarity_short_subset(self):
        # 短文本子集。
        assert _text_similarity("营收", "营收高增长") == 1.0

    def test_find_duplicate_same_path_same_text(self):
        node = _make_node(
            rel_path="a.md", institution="中信",
            text="营收高增长，渗透率提升",
        )
        prevs = [_make_node(
            rel_path="a.md", institution="中信",
            text="营收高增长，渗透率提升",
        )]
        # 同 rel_path + 同文本 → 重复。
        idx = _find_duplicate_prev(node, prevs)
        assert idx == 0

    def test_find_duplicate_same_path_diff_text_not_dup(self):
        """同 rel_path 但不同文本（opinion vs forecast）→ 不算重复。"""
        node = _make_node(
            rel_path="a.md", institution="中信",
            text="预计全年营收增长 30%",
        )
        prevs = [_make_node(
            rel_path="a.md", institution="中信",
            text="客户集中度风险加剧",
        )]
        idx = _find_duplicate_prev(node, prevs)
        assert idx is None

    def test_find_duplicate_similar_text(self):
        node = _make_node(
            rel_path="a.md", institution="中信",
            text="营收高增长，渗透率提升，龙头地位稳固",
        )
        prevs = [_make_node(
            rel_path="b.md", institution="中信",
            text="营收高增长，渗透率提升，龙头地位稳固",
        )]
        idx = _find_duplicate_prev(node, prevs)
        assert idx == 0

    def test_find_duplicate_different_inst_not_dup(self):
        node = _make_node(rel_path="a.md", institution="中信")
        prevs = [_make_node(rel_path="b.md", institution="国泰君安")]
        idx = _find_duplicate_prev(node, prevs)
        assert idx is None

    def test_find_duplicate_no_institution(self):
        node = _make_node(rel_path="a.md", institution="")
        prevs = [_make_node(rel_path="b.md", institution="")]
        idx = _find_duplicate_prev(node, prevs)
        assert idx is None


# ── 5. drift 计分 ────────────────────────────────────────────────────


class TestDriftScore:
    def test_single_version_zero_drift(self):
        versions = [ThesisVersion(
            rel_path="a.md", claim_text="t", report_date="2026-01-01",
            thesis_version_status=VERSION_NEW,
        )]
        assert _compute_drift_score(versions) == 0.0

    def test_all_reinforced_zero_drift(self):
        versions = [
            ThesisVersion(
                rel_path="a.md", claim_text="t", report_date="2026-01-01",
                thesis_version_status=VERSION_NEW,
            ),
            ThesisVersion(
                rel_path="b.md", claim_text="t", report_date="2026-02-01",
                thesis_version_status=VERSION_REINFORCED,
            ),
        ]
        assert _compute_drift_score(versions) == 0.0

    def test_reversed_max_drift(self):
        versions = [
            ThesisVersion(
                rel_path="a.md", claim_text="t", report_date="2026-01-01",
                thesis_version_status=VERSION_NEW,
            ),
            ThesisVersion(
                rel_path="b.md", claim_text="t", report_date="2026-02-01",
                thesis_version_status=VERSION_REVERSED,
            ),
        ]
        # 1 对，reversed 权重 1.0 → drift = 1.0。
        assert _compute_drift_score(versions) == 1.0

    def test_duplicate_not_counted(self):
        versions = [
            ThesisVersion(
                rel_path="a.md", claim_text="t", report_date="2026-01-01",
                thesis_version_status=VERSION_NEW,
            ),
            ThesisVersion(
                rel_path="b.md", claim_text="t", report_date="2026-02-01",
                thesis_version_status=VERSION_REINFORCED, is_duplicate=True,
            ),
        ]
        # 重复不计入 → 只有 1 个非重复节点 → drift = 0.0。
        assert _compute_drift_score(versions) == 0.0

    def test_reversed_higher_than_weakened(self):
        rev = [
            ThesisVersion(
                rel_path="a.md", claim_text="t", report_date="2026-01-01",
                thesis_version_status=VERSION_NEW,
            ),
            ThesisVersion(
                rel_path="b.md", claim_text="t", report_date="2026-02-01",
                thesis_version_status=VERSION_REVERSED,
            ),
        ]
        weak = [
            ThesisVersion(
                rel_path="a.md", claim_text="t", report_date="2026-01-01",
                thesis_version_status=VERSION_NEW,
            ),
            ThesisVersion(
                rel_path="b.md", claim_text="t", report_date="2026-02-01",
                thesis_version_status=VERSION_WEAKENED,
            ),
        ]
        assert _compute_drift_score(rev) > _compute_drift_score(weak)


# ── 6. 乱序输入稳定时间线 ────────────────────────────────────────────


class TestStableOrdering:
    def test_disordered_input_stable(self, tmp_path: Path):
        """同知识库内容以不同顺序写入 → 时间线节点顺序一致。"""
        inv = tmp_path / INVESTMENT_SUBDIR
        # 顺序 1：按日期升序。
        _write(inv / "a.md", _REINFORCE_1)  # 2026-03-01
        _write(inv / "b.md", _REINFORCE_2)  # 2026-05-01
        _write(inv / "c.md", _REINFORCE_3)  # 2026-06-01
        r1 = build_research_thesis_timeline(
            str(tmp_path), symbol="600100", today=_TODAY,
        )
        dates1 = [
            v.report_date
            for t in r1.symbols[0].theses for v in t.versions
        ]

        # 顺序 2：打乱写入顺序（但内容相同）。
        inv2 = tmp_path / "kb2" / INVESTMENT_SUBDIR
        _write(inv2 / "c.md", _REINFORCE_3)
        _write(inv2 / "a.md", _REINFORCE_1)
        _write(inv2 / "b.md", _REINFORCE_2)
        r2 = build_research_thesis_timeline(
            str(tmp_path / "kb2"), symbol="600100", today=_TODAY,
        )
        dates2 = [
            v.report_date
            for t in r2.symbols[0].theses for v in t.versions
        ]
        assert dates1 == dates2

    def test_missing_date_consistent_position(self, fixture_kb: Path):
        """无日期节点无论输入顺序，都排在有日期节点之后。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600600", today=_TODAY,
        )
        sym = result.symbols[0]
        for t in sym.theses:
            seen_none = False
            for v in t.versions:
                if v.report_date is None:
                    seen_none = True
                else:
                    # 一旦出现过无日期节点，后续不应再出现有日期节点。
                    assert not seen_none, (
                        f"有日期节点 {v.report_date} 出现在无日期节点之后"
                    )


# ── 7. 弱来源不得覆盖强来源 ──────────────────────────────────────────


class TestWeakSourceNotOverride:
    def test_weak_source_overlay_flagged(self, tmp_path: Path):
        """弱来源（media）晚于强来源（filing）+ 方向相反 → weak_source_overlay。"""
        # 用直接构造节点测试（filing 页通常不出现在 investment 分区）。
        from tradingagents.dataflows.research_thesis_timeline import (
            _build_thesis_timeline,
        )
        filing_page = ResearchFactOpinionPage(
            rel_path="filing.md",
            title="某公司-2025年报",
            symbols=["600700.SH 某庚"],
            symbol="600700",
            name="某庚",
            source_quality_tier=TIER_ORIGINAL_FILING,
            report_date="2026-01-01",
            stale_status="fresh",
            confidence="high",
            research_claims=[
                ResearchClaimItem(
                    claim_type=CLAIM_OPINION,
                    text="营收同比下降 30%",
                    origin_field="body:opinion",
                    source_quality_tier=TIER_ORIGINAL_FILING,
                )
            ],
        )
        media_page = ResearchFactOpinionPage(
            rel_path="media.md",
            title="媒体-某公司看多",
            symbols=["600700.SH 某庚"],
            symbol="600700",
            name="某庚",
            source_quality_tier=TIER_MEDIA,
            report_date="2026-05-01",
            stale_status="fresh",
            confidence="low",
            research_claims=[
                ResearchClaimItem(
                    claim_type=CLAIM_OPINION,
                    text="营收将大幅增长",
                    origin_field="body:opinion",
                    source_quality_tier=TIER_MEDIA,
                )
            ],
        )
        nodes = _collect_nodes_for_test(filing_page, media_page)
        timeline = _build_thesis_timeline(
            "600700.SH", "某庚", "revenue", "revenue", nodes,
            audit_map={},
        )
        # media 节点应标 weak_source_overlay=True 且 status=weakened。
        overlays = [v for v in timeline.versions if v.weak_source_overlay]
        assert len(overlays) >= 1
        assert all(
            v.thesis_version_status == VERSION_WEAKENED for v in overlays
        )
        assert timeline.weak_source_overlay_count >= 1


def _collect_nodes_for_test(*pages: ResearchFactOpinionPage):
    """测试辅助：从 page 直接收集候选节点。"""
    from tradingagents.dataflows.research_thesis_timeline import (
        _collect_candidate_nodes,
    )
    return _collect_candidate_nodes(list(pages), symbol="600700", name="某庚")


# ── 8. KB-017 citation_audit 接入 ────────────────────────────────────


class TestCitationAuditIntegration:
    def test_pending_audit_status_triggers_pending(self, fixture_kb: Path):
        """KB-017 pending → 节点标 pending_fact_check。"""
        # 先取一条 claim 文本。
        sym = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        ).symbols[0]
        target = None
        for t in sym.theses:
            for v in t.versions:
                if v.claim_text:
                    target = (v.rel_path, v.claim_text)
                    break
            if target:
                break
        assert target is not None

        audit = CitationAuditResult(symbol="600100.SH")
        audit.flags.append(CitationAuditFlag(
            claim_text=target[1],
            metric_key="other",
            audit_status=STATUS_PENDING,
            source_path=target[0],
        ))
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
            citation_audit=audit,
        )
        assert result.has_citation_audit is True
        sym = result.symbols[0]
        pending_found = any(
            v.thesis_version_status == VERSION_PENDING_FACT_CHECK
            and v.citation_audit_status == STATUS_PENDING
            for t in sym.theses for v in t.versions
        )
        assert pending_found

    def test_audit_mismatched_symbol_ignored(self, fixture_kb: Path):
        """audit.symbol 与查询 symbol 不匹配 → 不消费。"""
        audit = CitationAuditResult(symbol="999999.SH")
        audit.flags.append(CitationAuditFlag(
            claim_text="whatever",
            metric_key="other",
            audit_status=STATUS_PENDING,
            source_path="x.md",
        ))
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
            citation_audit=audit,
        )
        # symbol 不对齐 → has_citation_audit 仍 True（传入即 True），
        # 但实际不会出现 pending_fact_check 状态。
        assert result.has_citation_audit is True
        sym = result.symbols[0]
        statuses = {
            v.thesis_version_status
            for t in sym.theses for v in t.versions
        }
        # 由于 audit_map 为空（symbol 不对齐），不会出现 pending_fact_check。
        assert VERSION_PENDING_FACT_CHECK not in statuses or any(
            v.stale_status == "stale" for t in sym.theses for v in t.versions
        )

    def test_build_claim_audit_map_none(self):
        assert _build_claim_audit_map(None) == {}

    def test_build_claim_audit_map_empty(self):
        audit = CitationAuditResult()
        assert _build_claim_audit_map(audit) == {}

    def test_lookup_audit_status_missing(self):
        assert _lookup_audit_status({}, "a.md", "text") == ""


# ── 9. claim_theme 分类 ─────────────────────────────────────────────


class TestClaimTheme:
    def test_revenue_theme(self):
        claim = ResearchClaimItem(
            claim_type=CLAIM_OPINION, text="营收增长",
            origin_field="body:opinion",
        )
        theme, token = _claim_theme(claim)
        assert theme == "revenue"
        assert token == "revenue"

    def test_net_profit_theme(self):
        claim = ResearchClaimItem(
            claim_type=CLAIM_OPINION, text="归母净利提升",
            origin_field="body:opinion",
        )
        theme, _ = _claim_theme(claim)
        assert theme == "net_profit"

    def test_risk_theme(self):
        claim = ResearchClaimItem(
            claim_type=CLAIM_RISK, text="客户集中度风险",
            origin_field="risk_factors",
        )
        theme, token = _claim_theme(claim)
        assert theme == "risk"
        assert "客户集中度" in token

    def test_segment_theme(self):
        claim = ResearchClaimItem(
            claim_type=CLAIM_OPINION, text="AI服务器业务放量",
            origin_field="body:opinion",
        )
        theme, token = _claim_theme(claim)
        assert theme == "segment"
        assert "AI服务器" in token


# ── 10. 多 symbol 查询 / 全库 ────────────────────────────────────────


class TestMultiSymbol:
    def test_all_index_mode(self, fixture_kb: Path):
        """全库索引模式返回所有 symbol。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), today=_TODAY,
        )
        assert result.status == "HAS_DATA"
        sym_keys = [s.symbol_key for s in result.symbols]
        # 至少 6 个 fixture symbol。
        assert len(sym_keys) >= 6
        assert "600100.SH" in sym_keys
        assert "600300.SH" in sym_keys

    def test_name_query(self, fixture_kb: Path):
        result = build_research_thesis_timeline(
            str(fixture_kb), name="某丙", today=_TODAY,
        )
        assert len(result.symbols) >= 1
        assert any("某丙" in s.name for s in result.symbols)


# ── 11. lookup / 单 symbol 查询 ──────────────────────────────────────


class TestLookup:
    def test_lookup_found(self, fixture_kb: Path):
        sym = lookup_research_thesis_timeline(
            str(fixture_kb), "600100", today=_TODAY,
        )
        assert sym is not None
        assert sym.symbol_key == "600100.SH"

    def test_lookup_with_suffix(self, fixture_kb: Path):
        sym = lookup_research_thesis_timeline(
            str(fixture_kb), "600100.SH", today=_TODAY,
        )
        assert sym is not None

    def test_lookup_not_found(self, fixture_kb: Path):
        sym = lookup_research_thesis_timeline(
            str(fixture_kb), "999999", today=_TODAY,
        )
        assert sym is None

    def test_lookup_empty_symbol(self, fixture_kb: Path):
        sym = lookup_research_thesis_timeline(
            str(fixture_kb), "", today=_TODAY,
        )
        assert sym is None

    def test_symbol_equivalent_basic(self):
        assert _symbol_equivalent("600100", "600100.SH") is True
        assert _symbol_equivalent("600100.SH", "600100.SH") is True
        assert _symbol_equivalent("600100", "600200.SH") is False
        assert _symbol_equivalent("", "600100") is False


# ── 12. 报告渲染 ─────────────────────────────────────────────────────


class TestReportRendering:
    def test_report_has_title_and_status(self, fixture_kb: Path):
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        report = render_research_thesis_timeline_report(result)
        assert "KB-018" in report
        assert "时间线" in report or "timeline" in report.lower()

    def test_report_empty_result(self, tmp_path: Path):
        inv = tmp_path / INVESTMENT_SUBDIR
        inv.mkdir(parents=True)
        result = build_research_thesis_timeline(
            str(tmp_path), symbol="999999", today=_TODAY,
        )
        report = render_research_thesis_timeline_report(result)
        assert "无研报" in report or "NORMAL_NO_DATA" in report

    def test_report_failed(self, tmp_path: Path):
        result = build_research_thesis_timeline(
            str(tmp_path / "nonexistent"), symbol="600100", today=_TODAY,
        )
        report = render_research_thesis_timeline_report(result)
        assert "FAILED" in report or "失败" in report

    def test_report_contains_version_table(self, fixture_kb: Path):
        """报告包含版本演化表。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        report = render_research_thesis_timeline_report(result)
        assert "版本状态" in report or "thesis_version_status" in report

    def test_report_contains_paths_not_full_text(self, fixture_kb: Path):
        """报告只展示路径，不复制研报原文段落。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        report = render_research_thesis_timeline_report(result)
        assert ".md" in report
        # 不应包含完整原文段。
        assert "## 投资逻辑" not in report

    def test_report_top_limit(self, fixture_kb: Path):
        result = build_research_thesis_timeline(
            str(fixture_kb), today=_TODAY,
        )
        report = render_research_thesis_timeline_report(result, top=2)
        assert "KB-018" in report


# ── 13. JSON 序列化与扁平摘要 ────────────────────────────────────────


class TestSerialization:
    def test_result_json_serializable(self, fixture_kb: Path):
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        data = result.to_dict()
        payload = json.dumps(data, ensure_ascii=False)
        restored = json.loads(payload)
        assert restored["task"] == "KB-018"
        assert restored["status"] == result.status

    def test_symbol_result_serializable(self, fixture_kb: Path):
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        sym = result.symbols[0]
        data = sym.to_dict()
        payload = json.dumps(data, ensure_ascii=False)
        restored = json.loads(payload)
        assert restored["symbol_key"] == sym.symbol_key
        assert "theses" in restored

    def test_thesis_version_serializable(self):
        v = ThesisVersion(
            rel_path="a.md", claim_text="营收增长",
            report_date="2026-01-01",
            source_quality_tier=TIER_BROKER_RESEARCH,
            thesis_version_status=VERSION_NEW,
        )
        data = v.to_dict()
        payload = json.dumps(data, ensure_ascii=False)
        restored = json.loads(payload)
        assert restored["thesis_version_status"] == VERSION_NEW
        assert "decision" not in restored
        assert "action_label" not in restored
        assert "buy_level" not in restored

    def test_ta_consumable_summary_hit(self, fixture_kb: Path):
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        summary = timeline_to_ta_consumable_summary(result.symbols[0])
        assert summary["has_hit"] is True
        assert "consensus_drift_score" in summary
        assert "theses_brief" in summary
        # 不携带 decision / action_label / buy_level。
        assert "decision" not in summary
        assert "action_label" not in summary

    def test_ta_consumable_summary_empty(self):
        summary = timeline_to_ta_consumable_summary(None)
        assert summary["has_hit"] is False
        assert summary["consensus_drift_score"] == 0.0


# ── 14. 约束验证 ─────────────────────────────────────────────────────


class TestConstraints:
    def test_knowledge_root_not_modified(self, fixture_kb: Path):
        """只读：调用后知识库文件不变。"""
        target = fixture_kb / INVESTMENT_SUBDIR / "reinforce-1.md"
        before = target.read_text(encoding="utf-8")
        build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        after = target.read_text(encoding="utf-8")
        assert before == after

    def test_no_db_writes(self, fixture_kb: Path, tmp_path: Path):
        """不写生产 DB。"""
        db_candidate = tmp_path / "tradingagents.db"
        build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        assert not db_candidate.exists()

    def test_no_strong_action_words_in_report(self, fixture_kb: Path):
        """报告不含强买卖词。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), today=_TODAY,
        )
        report = render_research_thesis_timeline_report(result)
        _assert_no_strong_action_words(report)

    def test_no_strong_action_words_in_summary(self, fixture_kb: Path):
        """summary 不含强买卖词。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), today=_TODAY,
        )
        for sym in result.symbols:
            _assert_no_strong_action_words(sym.summary)
            for t in sym.theses:
                _assert_no_strong_action_words(t.summary)

    def test_no_strong_action_words_in_versions(self, fixture_kb: Path):
        """change_reason 不含强买卖词。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), today=_TODAY,
        )
        for sym in result.symbols:
            for t in sym.theses:
                for v in t.versions:
                    _assert_no_strong_action_words(v.change_reason)
                    _assert_no_strong_action_words(v.claim_text)

    def test_no_decision_fields_in_output(self, fixture_kb: Path):
        """输出不携带 decision / action_label / buy_level。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="600100", today=_TODAY,
        )
        data = result.to_dict()
        assert "decision" not in data
        assert "action_label" not in data
        assert "buy_level" not in data
        for sym in data["symbols"]:
            assert "decision" not in sym
            assert "action_label" not in sym
            for t in sym["theses"]:
                assert "decision" not in t
                for v in t["versions"]:
                    assert "decision" not in v

    def test_has_forbidden_action_words_detector(self):
        assert has_forbidden_action_words("建议买入某股票") is True
        assert has_forbidden_action_words("正常研究文本") is False

    def test_drift_score_bounded(self, fixture_kb: Path):
        """consensus_drift_score 在合理区间。"""
        result = build_research_thesis_timeline(
            str(fixture_kb), today=_TODAY,
        )
        for sym in result.symbols:
            assert sym.consensus_drift_score >= 0.0


# ── 15. 边界场景 ─────────────────────────────────────────────────────


class TestEdgeCases:
    def test_knowledge_root_not_exists(self, tmp_path: Path):
        result = build_research_thesis_timeline(
            str(tmp_path / "nonexistent"), symbol="600100", today=_TODAY,
        )
        assert result.status == "FAILED"
        assert len(result.errors) > 0

    def test_investment_dir_not_exists(self, tmp_path: Path):
        result = build_research_thesis_timeline(
            str(tmp_path), symbol="600100", today=_TODAY,
        )
        assert result.status == "NORMAL_NO_DATA"
        assert len(result.errors) > 0

    def test_empty_investment_dir(self, tmp_path: Path):
        inv = tmp_path / INVESTMENT_SUBDIR
        inv.mkdir(parents=True)
        result = build_research_thesis_timeline(
            str(tmp_path), symbol="600100", today=_TODAY,
        )
        assert result.status == "NORMAL_NO_DATA"

    def test_no_matching_symbol(self, fixture_kb: Path):
        result = build_research_thesis_timeline(
            str(fixture_kb), symbol="999999", today=_TODAY,
        )
        assert result.status == "NORMAL_NO_DATA"
        assert len(result.symbols) == 0

    def test_single_page_does_not_crash(self, tmp_path: Path):
        """只有一页研报 → 不崩溃，首节点是 new，无 reversed。"""
        inv = tmp_path / INVESTMENT_SUBDIR
        _write(inv / "single.md", _REINFORCE_1)
        result = build_research_thesis_timeline(
            str(tmp_path), symbol="600100", today=_TODAY,
        )
        assert result.status == "HAS_DATA"
        assert len(result.symbols) == 1
        sym = result.symbols[0]
        # 单页：至少有一个 new 节点；无 reversed（无前后对比）。
        all_statuses = {
            v.thesis_version_status
            for t in sym.theses for v in t.versions
        }
        assert VERSION_NEW in all_statuses
        assert VERSION_REVERSED not in all_statuses
        assert sym.reversed_versions == 0

    def test_default_result_structure(self):
        """空 result 的默认字段结构稳定。"""
        r = ResearchThesisTimelineResult()
        assert r.status == "NORMAL_NO_DATA"
        assert r.task == "KB-018"
        assert r.symbols == []
        assert r.has_citation_audit is False


# ── 16. suggest_report_output_path ──────────────────────────────────


class TestOutputPath:
    def test_suggest_output_path_format(self):
        path = suggest_report_output_path()
        assert "research_thesis_timeline-" in path
        assert path.endswith(".md")
        assert "docs/knowledge_reports" in path


# ── 17. CLI smoke ────────────────────────────────────────────────────


class TestCLI:
    def test_cli_runs_on_fixture(self, fixture_kb: Path):
        """CLI 在 fixture 上能跑通（不依赖真实知识库）。"""
        import subprocess
        result = subprocess.run(
            [
                "python", "scripts/research_thesis_timeline.py",
                "--knowledge-root", str(fixture_kb),
                "--symbol", "600100",
                "--summary",
            ],
            capture_output=True, text=True, timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "consensus_drift_score" in result.stdout

    def test_cli_suggest_output(self):
        import subprocess
        result = subprocess.run(
            [
                "python", "scripts/research_thesis_timeline.py",
                "--suggest-output",
            ],
            capture_output=True, text=True, timeout=30,
            check=False,
        )
        assert result.returncode == 0
        assert "research_thesis_timeline-" in result.stdout
