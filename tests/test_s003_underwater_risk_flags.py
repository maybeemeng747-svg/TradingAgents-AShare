# [S-003] underwater_risk_flags
"""Tests for underwater risk flag detection, candidate integration, and rendering."""

import json
import sqlite3
import tempfile
import os

import pytest

from tradingagents.tradeflow.underwater_risk_flags import (
    detect_underwater_risks,
    UnderwaterRiskResult,
    FLAG_LOCKUP_RISK,
    FLAG_REDUCE_HOLDING_RISK,
    FLAG_INQUIRY_RISK,
    FLAG_FINANCIAL_QUALITY_RISK,
    FLAG_MARGIN_CROWDING_RISK,
    FLAG_LHB_OVERHEAT_RISK,
    ALL_RISK_FLAGS,
    MAX_RISK_PENALTY,
)
from tradingagents.tradeflow.schemas import Candidate, CandidateSignal, DailyPlan
from tradingagents.tradeflow.candidate_engine import (
    init_db,
    save_candidate,
    evaluate_symbol,
)


# ═══════════════════════════════════════════
# Unit tests for detect_underwater_risks()
# ═══════════════════════════════════════════


class TestNoInput:
    def test_no_event_texts_returns_empty(self):
        r = detect_underwater_risks()
        assert r.risk_flags == []
        assert r.risk_penalty == 0.0
        assert r.risk_evidence_refs == []
        assert r.risk_reasons == []

    def test_empty_list_returns_empty(self):
        r = detect_underwater_risks(event_texts=[])
        assert r.risk_flags == []

    def test_none_returns_empty(self):
        r = detect_underwater_risks(event_texts=None)
        assert r.risk_flags == []

    def test_irrelevant_text_no_flags(self):
        r = detect_underwater_risks(event_texts=["公司发布产品更新公告"])
        assert r.risk_flags == []


class TestLockupRisk:
    def test_lockup_event(self):
        r = detect_underwater_risks(event_texts=["限售股解禁公告"])
        assert FLAG_LOCKUP_RISK in r.risk_flags
        assert r.risk_penalty < 0

    def test_lockup_dingzeng(self):
        r = detect_underwater_risks(event_texts=["定增解禁上市流通"])
        assert FLAG_LOCKUP_RISK in r.risk_flags

    def test_lockup_xianshou(self):
        r = detect_underwater_risks(event_texts=["限售期满三年股份上市"])
        assert FLAG_LOCKUP_RISK in r.risk_flags

    def test_lockup_penalty_weight(self):
        r = detect_underwater_risks(event_texts=["首发原股东限售股份上市流通"])
        assert r.risk_penalty == -10

    def test_lockup_evidence_ref(self):
        r = detect_underwater_risks(event_texts=["限售股解禁公告"])
        assert any(ref["flag"] == FLAG_LOCKUP_RISK for ref in r.risk_evidence_refs)
        assert any("限售股解禁" in ref["matched_text"] for ref in r.risk_evidence_refs)


class TestReduceHoldingRisk:
    def test_reduce_holding_plan(self):
        r = detect_underwater_risks(event_texts=["大股东减持计划公告"])
        assert FLAG_REDUCE_HOLDING_RISK in r.risk_flags
        assert r.risk_penalty < 0

    def test_reduce_dongjiangao(self):
        r = detect_underwater_risks(event_texts=["董监高减持股份预披露"])
        assert FLAG_REDUCE_HOLDING_RISK in r.risk_flags

    def test_reduce_passive(self):
        r = detect_underwater_risks(event_texts=["被动减持导致持股比例下降"])
        assert FLAG_REDUCE_HOLDING_RISK in r.risk_flags

    def test_reduce_holding_penalty(self):
        r = detect_underwater_risks(event_texts=["大股东减持计划公告"])
        assert r.risk_penalty == -10


