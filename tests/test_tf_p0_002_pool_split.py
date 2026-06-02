# [TF-P0-002] tradeflow_pool_split
"""Tests for candidate pool split: short-term tech pool vs haotian left-side pool."""

import os
import sqlite3
import tempfile
from datetime import datetime

import pytest

from tradingagents.tradeflow.ambush_score import (
    CandidateType,
    classify_candidate_type,
    compute_ambush_score,
    AmbushScoreResult,
)
from tradingagents.tradeflow.candidate_pool import (
    pool_to_candidate_types,
    candidate_type_to_pool,
    POOL_TO_CANDIDATE_TYPES,
    POOL_LABELS,
    ALL_POOLS,
)
from tradingagents.tradeflow.candidate_engine import (
    init_db,
    save_candidate,
    Candidate,
    CandidateSignal,
)
from tradingagents.tradeflow.schemas import (
    STRATEGY_VCP,
    STRATEGY_PULLBACK,
)


class TestCandidateTypeEnum:
    def test_has_unclassified_data_gap(self):
        assert CandidateType.UNCLASSIFIED_DATA_GAP.value == "UNCLASSIFIED_DATA_GAP"

    def test_all_types_count(self):
        assert len(CandidateType) == 7


class TestClassifyCandidateTypeDataGap:
    def test_low_completeness_no_policy_no_tech(self):
        ct, reason = classify_candidate_type(
            data_completeness=0.15,
            has_policy=False,
            has_tech_breakout=False,
        )
        assert ct == CandidateType.UNCLASSIFIED_DATA_GAP
        assert "证据缺口" in reason

    def test_low_completeness_but_has_policy(self):
        ct, reason = classify_candidate_type(
            data_completeness=0.15,
            has_policy=True,
            mandate_score_component=45.0,
            has_beneficiary_path=True,
            beneficiary_score_component=50.0,
        )
        assert ct != CandidateType.UNCLASSIFIED_DATA_GAP

    def test_low_completeness_but_has_tech(self):
        ct, reason = classify_candidate_type(
            data_completeness=0.15,
            has_tech_breakout=True,
            pricing_gap_score=70.0,
        )
        assert ct != CandidateType.UNCLASSIFIED_DATA_GAP

    def test_high_completeness_no_policy(self):
        ct, reason = classify_candidate_type(
            data_completeness=0.9,
            has_policy=False,
            has_tech_breakout=True,
            pricing_gap_score=60.0,
        )
        assert ct == CandidateType.TECH_TRADE

    def test_boundary_30_percent(self):
        ct, reason = classify_candidate_type(
            data_completeness=0.30,
            has_policy=False,
            has_tech_breakout=False,
        )
        assert ct != CandidateType.UNCLASSIFIED_DATA_GAP

    def test_below_30_percent(self):
        ct, reason = classify_candidate_type(
            data_completeness=0.29,
            has_policy=False,
            has_tech_breakout=False,
        )
        assert ct == CandidateType.UNCLASSIFIED_DATA_GAP

    def test_default_completeness_is_1(self):
        ct, reason = classify_candidate_type(
            has_policy=False,
            has_tech_breakout=True,
            pricing_gap_score=60.0,
        )
        assert ct == CandidateType.TECH_TRADE


class TestPureVCPClassifiedTechTrade:
    def test_vcp_no_policy(self):
        ct, reason = classify_candidate_type(
            has_policy=False,
            has_tech_breakout=True,
            pricing_gap_score=55.0,
            data_completeness=0.85,
        )
        assert ct == CandidateType.TECH_TRADE
        assert "技术交易" in reason

    def test_pullback_no_policy(self):
        ct, reason = classify_candidate_type(
            has_policy=False,
            has_tech_breakout=False,
            pricing_gap_score=55.0,
            data_completeness=0.85,
        )
        assert ct == CandidateType.TECH_TRADE

    def test_policy_evidence_gives_mandate(self):
        ct, reason = classify_candidate_type(
            has_policy=True,
            has_beneficiary_path=True,
            mandate_score_component=50.0,
            beneficiary_score_component=45.0,
            has_tech_breakout=False,
            data_completeness=0.85,
        )
        assert ct == CandidateType.POLICY_AMBUSH


