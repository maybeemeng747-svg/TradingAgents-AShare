# [KB-009] research_attention_decay
"""Tests for 研报来源去重、时效衰减与过热惩罚规则 (KB-009).

覆盖：
  - ``split_institution``：机构名提取（分隔符 / 无分隔符 / 空串）。
  - ``compute_symbol_decay``：
      * 机构级去重（同机构重复 → 扣分；不同机构共识 → 不压制）。
      * 时效衰减（过期弱证据 / 高 stale_risk 衰减更快 / 年龄衰减）。
      * explain 可读、warnings 透出。
  - ``compute_overheat_penalty``：
      * 三信号叠加（overheat_flags / 短期涨幅 / 主题拥挤）。
      * 单一信号不构成清零（不用单日涨幅作为唯一指标）。
      * 阈值边界（恰好等于 / 略超阈值）。
  - ``apply_overheat_penalty`` / ``compute_effective_attention``：链式叠加。
  - ``attention_to_summary``：KB-009 字段透出（命中 / 无命中）。
  - TradeFlow ``_enrich_candidate_with_research_attention`` /
    ``_enrich_candidates_with_research_attention``：
      * 过热降低 effective_score，但不改 KB-007 base score / 强动作门禁。
      * 无命中 NORMAL_NO_DATA；overheat_flags 从候选读取。
  - Pydantic schema 包含 KB-009 字段。
  - 只读安全性（不写知识库）。
  - explain / summary 不含买卖建议或强动作词。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from tradingagents.dataflows.local_knowledge_audit import INVESTMENT_SUBDIR
from tradingagents.dataflows.research_attention import (
    PageMention,
    SymbolAttention,
    attention_to_summary,
    lookup_research_attention,
)
from tradingagents.dataflows.research_attention_decay import (
    CONTRACT_VERSION,
    TASK_CODE,
    SymbolDecayResult,
    apply_overheat_penalty,
    compute_effective_attention,
    compute_overheat_penalty,
    compute_symbol_decay,
    decay_to_summary,
    page_time_decay_factor,
    render_decay_summary_inline,
    split_institution,
)
from tradingagents.dataflows.local_knowledge_provider import STATUS_NORMAL_NO_DATA


# ── 工具：构造 fixture 知识库 ─────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 同机构重复 fixture：两篇 fresh 研报都引用"中信证券"，第三篇引用"国泰君安"。
# 用来验证"同机构重复只计一次主权重，不同机构共识不压制"。
_DUP_INST_PAGE_A = """---
title: 标的A-中信证券点评1
created: 2026-06-01
updated: 2026-06-29
sources:
  - "[[../../raw/2026-06-01-中信证券-标的A.md|中信证券-标的A]]"
tags: [标的A]
symbols: ["600000.SH 标的A"]
themes: [消费, 白酒]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 标的A
"""

_DUP_INST_PAGE_B = """---
title: 标的A-中信证券点评2
created: 2026-06-10
updated: 2026-06-30
sources:
  - "[[../../raw/2026-06-10-中信证券-标的A跟踪.md|中信证券-标的A跟踪]]"
tags: [标的A]
symbols: ["600000.SH 标的A"]
themes: [消费, 食品]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 标的A跟踪
"""

_DUP_INST_PAGE_C = """---
title: 标的A-国泰君安深度
created: 2026-06-15
updated: 2026-06-30
sources:
  - "[[../../raw/2026-06-15-国泰君安-标的A深度.md|国泰君安-标的A深度]]"
tags: [标的A]
symbols: ["600000.SH 标的A"]
themes: [消费, 白酒, 食品]
report_type: 深度
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 标的A深度
"""

# 不同机构共识 fixture：3 家不同机构各一篇（无重复）。
_CONSENSUS_PAGE_X = """---
title: 标的B-中金点评
created: 2026-06-01
updated: 2026-06-29
sources:
  - "中金-标的B"