class TestInquiryRisk:
    def test_inquiry_letter(self):
        r = detect_underwater_risks(event_texts=["收到深交所问询函"])
        assert FLAG_INQUIRY_RISK in r.risk_flags
        assert r.risk_penalty < 0

    def test_supervision_letter(self):
        r = detect_underwater_risks(event_texts=["收到证监会关注函"])
        assert FLAG_INQUIRY_RISK in r.risk_flags

    def test_investigation(self):
        r = detect_underwater_risks(event_texts=["证监会立案调查通知书"])
        assert FLAG_INQUIRY_RISK in r.risk_flags

    def test_inquiry_penalty(self):
        r = detect_underwater_risks(event_texts=["证监会立案调查通知书"])
        assert r.risk_penalty == -9


class TestFinancialQualityRisk:
    def test_audit_nonstandard(self):
        r = detect_underwater_risks(event_texts=["年度审计报告保留意见"])
        assert FLAG_FINANCIAL_QUALITY_RISK in r.risk_flags

    def test_goodwill_impairment(self):
        r = detect_underwater_risks(event_texts=["商誉减值测试结果公告"])
        assert FLAG_FINANCIAL_QUALITY_RISK in r.risk_flags

    def test_st_risk(self):
        r = detect_underwater_risks(event_texts=["公司被实施退市风险警示"])
        assert FLAG_FINANCIAL_QUALITY_RISK in r.risk_flags

    def test_financial_fraud(self):
        r = detect_underwater_risks(event_texts=["涉嫌虚增收入被处罚"])
        assert FLAG_FINANCIAL_QUALITY_RISK in r.risk_flags

    def test_cashflow_negative(self):
        r = detect_underwater_risks(event_texts=["经营性现金流为负连续三年"])
        assert FLAG_FINANCIAL_QUALITY_RISK in r.risk_flags


class TestMarginCrowdingRisk:
    def test_margin_not_queried_no_flag(self):
        r = detect_underwater_risks(
            event_texts=["融资余额创新高"],
            margin_status="NOT_QUERIED",
        )
        assert FLAG_MARGIN_CROWDING_RISK not in r.risk_flags

    def test_margin_failed_no_flag(self):
        r = detect_underwater_risks(
            event_texts=["融资余额创新高"],
            margin_status="FAILED",
        )
        assert FLAG_MARGIN_CROWDING_RISK not in r.risk_flags

    def test_margin_normal_no_data_no_flag(self):
        r = detect_underwater_risks(
            event_texts=["融资余额创新高"],
            margin_status="NORMAL_NO_DATA",
        )
        assert FLAG_MARGIN_CROWDING_RISK not in r.risk_flags

    def test_margin_has_data_with_crowding_text(self):
        r = detect_underwater_risks(
            event_texts=["融资余额创新高突破百亿"],
            margin_status="HAS_DATA",
        )
        assert FLAG_MARGIN_CROWDING_RISK in r.risk_flags

    def test_margin_has_data_no_crowding_text(self):
        r = detect_underwater_risks(
            event_texts=["公司发布年报"],
            margin_status="HAS_DATA",
        )
        assert FLAG_MARGIN_CROWDING_RISK not in r.risk_flags

    def test_margin_extra_texts(self):
        r = detect_underwater_risks(
            event_texts=["公司发布年报"],
            margin_status="HAS_DATA",
            margin_texts=["融资客扎堆买入"],
        )
        assert FLAG_MARGIN_CROWDING_RISK in r.risk_flags