class TestPoolMapping:
    def test_pool_all(self):
        assert pool_to_candidate_types("all") == []

    def test_pool_haotian(self):
        assert pool_to_candidate_types("haotian") == ["POLICY_AMBUSH"]

    def test_pool_policy(self):
        assert pool_to_candidate_types("policy") == ["POLICY_CONFIRM"]

    def test_pool_tech(self):
        assert pool_to_candidate_types("tech") == ["TECH_TRADE"]

    def test_pool_event(self):
        assert pool_to_candidate_types("event") == ["EVENT_WATCH"]

    def test_pool_gap(self):
        assert pool_to_candidate_types("gap") == ["UNCLASSIFIED_DATA_GAP"]

    def test_pool_unknown(self):
        assert pool_to_candidate_types("nonexistent") == []

    def test_all_pools_have_labels(self):
        for p in ALL_POOLS:
            assert p in POOL_LABELS

    def test_ct_to_pool_haotian(self):
        assert candidate_type_to_pool("POLICY_AMBUSH") == "haotian"

    def test_ct_to_pool_tech(self):
        assert candidate_type_to_pool("TECH_TRADE") == "tech"

    def test_ct_to_pool_gap(self):
        assert candidate_type_to_pool("UNCLASSIFIED_DATA_GAP") == "gap"

    def test_ct_to_pool_pseudo_falls_to_all(self):
        assert candidate_type_to_pool("PSEUDO_POLICY") == "all"

    def test_ct_to_pool_overheated_falls_to_all(self):
        assert candidate_type_to_pool("OVERHEATED_AVOID") == "all"


class TestComputeAmbushScoreDataGap:
    def test_low_data_completeness_triggers_gap(self):
        result = compute_ambush_score(
            data_completeness=0.2,
            strategy_tags=["VCP"],
            tech_score=5.0,
        )
        assert result.candidate_type == "UNCLASSIFIED_DATA_GAP"

    def test_high_data_no_policy_tech(self):
        result = compute_ambush_score(
            strategy_tags=["VCP", "PULLBACK_SUPPORT"],
            tech_score=10.0,
            has_tech_breakout=True,
        )
        assert result.candidate_type == "TECH_TRADE"

    def test_with_policy_evidence(self):
        result = compute_ambush_score(
            mandate_score=60.0,
            policy_tags=["低空经济"],
            company_role="LEADER",
            beneficiary_path=["整机"],
            has_company_evidence=True,
            strategy_tags=["PULLBACK_SUPPORT"],
        )
        assert result.candidate_type in ("POLICY_AMBUSH", "POLICY_CONFIRM")