tags: [标的B]
symbols: ["600001.SH 标的B"]
themes: [新能源]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 标的B
"""

_CONSENSUS_PAGE_Y = """---
title: 标的B-华泰点评
created: 2026-06-02
updated: 2026-06-30
sources:
  - "华泰-标的B"
symbols: ["600001.SH 标的B"]
themes: [新能源, 储能]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 标的B华泰
"""

_CONSENSUS_PAGE_Z = """---
title: 标的B-招商点评
created: 2026-06-03
updated: 2026-06-30
sources:
  - "招商证券-标的B"
symbols: ["600001.SH 标的B"]
themes: [新能源]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 高
stale_risk: 低
---

# 标的B招商
"""

# 高 stale_risk 页面（未过期，用于验证"高 stale_risk 衰减更快"）。
_HIGH_STALE_PAGE = """---
title: 标的C-高stale点评
created: 2026-03-01
updated: 2026-04-01
sources:
  - "中信建投-标的C"
symbols: ["600002.SH 标的C"]
themes: [周期]
report_type: 公司点评
evidence_level: A
valid_until: 2099-12-31
source_quality: 中
stale_risk: 高
---

# 标的C
"""


@pytest.fixture()
def fixture_kb(tmp_path: Path) -> Path:
    inv = tmp_path / INVESTMENT_SUBDIR
    _write(inv / "标的A-中信1.md", _DUP_INST_PAGE_A)
    _write(inv / "标的A-中信2.md", _DUP_INST_PAGE_B)
    _write(inv / "标的A-国泰君安.md", _DUP_INST_PAGE_C)
    _write(inv / "标的B-中金.md", _CONSENSUS_PAGE_X)
    _write(inv / "标的B-华泰.md", _CONSENSUS_PAGE_Y)
    _write(inv / "标的B-招商.md", _CONSENSUS_PAGE_Z)
    _write(inv / "标的C-高stale.md", _HIGH_STALE_PAGE)
    return tmp_path


@pytest.fixture()
def empty_kb(tmp_path: Path) -> Path:
    (tmp_path / INVESTMENT_SUBDIR).mkdir(parents=True)
    return tmp_path


# ── 1. split_institution ─────────────────────────────────────────────


class TestSplitInstitution:
    def test_hyphen_separator(self):
        assert split_institution("中邮证券-华勤技术超节点") == "中邮证券"

    def test_em_dash_separator(self):
        assert split_institution("国泰君安—华勤技术深度") == "国泰君安"

    def test_colon_separator(self):
        assert split_institution("中金：腾讯") == "中金"

    def test_ascii_colon(self):
        assert split_institution("Bernstein:Dell") == "Bernstein"

    def test_ascii_hyphen(self):
        assert split_institution("Bernstein-Dell") == "Bernstein"

    def test_no_separator_returns_whole(self):
        # 无分隔符 → 整串作为独立来源 key（不折叠）。
        assert split_institution("星球社群截图") == "星球社群截图"

    def test_empty_returns_empty(self):
        assert split_institution("") == ""

    def test_strips_whitespace(self):
        assert split_institution("  中信证券 - 标的A  ") == "中信证券"


# ── 2. compute_symbol_decay — 机构级去重 ─────────────────────────────


class TestInstitutionDedup:
    def test_same_institution_duplicate_penalized(self, fixture_kb: Path):
        """同机构（中信证券）在两篇 fresh 页重复 → 扣分，effective < base。"""
        sym = lookup_research_attention(str(fixture_kb), "600000")
        assert sym is not None
        decay = compute_symbol_decay(sym)
        # 3 篇 fresh，其中中信证券重复 1 次 → duplicate_institution_count=1。
        assert decay.fresh_mention_count == 3
        assert decay.unique_institution_count == 2  # 中信证券 + 国泰君安
        assert decay.duplicate_institution_count == 1
        assert decay.dedup_penalty > 0.0
        assert decay.effective_score < sym.research_attention_score
        # explain 必须可读且含"机构级去重"。
        joined = " ".join(decay.explain)
        assert "机构级去重" in joined

    def test_different_institutions_consensus_not_suppressed(self, fixture_kb: Path):
        """3 家不同机构各一篇 → 不扣分，effective ≈ base（无去重惩罚）。"""
        sym = lookup_research_attention(str(fixture_kb), "600001")
        assert sym is not None
        decay = compute_symbol_decay(sym)
        assert decay.fresh_mention_count == 3
        assert decay.unique_institution_count == 3
        assert decay.duplicate_institution_count == 0
        assert decay.dedup_penalty == 0.0
        # 无过热时 effective 应接近 base（仅可能有微小时效衰减）。
        assert decay.effective_score <= sym.research_attention_score
        assert decay.effective_score >= sym.research_attention_score * 0.99

    def test_no_fresh_pages_no_dedup(self, empty_kb: Path):
        """无命中 → factor 默认 1.0，无扣分。"""
        from tradingagents.dataflows.research_attention import SymbolAttention

        sym = SymbolAttention(symbol_key="X", bare_code="", name="X", asset_class="OTHER")
        decay = compute_symbol_decay(sym)
        assert decay.fresh_mention_count == 0
        assert decay.dedup_penalty == 0.0
        assert decay.time_decay_factor == 1.0
        assert decay.effective_score == 0.0


# ── 3. compute_symbol_decay — 时效衰减 ───────────────────────────────


class TestTimeDecay:
    def test_expired_page_weak_evidence(self):
        """valid_until 过期 → 弱证据因子（0.3 / 0.5）。"""
        # 高 stale 过期页
        p_high = PageMention(
            rel_path="a", title="a", page_type="company", is_stale=True,
            updated_at="2020-01-01",
        )
        # 低 stale 过期页
        p_low = PageMention(
            rel_path="b", title="b", page_type="company", is_stale=False,
            updated_at="2020-01-01",
        )
        assert page_time_decay_factor(p_high, True) == 0.3
        assert page_time_decay_factor(p_low, True) == 0.5

    def test_high_stale_risk_decays_faster(self):
        """未过期但高 stale_risk → 随年龄更快衰减到 0.6 地板。"""
        today = date(2026, 7, 1)
        # 100 天前更新，高 stale → 应低于低 stale 同龄页。
        old_date = (today - timedelta(days=100)).strftime("%Y-%m-%d")
        p_high = PageMention(
            rel_path="h", title="h", page_type="company", is_stale=True,
            updated_at=old_date,
        )
        p_low = PageMention(
            rel_path="l", title="l", page_type="company", is_stale=False,
            updated_at=old_date,
        )
        # 未过期分支（valid_until_expired=False）。
        f_high = page_time_decay_factor(p_high, False, today)
        f_low = page_time_decay_factor(p_low, False, today)
        assert f_high < f_low
        assert f_high >= 0.6  # 地板
        assert f_low >= 0.8

    def test_no_updated_no_decay(self):
        """无 updated 字段 → 视作无年龄衰减（因子 1.0）。"""
        p = PageMention(
            rel_path="x", title="x", page_type="company", is_stale=False,
            updated_at=None,
        )
        assert page_time_decay_factor(p, False) == 1.0

    def test_recent_page_full_factor(self):
        """近期更新的低 stale 页 → 因子接近 1.0。"""
        today = date(2026, 7, 1)
        recent = (today - timedelta(days=2)).strftime("%Y-%m-%d")
        p = PageMention(
            rel_path="r", title="r", page_type="company", is_stale=False,
            updated_at=recent,
        )
        assert page_time_decay_factor(p, False, today) > 0.99


# ── 4. compute_overheat_penalty ──────────────────────────────────────


class TestOverheatPenalty:
    def test_three_signals_combine(self):
        penalty, explain, active = compute_overheat_penalty(
            overheat_flags=["短期过热", "量能异常"],
            short_term_gain_pct=30.0,
            theme_count=8,
        )
        # flags=2×0.5 + gain((30-20)/10×0.3=0.3) + crowd((8-6)×0.4=0.8) = 2.1
        assert penalty == pytest.approx(2.1, abs=0.001)
        assert len(active) == 3
        assert any("overheat_flags" in a for a in active)
        assert any("short_term_gain" in a for a in active)
        assert any("theme_crowding" in a for a in active)
        # explain 可读。
        assert all("=" in e for e in explain)

    def test_single_gain_signal_does_not_zero_out(self):
        """任务约束：不用单日价格涨幅作为唯一过热指标 → 单一涨幅仍能触发，但不会清零分数。"""
        penalty, explain, active = compute_overheat_penalty(short_term_gain_pct=50.0)
        assert penalty > 0.0
        assert len(active) == 1
        # 涨幅 50%：超出 30pp，(30/10)×0.3 = 0.9。
        assert penalty == pytest.approx(0.9, abs=0.001)

    def test_no_signals_zero_penalty(self):
        penalty, explain, active = compute_overheat_penalty()
        assert penalty == 0.0
        assert active == []
        assert explain == []

    def test_gain_below_threshold_no_penalty(self):
        """涨幅未超阈值 → 不触发。"""
        penalty, _, active = compute_overheat_penalty(short_term_gain_pct=20.0)
        assert penalty == 0.0
        assert active == []

    def test_theme_at_crowding_threshold_no_penalty(self):
        """主题数恰好等于拥挤阈值（6）→ 不触发。"""
        penalty, _, active = compute_overheat_penalty(theme_count=6)
        assert penalty == 0.0
        assert active == []

    def test_theme_above_threshold_triggers(self):
        penalty, _, active = compute_overheat_penalty(theme_count=7)
        assert penalty > 0.0
        assert any("theme_crowding" in a for a in active)

    def test_empty_flags_ignored(self):
        penalty, _, _ = compute_overheat_penalty(overheat_flags=["", "  "])
        assert penalty == 0.0


# ── 5. apply_overheat_penalty / compute_effective_attention ───────────


class TestEffectiveAttention:
    def test_overheat_reduces_effective_below_decay(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "600000")
        decay = compute_symbol_decay(sym)
        decay_adjusted = decay.decay_adjusted_score
        final = apply_overheat_penalty(
            decay, overheat_flags=["短期过热"], short_term_gain_pct=30.0, theme_count=8
        )
        assert final.overheat_penalty > 0.0
        assert final.effective_score < decay_adjusted
        assert final.effective_score >= 0.0

    def test_no_overheat_effective_equals_decay(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "600001")
        final = compute_effective_attention(sym)  # 无过热参数
        assert final.overheat_penalty == 0.0
        assert final.effective_score == final.decay_adjusted_score

    def test_effective_never_negative(self, fixture_kb: Path):
        """极端过热 → effective 不会变成负数（max(0, ...)）。"""
        sym = lookup_research_attention(str(fixture_kb), "600000")
        final = compute_effective_attention(
            sym,
            overheat_flags=["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"],
            short_term_gain_pct=999.0,
            theme_count=50,
        )
        assert final.effective_score >= 0.0


# ── 6. attention_to_summary KB-009 字段 ──────────────────────────────


class TestAttentionSummaryKB009:
    def test_hit_summary_has_kb009_fields(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "600000")
        s = attention_to_summary(sym)
        for key in (
            "research_attention_effective_score",
            "research_attention_base_score",
            "research_attention_dedup_penalty",
            "research_attention_time_decay_factor",
            "research_attention_overheat_penalty",
            "research_attention_unique_institution_count",
            "research_attention_duplicate_institution_count",
            "research_attention_decay_explain",
            "research_attention_warnings",
        ):
            assert key in s, f"missing KB-009 key: {key}"
        # KB-007 base 字段保持不变（回归保护）。
        assert "research_attention_score" in s
        assert "score_explain" in s

    def test_no_hit_summary_has_kb009_empty_fields(self):
        s = attention_to_summary(None)
        assert s["research_attention_effective_score"] == 0.0
        assert s["research_attention_time_decay_factor"] == 1.0
        assert s["research_attention_decay_explain"] == []
        assert s["has_hit"] is False

    def test_kb007_base_score_unchanged(self, fixture_kb: Path):
        """KB-009 不修改 KB-007 base score（透明、回归稳定）。"""
        sym = lookup_research_attention(str(fixture_kb), "600000")
        s = attention_to_summary(sym)
        assert s["research_attention_score"] == s["research_attention_base_score"]


# ── 7. decay_to_summary / render_decay_summary_inline ─────────────────


class TestDecaySummary:
    def test_decay_to_summary_structure(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "600000")
        decay = compute_symbol_decay(sym)
        d = decay_to_summary(decay)
        assert d["research_attention_effective_score"] >= 0.0
        assert "research_attention_decay_explain" in d
        assert "research_attention_warnings" in d

    def test_decay_to_summary_none(self):
        d = decay_to_summary(None)
        assert d["research_attention_effective_score"] == 0.0
        assert d["research_attention_time_decay_factor"] == 1.0

    def test_render_inline_no_strong_action_words(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "600000")
        decay = compute_effective_attention(
            sym, overheat_flags=["短期过热"], short_term_gain_pct=25.0
        )
        text = render_decay_summary_inline(decay)
        assert text != ""
        for forbidden in ("买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL"):
            assert forbidden not in text

    def test_render_inline_none_empty(self):
        assert render_decay_summary_inline(None) == ""


# ── 8. TradeFlow candidate enrichment（KB-009 过热叠加）──────────────


class TestTradeFlowEnrichmentKB009:
    def test_overheat_reduces_effective_not_base(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item_no_heat = {"symbol": "600000.SH", "name": "标的A"}
        item_heat = {
            "symbol": "600000.SH",
            "name": "标的A",
            "overheat_flags": ["短期过热", "量能异常"],
            "short_term_gain_pct": 30.0,
        }
        r0 = _enrich_candidate_with_research_attention(dict(item_no_heat))
        r1 = _enrich_candidate_with_research_attention(dict(item_heat))
        # KB-007 base score 不受 overheat 影响（透明）。
        assert r0["research_attention_score"] == r1["research_attention_score"]
        # 过热时 effective 更低。
        assert r1["research_attention_effective_score"] < r0["research_attention_effective_score"]
        assert r1["research_attention_overheat_penalty"] > 0.0
        assert r0["research_attention_overheat_penalty"] == 0.0

    def test_overheat_does_not_change_strong_action_gate(
        self, fixture_kb: Path, monkeypatch
    ):
        """过热只降低研究优先级，不改强动作门禁 / tier / decision。"""
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = {
            "symbol": "600000.SH",
            "name": "标的A",
            "tier": "watch",
            "action": "OBSERVE",
            "overheat_flags": ["短期过热"],
            "short_term_gain_pct": 40.0,
        }
        r = _enrich_candidate_with_research_attention(item)
        assert r["tier"] == "watch"
        assert r["action"] == "OBSERVE"
        assert r["research_attention_overheat_penalty"] > 0.0

    def test_no_hit_effective_zero(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        r = _enrich_candidate_with_research_attention(
            {"symbol": "999999.SH", "overheat_flags": ["x"]}
        )
        assert r["research_attention_effective_score"] == 0.0
        assert r["research_attention_score"] == 0.0

    def test_empty_symbol_has_kb009_defaults(self, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )

        r = _enrich_candidate_with_research_attention({"symbol": ""})
        assert r["research_attention_effective_score"] == 0.0
        assert r["research_attention_overheat_penalty"] == 0.0

    def test_detail_contains_decay_explain(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        r = _enrich_candidate_with_research_attention(
            {"symbol": "600000.SH", "overheat_flags": ["短期过热"]}
        )
        detail = r["research_attention_detail"]
        assert "research_attention_decay_explain" in detail
        assert "research_attention_overheat_flags" in detail
        # explain 非空（有机构去重或过热）。
        assert len(detail["research_attention_decay_explain"]) > 0


class TestTradeFlowBatchEnrichmentKB009:
    def test_batch_applies_overheat_per_item(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        items = [
            {"symbol": "600000.SH", "overheat_flags": ["短期过热"]},
            {"symbol": "600001.SH"},  # 无过热
            {"symbol": "999999.SH"},  # 无命中
        ]
        result = _enrich_candidates_with_research_attention(items)
        eff = [it["research_attention_effective_score"] for it in result]
        assert eff[2] == 0.0
        # 共识标的（600001，不同机构）无过热 → effective 接近 base。
        assert result[1]["research_attention_overheat_penalty"] == 0.0
        # 过热标的 effective 受惩罚。
        assert result[0]["research_attention_overheat_penalty"] > 0.0

    def test_batch_shared_scan_consistent(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidates_with_research_attention,
        )

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        items = [{"symbol": "600000.SH"}, {"symbol": "600000"}]
        result = _enrich_candidates_with_research_attention(items)
        # 两种写法命中同一标的 → effective 一致。
        assert result[0]["research_attention_effective_score"] == result[1][
            "research_attention_effective_score"
        ]


# ── 9. Pydantic schema ───────────────────────────────────────────────


class TestTradeFlowSchemaKB009:
    def test_candidate_item_has_kb009_fields(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem

        fields = TradeFlowCandidateItem.model_fields
        assert "research_attention_effective_score" in fields
        assert "research_attention_overheat_penalty" in fields

    def test_candidate_item_default_values(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem

        item = TradeFlowCandidateItem(symbol="600000.SH")
        assert item.research_attention_effective_score == 0.0
        assert item.research_attention_overheat_penalty == 0.0

    def test_candidate_item_accepts_kb009_payload(self, fixture_kb: Path, monkeypatch):
        from api.services.tradeflow_service import (
            _enrich_candidate_with_research_attention,
        )
        from api.tradeflow_schemas import TradeFlowCandidateItem

        monkeypatch.setenv("AUTO_DEV_KNOWLEDGE_ROOT", str(fixture_kb))
        item = _enrich_candidate_with_research_attention(
            {"symbol": "600000.SH", "overheat_flags": ["短期过热"]}
        )
        schema_item = TradeFlowCandidateItem(**item)
        assert schema_item.research_attention_effective_score >= 0.0


# ── 10. 只读安全性 ───────────────────────────────────────────────────


class TestReadOnlySafety:
    def test_decay_does_not_write_to_knowledge_base(self, fixture_kb: Path):
        files = list((fixture_kb / INVESTMENT_SUBDIR).rglob("*.md"))
        assert files
        before = {p: (p.stat().st_mtime, p.read_bytes()) for p in files}

        sym = lookup_research_attention(str(fixture_kb), "600000")
        compute_symbol_decay(sym)
        compute_effective_attention(
            sym, overheat_flags=["x"], short_term_gain_pct=30.0, theme_count=8
        )

        for p in files:
            mt, content = before[p]
            assert p.stat().st_mtime == mt, f"mtime changed: {p}"
            assert p.read_bytes() == content, f"content changed: {p}"


# ── 11. explain 可读 + 不含强动作词 ──────────────────────────────────


class TestExplainReadable:
    def test_explain_lines_are_strings(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "600000")
        decay = compute_effective_attention(
            sym, overheat_flags=["短期过热"], short_term_gain_pct=25.0
        )
        for line in decay.explain:
            assert isinstance(line, str)
            assert line != ""

    def test_explain_no_strong_action_words(self, fixture_kb: Path):
        sym = lookup_research_attention(str(fixture_kb), "600000")
        decay = compute_effective_attention(
            sym, overheat_flags=["短期过热"], short_term_gain_pct=25.0
        )
        blob = " ".join(decay.explain) + " ".join(decay.warnings)
        for forbidden in ("买入", "卖出", "加仓", "减仓", "强烈推荐", "BUY", "SELL"):
            assert forbidden not in blob