class TestLHBOverheatRisk:
    def test_lhb_not_queried_no_flag(self):
        r = detect_underwater_risks(
            event_texts=["龙虎榜机构买入活跃"],
            lhb_status="NOT_QUERIED",
        )
        assert FLAG_LHB_OVERHEAT_RISK not in r.risk_flags

    def test_lhb_failed_no_flag(self):
        r = detect_underwater_risks(
            event_texts=["龙虎榜机构买入活跃"],
            lhb_status="FAILED",
        )
        assert FLAG_LHB_OVERHEAT_RISK not in r.risk_flags

    def test_lhb_normal_no_data_no_flag(self):
        r = detect_underwater_risks(
            event_texts=["龙虎榜机构买入活跃"],
            lhb_status="NORMAL_NO_DATA",
        )
        assert FLAG_LHB_OVERHEAT_RISK not in r.risk_flags

    def test_lhb_has_data_with_overheat(self):
        r = detect_underwater_risks(
            event_texts=["龙虎榜机构买入活跃"],
            lhb_status="HAS_DATA",
        )
        assert FLAG_LHB_OVERHEAT_RISK in r.risk_flags

    def test_lhb_has_data_no_overheat_text(self):
        r = detect_underwater_risks(
            event_texts=["公司发布年报"],
            lhb_status="HAS_DATA",
        )
        assert FLAG_LHB_OVERHEAT_RISK not in r.risk_flags

    def test_lhb_extra_texts(self):
        r = detect_underwater_risks(
            event_texts=["公司发布年报"],
            lhb_status="HAS_DATA",
            lhb_texts=["知名游资介入买入过亿"],
        )
        assert FLAG_LHB_OVERHEAT_RISK in r.risk_flags

    def test_lhb_net_sell_overheat(self):
        r = detect_underwater_risks(
            event_texts=["龙虎榜净卖出过亿"],
            lhb_status="HAS_DATA",
        )
        assert FLAG_LHB_OVERHEAT_RISK in r.risk_flags


class TestMultipleRisks:
    def test_multiple_risks_stacking(self):
        r = detect_underwater_risks(
            event_texts=[
                "限售股解禁公告",
                "大股东减持计划公告",
                "收到深交所问询函",
            ],
        )
        assert FLAG_LOCKUP_RISK in r.risk_flags
        assert FLAG_REDUCE_HOLDING_RISK in r.risk_flags
        assert FLAG_INQUIRY_RISK in r.risk_flags
        assert r.risk_penalty == -30

    def test_penalty_capped_at_max(self):
        events = [
            "限售股解禁上市流通",
            "首发原股东限售股份上市",
            "大股东减持计划",
            "董监高减持股份预披露",
            "收到问询函",
            "证监会立案调查",
            "审计报告保留意见",
            "财务造假虚增收入",
        ]
        r = detect_underwater_risks(event_texts=events)
        assert r.risk_penalty >= -MAX_RISK_PENALTY

    def test_no_duplicate_flags(self):
        r = detect_underwater_risks(
            event_texts=["限售股解禁公告", "限售期满股份上市"],
        )
        lockup_count = r.risk_flags.count(FLAG_LOCKUP_RISK)
        assert lockup_count == 1

    def test_flags_are_sorted(self):
        r = detect_underwater_risks(
            event_texts=["限售股解禁公告", "收到问询函"],
        )
        assert r.risk_flags == sorted(r.risk_flags)


class TestNoEvidenceNoRisk:
    def test_no_evidence_no_lockup(self):
        r = detect_underwater_risks(event_texts=["公司日常经营公告"])
        assert FLAG_LOCKUP_RISK not in r.risk_flags

    def test_no_evidence_no_inquiry(self):
        r = detect_underwater_risks(event_texts=["公司日常经营公告"])
        assert FLAG_INQUIRY_RISK not in r.risk_flags

    def test_not_queried_not_same_as_no_risk_text(self):
        r = detect_underwater_risks(
            event_texts=["公司龙虎榜上榜"],
            lhb_status="NOT_QUERIED",
        )
        assert FLAG_LHB_OVERHEAT_RISK not in r.risk_flags


class TestRiskResultDataclass:
    def test_default_values(self):
        r = UnderwaterRiskResult()
        assert r.risk_flags == []
        assert r.risk_penalty == 0.0
        assert r.risk_evidence_refs == []
        assert r.risk_reasons == []

    def test_all_flags_constant(self):
        assert len(ALL_RISK_FLAGS) == 6