class TestSaveCandidatePoolType:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_tradeflow.db")
        init_db(self.db_path)

    def test_tech_trade_default(self):
        c = Candidate(
            symbol="601689.SH",
            name="拓普集团",
            trade_date="2026-06-02",
            strategy_tags=["VCP", "PULLBACK_SUPPORT"],
            signals=[CandidateSignal(strategy_tag=STRATEGY_VCP, score=8.0)],
        )
        c.merge_signals()
        c.candidate_type = "TECH_TRADE"
        save_candidate(c, self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tradeflow_candidates WHERE symbol='601689.SH'").fetchone()
        assert row["candidate_type"] == "TECH_TRADE"
        conn.close()

    def test_unclassified_data_gap(self):
        c = Candidate(
            symbol="000001.SZ",
            name="平安银行",
            trade_date="2026-06-02",
        )
        c.candidate_type = "UNCLASSIFIED_DATA_GAP"
        c.candidate_type_reason = "证据缺口: 数据完整度20%<30%, 无法分类"
        save_candidate(c, self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tradeflow_candidates WHERE symbol='000001.SZ'").fetchone()
        assert row["candidate_type"] == "UNCLASSIFIED_DATA_GAP"
        conn.close()

    def test_policy_ambush(self):
        c = Candidate(
            symbol="300034.SZ",
            name="钢研高纳",
            trade_date="2026-06-02",
        )
        c.candidate_type = "POLICY_AMBUSH"
        c.mandate_topic = "低空经济"
        c.company_role = "LEADER"
        c.ambush_score = 73.5
        c.mandate_score_component = 55.0
        save_candidate(c, self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tradeflow_candidates WHERE symbol='300034.SZ'").fetchone()
        assert row["candidate_type"] == "POLICY_AMBUSH"
        assert row["mandate_topic"] == "低空经济"
        conn.close()


class TestPoolAPIFiltering:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_tradeflow.db")
        init_db(self.db_path)

    def _insert(self, symbol, name, ct):
        c = Candidate(symbol=symbol, name=name, trade_date="2026-06-02")
        c.candidate_type = ct
        save_candidate(c, self.db_path)

    def test_pool_filter_service(self):
        from api.services.tradeflow_service import get_candidates
        self._insert("300034.SZ", "钢研高纳", "POLICY_AMBUSH")
        self._insert("601689.SH", "拓普集团", "TECH_TRADE")
        self._insert("000001.SZ", "平安银行", "UNCLASSIFIED_DATA_GAP")

        res_all = get_candidates("2026-06-02", tf_db_path=self.db_path)
        assert res_all["status"] == "ok"
        assert len(res_all["candidates"]) == 3

        res_haotian = get_candidates("2026-06-02", pool="haotian", tf_db_path=self.db_path)
        haotian_syms = [c["symbol"] for c in res_haotian["candidates"]]
        assert "300034.SZ" in haotian_syms
        assert "601689.SH" not in haotian_syms

        res_tech = get_candidates("2026-06-02", pool="tech", tf_db_path=self.db_path)
        tech_syms = [c["symbol"] for c in res_tech["candidates"]]
        assert "601689.SH" in tech_syms
        assert "300034.SZ" not in tech_syms

        res_gap = get_candidates("2026-06-02", pool="gap", tf_db_path=self.db_path)
        gap_syms = [c["symbol"] for c in res_gap["candidates"]]
        assert "000001.SZ" in gap_syms

    def test_pool_all_returns_all(self):
        from api.services.tradeflow_service import get_candidates
        self._insert("300034.SZ", "钢研高纳", "POLICY_AMBUSH")
        self._insert("601689.SH", "拓普集团", "TECH_TRADE")

        res = get_candidates("2026-06-02", pool="all", tf_db_path=self.db_path)
        assert len(res["candidates"]) == 2


class TestAcceptanceTFP0002:
    def test_pure_vcp_is_tech_trade(self):
        ct, reason = classify_candidate_type(
            has_policy=False,
            has_tech_breakout=True,
            pricing_gap_score=55.0,
            data_completeness=0.40,
        )
        assert ct == CandidateType.TECH_TRADE
        assert "技术交易" in reason

    def test_policy_evidence_is_policy_ambush(self):
        ct, reason = classify_candidate_type(
            has_policy=True,
            has_beneficiary_path=True,
            mandate_score_component=50.0,
            beneficiary_score_component=45.0,
            has_tech_breakout=False,
            data_completeness=0.85,
        )
        assert ct == CandidateType.POLICY_AMBUSH

    def test_low_completeness_is_data_gap(self):
        ct, reason = classify_candidate_type(
            has_policy=False,
            has_tech_breakout=False,
            data_completeness=0.20,
        )
        assert ct == CandidateType.UNCLASSIFIED_DATA_GAP

    def test_pool_mapping_complete(self):
        assert pool_to_candidate_types("haotian") == ["POLICY_AMBUSH"]
        assert pool_to_candidate_types("tech") == ["TECH_TRADE"]
        assert pool_to_candidate_types("policy") == ["POLICY_CONFIRM"]
        assert pool_to_candidate_types("event") == ["EVENT_WATCH"]
        assert pool_to_candidate_types("gap") == ["UNCLASSIFIED_DATA_GAP"]

    def test_no_strong_buy_sell_words(self):
        forbidden = {"立即买入", "重仓买入", "立即清仓", "满仓", "梭哈"}
        ct, reason = classify_candidate_type(
            has_policy=True,
            has_beneficiary_path=True,
            mandate_score_component=50.0,
            beneficiary_score_component=45.0,
            has_tech_breakout=False,
            data_completeness=0.85,
        )
        for w in forbidden:
            assert w not in reason
        assert w not in ct.value