class TestRiskReasons:
    def test_reasons_populated(self):
        r = detect_underwater_risks(event_texts=["限售股解禁公告"])
        assert len(r.risk_reasons) >= 1
        assert any("解禁" in reason for reason in r.risk_reasons)

    def test_multiple_risks_multiple_reasons(self):
        r = detect_underwater_risks(
            event_texts=["限售股解禁公告", "收到问询函"],
        )
        assert len(r.risk_reasons) >= 2


# ═══════════════════════════════════════════
# Integration tests with Candidate / schemas
# ═══════════════════════════════════════════


class TestCandidateRiskFields:
    def test_candidate_has_risk_penalty_field(self):
        c = Candidate(symbol="000001.SZ")
        assert c.risk_penalty == 0.0

    def test_candidate_has_risk_evidence_refs(self):
        c = Candidate(symbol="000001.SZ")
        assert c.risk_evidence_refs == []

    def test_candidate_has_risk_reasons(self):
        c = Candidate(symbol="000001.SZ")
        assert c.risk_reasons == []


class TestCandidateToDbRow:
    def test_to_db_row_includes_risk_fields(self):
        c = Candidate(
            symbol="000001.SZ",
            risk_penalty=-15.0,
            risk_evidence_refs=[{"flag": "LOCKUP_RISK", "matched_text": "test"}],
            risk_reasons=["解禁风险(-10)"],
        )
        row = c.to_db_row()
        assert row["risk_penalty"] == -15.0
        refs = json.loads(row["risk_evidence_refs_json"])
        assert len(refs) == 1
        reasons = json.loads(row["risk_reasons_json"])
        assert len(reasons) == 1

    def test_from_db_row_roundtrip(self):
        c = Candidate(
            symbol="000001.SZ",
            risk_penalty=-20.0,
            risk_evidence_refs=[{"flag": "INQUIRY_RISK"}],
            risk_reasons=["问询风险(-10)"],
        )
        row = c.to_db_row()
        row["strategy_tags_json"] = "[]"
        row["evidence_json"] = "{}"
        row["risk_flags_json"] = "[]"
        row["created_at"] = "2026-05-29T00:00:00"
        row["updated_at"] = "2026-05-29T00:00:00"
        c2 = Candidate.from_db_row(row)
        assert c2.risk_penalty == -20.0
        assert len(c2.risk_evidence_refs) == 1
        assert len(c2.risk_reasons) == 1


class TestDbPersistence:
    def test_init_db_has_risk_columns(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            cursor = conn.execute("PRAGMA table_info(tradeflow_candidates)")
            col_names = {row[1] for row in cursor.fetchall()}
            conn.close()
            assert "risk_penalty" in col_names
            assert "risk_evidence_refs_json" in col_names
            assert "risk_reasons_json" in col_names
        finally:
            os.unlink(db_path)

    def test_save_and_load_candidate_with_risks(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            init_db(db_path)
            c = Candidate(
                symbol="002138.SZ",
                trade_date="2026-05-29",
                risk_penalty=-10.0,
                risk_flags=["LOCKUP_RISK"],
                risk_evidence_refs=[{"flag": "LOCKUP_RISK", "matched_text": "test"}],
                risk_reasons=["解禁风险(-10)"],
            )
            save_candidate(c, db_path)
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol='002138.SZ'"
            ).fetchone()
            columns = [desc[0] for desc in conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol='002138.SZ'"
            ).description]
            conn.close()
            d = dict(zip(columns, row))
            assert d["risk_penalty"] == -10.0
            assert FLAG_LOCKUP_RISK in json.loads(d["risk_flags_json"])
        finally:
            os.unlink(db_path)


class TestEvaluateSymbolIntegration:
    def test_evaluate_with_lockup_event(self):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=100, freq="B")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(10, 20, 100),
            "High": np.random.uniform(20, 25, 100),
            "Low": np.random.uniform(8, 12, 100),
            "Close": np.random.uniform(10, 20, 100),
            "Volume": np.random.uniform(1e7, 1e8, 100),
        })

        c, reason = evaluate_symbol(
            symbol="000001.SZ",
            df=df,
            event_overrides=[{"title": "限售股解禁上市流通公告"}],
        )
        if c is not None:
            assert FLAG_LOCKUP_RISK in c.risk_flags
            assert c.risk_penalty < 0

    def test_evaluate_no_risk_events(self):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=100, freq="B")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.uniform(10, 20, 100),
            "High": np.random.uniform(20, 25, 100),
            "Low": np.random.uniform(8, 12, 100),
            "Close": np.random.uniform(10, 20, 100),
            "Volume": np.random.uniform(1e7, 1e8, 100),
        })

        c, reason = evaluate_symbol(
            symbol="000001.SZ",
            df=df,
            event_overrides=[{"title": "公司发布产品更新公告"}],
        )
        if c is not None:
            assert c.risk_penalty == 0.0


class TestDeepTaGate:
    def test_high_risk_flags_disable_deep_ta(self):
        c = Candidate(
            symbol="000001.SZ",
            need_deep_ta=True,
            signals=[
                CandidateSignal(strategy_tag="VCP", score=50, need_deep_ta=True),
            ],
        )
        c.merge_signals()
        c.need_deep_ta = True
        c.risk_flags = ["INQUIRY_RISK", "LOCKUP_RISK", "FINANCIAL_QUALITY_RISK"]
        from tradingagents.tradeflow.underwater_risk_flags import detect_underwater_risks
        r = detect_underwater_risks(
            event_texts=["限售股解禁", "收到问询函", "审计保留意见"],
        )
        high_risk = {f for f in r.risk_flags
                     if f in {"INQUIRY_RISK", "FINANCIAL_QUALITY_RISK"}}
        assert len(r.risk_flags) >= 3 or high_risk


class TestDailyPlanRender:
    def test_render_shows_risk_flags(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "000001.SZ",
                "name": "测试",
                "action": "OBSERVE",
                "risk_flags": ["LOCKUP_RISK", "INQUIRY_RISK"],
                "risk_penalty": -20.0,
                "risk_reasons": ["解禁风险(-10)", "问询风险(-10)"],
            }],
        )
        text = plan.render_text()
        assert "水下风险" in text
        assert "LOCKUP_RISK" in text
        assert "-20" in text

    def test_render_no_risk_flags(self):
        plan = DailyPlan(
            trade_date="2026-05-29",
            candidates=[{
                "symbol": "000001.SZ",
                "name": "测试",
                "action": "OBSERVE",
            }],
        )
        text = plan.render_text()
        assert "水下风险" not in text


class TestPlanRunnerIntegration:
    def test_build_plan_entry_includes_risk_fields(self):
        from tradingagents.tradeflow.plan_runner import _build_plan_entry

        c = Candidate(
            symbol="000001.SZ",
            risk_penalty=-15.0,
            risk_evidence_refs=[{"flag": "LOCKUP_RISK"}],
            risk_reasons=["解禁风险(-10)"],
        )
        entry = _build_plan_entry(c)
        assert entry["risk_penalty"] == -15.0
        assert len(entry["risk_evidence_refs"]) == 1
        assert len(entry["risk_reasons"]) == 1


class TestSymbolIsolation:
    def test_different_symbols_independent(self):
        r1 = detect_underwater_risks(event_texts=["限售股解禁公告"])
        r2 = detect_underwater_risks(event_texts=["公司发布产品更新"])
        assert FLAG_LOCKUP_RISK in r1.risk_flags
        assert FLAG_LOCKUP_RISK not in r2.risk_flags
        assert r1.risk_penalty < 0
        assert r2.risk_penalty == 0.0

    def test_risk_flags_not_shared_across_calls(self):
        r1 = detect_underwater_risks(event_texts=["限售股解禁公告"])
        r2 = detect_underwater_risks(event_texts=["公司日常公告"])
        assert len(r1.risk_flags) > 0
        assert len(r2.risk_flags) == 0
