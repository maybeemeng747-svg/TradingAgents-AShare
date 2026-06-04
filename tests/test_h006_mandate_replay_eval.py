# [H-006] mandate_replay_eval tests
"""Tests for 昊天候选池回放评估与反证机制."""

import os
import tempfile
from unittest.mock import patch

import pytest

from tradingagents.tradeflow.ambush_score import CandidateType
from tradingagents.tradeflow.mandate_replay_eval import (
    ALL_COUNTER_TYPES,
    ALL_HORIZONS,
    ALL_REPLAY_FIXTURE_IDS,
    COUNTER_CAPITAL_NOT_RECOGNIZING,
    COUNTER_FALSE_PATH,
    COUNTER_FUNDAMENTAL_RISK,
    COUNTER_OVERHEAT_REVERSAL,
    COUNTER_POLICY_FADED,
    COUNTER_TYPE_LABELS,
    FIXTURE_OVERHEATED_CRASH,
    FIXTURE_POLICY_AMBUSH_SUCCESS,
    CounterEvidence,
    PriceSnapshot,
    PostEventCheck,
    ReplayEvaluation,
    ReplayFixture,
    ReplayReport,
    _FIXTURE_BUILDERS,
    _build_capital_ignore,
    _build_data_gap_unclassified,
    _build_event_watch_no_follow,
    _build_false_path,
    _build_overheated_crash,
    _build_policy_ambush_counter,
    _build_policy_ambush_success,
    _build_policy_confirm_success,
    _build_pseudo_policy_fake,
    _build_tech_trade_short,
    _build_policy_fade,
    evaluate_fixture,
    get_all_replay_fixtures,
    get_replay_fixture,
    render_replay_report,
    run_replay_and_save,
    run_replay_evaluation,
    save_replay_report,
)


# ── PriceSnapshot ──────────────────────────────────────────────────


class TestPriceSnapshot:
    def test_default_values(self):
        ps = PriceSnapshot()
        assert ps.entry_price == 0.0
        assert ps.prices == {}
        assert ps.get_return_pct(5) is None

    def test_get_return_pct(self):
        ps = PriceSnapshot(entry_price=100.0, prices={5: 110.0})
        assert ps.get_return_pct(5) == 10.0

    def test_get_return_pct_negative(self):
        ps = PriceSnapshot(entry_price=100.0, prices={10: 88.0})
        assert ps.get_return_pct(10) == -12.0

    def test_get_return_pct_zero_entry(self):
        ps = PriceSnapshot(entry_price=0.0, prices={5: 10.0})
        assert ps.get_return_pct(5) is None

    def test_get_return_pct_missing_horizon(self):
        ps = PriceSnapshot(entry_price=100.0, prices={5: 110.0})
        assert ps.get_return_pct(10) is None

    def test_get_index_return_pct(self):
        ps = PriceSnapshot(
            entry_price=100.0,
            index_prices={0: 3000.0, 5: 3060.0},
        )
        assert ps.get_index_return_pct(5) == 2.0

    def test_get_index_return_pct_no_entry(self):
        ps = PriceSnapshot(entry_price=100.0, index_prices={5: 3060.0})
        assert ps.get_index_return_pct(5) is None

    def test_get_excess_return(self):
        ps = PriceSnapshot(
            entry_price=100.0,
            prices={5: 110.0},
            index_prices={0: 3000.0, 5: 3030.0},
        )
        assert ps.get_excess_return_pct(5) == 9.0

    def test_get_excess_return_none(self):
        ps = PriceSnapshot(entry_price=100.0, prices={5: 110.0})
        assert ps.get_excess_return_pct(5) is None

    def test_get_max_gain(self):
        ps = PriceSnapshot(entry_price=100.0, prices={5: 105.0, 10: 120.0, 20: 110.0})
        assert ps.get_max_gain_pct() == 20.0

    def test_get_max_gain_no_gain(self):
        ps = PriceSnapshot(entry_price=100.0, prices={5: 95.0, 10: 90.0})
        assert ps.get_max_gain_pct() == 0.0

    def test_get_max_gain_empty(self):
        ps = PriceSnapshot(entry_price=100.0)
        assert ps.get_max_gain_pct() is None

    def test_get_max_drawdown(self):
        ps = PriceSnapshot(entry_price=100.0, prices={5: 95.0, 10: 85.0, 20: 90.0})
        assert ps.get_max_drawdown_pct() == 15.0

    def test_get_max_drawdown_no_dd(self):
        ps = PriceSnapshot(entry_price=100.0, prices={5: 105.0, 10: 110.0})
        assert ps.get_max_drawdown_pct() == 0.0

    def test_to_dict(self):
        ps = PriceSnapshot(
            entry_price=100.0,
            prices={5: 110.0, 10: 120.0},
            index_prices={0: 3000.0, 5: 3060.0, 10: 3120.0},
        )
        d = ps.to_dict()
        assert d["entry_price"] == 100.0
        assert "returns" in d
        assert d["returns"]["5"]["return_pct"] == 10.0
        assert d["max_gain_pct"] == 20.0
        assert d["max_drawdown_pct"] == 0.0


# ── PostEventCheck ─────────────────────────────────────────────────


class TestPostEventCheck:
    def test_default(self):
        pe = PostEventCheck()
        assert pe.policy_reconfirmed is False
        assert pe.policy_persistence_score == 0.0

    def test_to_dict(self):
        pe = PostEventCheck(
            policy_reconfirmed=True,
            announcement_fulfilled=True,
            trend_confirmed=False,
            policy_persistence_score=75.5,
            benefit_realization_score=60.3,
        )
        d = pe.to_dict()
        assert d["policy_reconfirmed"] is True
        assert d["policy_persistence_score"] == 75.5
        assert d["benefit_realization_score"] == 60.3


# ── CounterEvidence ────────────────────────────────────────────────


class TestCounterEvidence:
    def test_default(self):
        ce = CounterEvidence()
        assert ce.counter_type == ""
        assert ce.severity == 0.0

    def test_to_dict(self):
        ce = CounterEvidence(
            counter_type=COUNTER_POLICY_FADED,
            description="政策消退",
            horizon=10,
            severity=0.7,
        )
        d = ce.to_dict()
        assert d["counter_type"] == COUNTER_POLICY_FADED
        assert d["severity"] == 0.7


# ── ReplayFixture ──────────────────────────────────────────────────


class TestReplayFixture:
    def test_default(self):
        rf = ReplayFixture()
        assert rf.fixture_id == ""
        assert rf.price_snapshot is not None
        assert rf.counter_evidences == []

    def test_to_dict(self):
        rf = ReplayFixture(
            fixture_id="test",
            symbol="000001.SZ",
            candidate_type=CandidateType.POLICY_AMBUSH.value,
            price_snapshot=PriceSnapshot(entry_price=10.0),
        )
        d = rf.to_dict()
        assert d["fixture_id"] == "test"
        assert d["symbol"] == "000001.SZ"
        assert "price_snapshot" in d


# ── ReplayEvaluation ───────────────────────────────────────────────


class TestReplayEvaluation:
    def test_default(self):
        ev = ReplayEvaluation()
        assert ev.passed is True
        assert ev.verdict_score == 0.0

    def test_to_dict(self):
        ev = ReplayEvaluation(
            fixture_id="test",
            max_gain_pct=15.0,
            max_drawdown_pct=None,
        )
        d = ev.to_dict()
        assert d["max_gain_pct"] == "15.0%"
        assert d["max_drawdown_pct"] == "N/A"


# ── ReplayReport ───────────────────────────────────────────────────


class TestReplayReport:
    def test_default(self):
        r = ReplayReport()
        assert r.all_passed is True
        assert r.total_fixtures == 0

    def test_to_dict(self):
        r = ReplayReport(
            total_fixtures=3,
            passed=2,
            failed=1,
            all_passed=False,
            avg_verdict_score=65.5,
        )
        d = r.to_dict()
        assert d["total_fixtures"] == 3
        assert d["avg_verdict_score"] == 65.5


# ── Counter Types ──────────────────────────────────────────────────


class TestCounterTypes:
    def test_all_five_types(self):
        assert len(ALL_COUNTER_TYPES) == 5

    def test_labels_complete(self):
        for ct in ALL_COUNTER_TYPES:
            assert ct in COUNTER_TYPE_LABELS

    def test_known_types(self):
        assert COUNTER_POLICY_FADED == "policy_faded"
        assert COUNTER_FALSE_PATH == "false_path"
        assert COUNTER_OVERHEAT_REVERSAL == "overheat_reversal"
        assert COUNTER_FUNDAMENTAL_RISK == "fundamental_risk"
        assert COUNTER_CAPITAL_NOT_RECOGNIZING == "capital_not_recognizing"


# ── Horizons ───────────────────────────────────────────────────────


class TestHorizons:
    def test_all_four(self):
        assert ALL_HORIZONS == [5, 10, 20, 60]

    def test_count(self):
        assert len(ALL_HORIZONS) == 4


# ── Fixture IDs ────────────────────────────────────────────────────


class TestFixtureIDs:
    def test_all_10_fixtures(self):
        assert len(ALL_REPLAY_FIXTURE_IDS) == 11

    def test_all_builders_registered(self):
        for fid in ALL_REPLAY_FIXTURE_IDS:
            assert fid in _FIXTURE_BUILDERS

    def test_unique_ids(self):
        assert len(ALL_REPLAY_FIXTURE_IDS) == len(set(ALL_REPLAY_FIXTURE_IDS))


# ── Get Fixture ────────────────────────────────────────────────────


class TestGetFixture:
    def test_known_fixture(self):
        f = get_replay_fixture(FIXTURE_POLICY_AMBUSH_SUCCESS)
        assert f is not None
        assert f.fixture_id == FIXTURE_POLICY_AMBUSH_SUCCESS

    def test_unknown_returns_none(self):
        assert get_replay_fixture("nonexistent") is None

    def test_all_fixtures_retrievable(self):
        for fid in ALL_REPLAY_FIXTURE_IDS:
            f = get_replay_fixture(fid)
            assert f is not None, f"Missing fixture: {fid}"

    def test_get_all(self):
        all_f = get_all_replay_fixtures()
        assert len(all_f) == 11


# ── Fixture Content Verification ───────────────────────────────────


class TestFixtureContent:
    def test_policy_ambush_success(self):
        f = _build_policy_ambush_success()
        assert f.candidate_type == CandidateType.POLICY_AMBUSH.value
        assert f.mandate_score_component >= 40
        assert f.company_role == "CORE_SUPPLIER"
        assert len(f.counter_evidences) == 0
        assert f.post_event.policy_reconfirmed is True

    def test_policy_ambush_counter(self):
        f = _build_policy_ambush_counter()
        assert f.candidate_type == CandidateType.POLICY_AMBUSH.value
        assert len(f.counter_evidences) >= 1
        counter_types = [c.counter_type for c in f.counter_evidences]
        assert COUNTER_POLICY_FADED in counter_types
        assert COUNTER_FALSE_PATH in counter_types

    def test_policy_confirm_success(self):
        f = _build_policy_confirm_success()
        assert f.candidate_type == CandidateType.POLICY_CONFIRM.value
        assert f.company_role == "LEADER"
        assert f.post_event.trend_confirmed is True

    def test_tech_trade(self):
        f = _build_tech_trade_short()
        assert f.candidate_type == CandidateType.TECH_TRADE.value
        assert f.mandate_score_component < 20
        assert f.company_role == "UNKNOWN"

    def test_event_watch(self):
        f = _build_event_watch_no_follow()
        assert f.candidate_type == CandidateType.EVENT_WATCH.value
        assert f.post_event.policy_reconfirmed is False

    def test_pseudo_policy(self):
        f = _build_pseudo_policy_fake()
        assert f.candidate_type == CandidateType.PSEUDO_POLICY.value
        assert f.company_role == "CONCEPT_ONLY"
        assert len(f.counter_evidences) >= 1

    def test_overheated_crash(self):
        f = _build_overheated_crash()
        assert f.candidate_type == CandidateType.OVERHEATED_AVOID.value
        assert len(f.counter_evidences) >= 1
        counter_types = [c.counter_type for c in f.counter_evidences]
        assert COUNTER_OVERHEAT_REVERSAL in counter_types

    def test_policy_fade(self):
        f = _build_policy_fade()
        assert f.candidate_type == CandidateType.POLICY_AMBUSH.value
        assert len(f.counter_evidences) >= 1

    def test_false_path(self):
        f = _build_false_path()
        assert f.candidate_type == CandidateType.POLICY_CONFIRM.value
        counter_types = [c.counter_type for c in f.counter_evidences]
        assert COUNTER_FALSE_PATH in counter_types

    def test_capital_ignore(self):
        f = _build_capital_ignore()
        assert f.candidate_type == CandidateType.POLICY_AMBUSH.value
        counter_types = [c.counter_type for c in f.counter_evidences]
        assert COUNTER_CAPITAL_NOT_RECOGNIZING in counter_types

    def test_data_gap_unclassified(self):
        f = _build_data_gap_unclassified()
        assert f.candidate_type == CandidateType.UNCLASSIFIED_DATA_GAP.value
        assert f.mandate_score_component == 0.0
        assert f.company_role == "UNKNOWN"
        assert len(f.counter_evidences) == 0

    def test_all_fixtures_have_prices(self):
        for fid in ALL_REPLAY_FIXTURE_IDS:
            f = get_replay_fixture(fid)
            assert f.price_snapshot.entry_price > 0, f"{fid} has no entry price"
            assert len(f.price_snapshot.prices) > 0, f"{fid} has no price data"

    def test_all_fixtures_have_horizons(self):
        for fid in ALL_REPLAY_FIXTURE_IDS:
            f = get_replay_fixture(fid)
            for h in ALL_HORIZONS:
                assert h in f.price_snapshot.prices, f"{fid} missing horizon {h}"

    def test_severity_range(self):
        for fid in ALL_REPLAY_FIXTURE_IDS:
            f = get_replay_fixture(fid)
            for ce in f.counter_evidences:
                assert 0 < ce.severity <= 1.0, f"{fid} severity out of range: {ce.severity}"


# ── Evaluate Fixture ───────────────────────────────────────────────


class TestEvaluateFixture:
    def test_policy_ambush_success_passes(self):
        f = _build_policy_ambush_success()
        ev = evaluate_fixture(f)
        assert ev.passed is True
        assert ev.verdict_score >= 70
        assert "confirmed" in ev.verdict

    def test_policy_ambush_counter_fails(self):
        f = _build_policy_ambush_counter()
        ev = evaluate_fixture(f)
        assert ev.verdict_score < 50
        assert "counter_evidence" in ev.verdict

    def test_policy_confirm_success_passes(self):
        f = _build_policy_confirm_success()
        ev = evaluate_fixture(f)
        assert ev.passed is True
        assert ev.verdict_score >= 70

    def test_tech_trade_returns(self):
        f = _build_tech_trade_short()
        ev = evaluate_fixture(f)
        assert ev.returns_by_horizon["5"] is not None
        assert ev.returns_by_horizon["5"] == pytest.approx(10.0, abs=0.1)

    def test_event_watch(self):
        f = _build_event_watch_no_follow()
        ev = evaluate_fixture(f)
        assert ev.passed is True

    def test_pseudo_policy(self):
        f = _build_pseudo_policy_fake()
        ev = evaluate_fixture(f)
        assert ev.passed is True
        assert "confirmed" in ev.verdict or "warning" in ev.verdict

    def test_overheated_crash(self):
        f = _build_overheated_crash()
        ev = evaluate_fixture(f)
        assert ev.passed is True
        assert ev.verdict_score >= 80

    def test_policy_fade_counter(self):
        f = _build_policy_fade()
        ev = evaluate_fixture(f)
        assert len(ev.counter_evidences) > 0

    def test_false_path_counter(self):
        f = _build_false_path()
        ev = evaluate_fixture(f)
        assert len(ev.counter_evidences) > 0

    def test_capital_ignore_counter(self):
        f = _build_capital_ignore()
        ev = evaluate_fixture(f)
        assert len(ev.counter_evidences) > 0
        assert ev.counter_evidences[0].counter_type == COUNTER_CAPITAL_NOT_RECOGNIZING

    def test_data_gap_unclassified_evaluation(self):
        f = _build_data_gap_unclassified()
        ev = evaluate_fixture(f)
        assert ev.passed is True
        assert "证据缺口" in ev.verdict

    def test_beat_index_computed(self):
        f = _build_policy_ambush_success()
        ev = evaluate_fixture(f)
        for h in ALL_HORIZONS:
            k = str(h)
            assert k in ev.beat_index

    def test_max_gain_drawdown(self):
        f = _build_policy_ambush_success()
        ev = evaluate_fixture(f)
        assert ev.max_gain_pct is not None
        assert ev.max_gain_pct > 0
        assert ev.max_drawdown_pct is not None

    def test_calibration_hints_generated(self):
        f = _build_policy_ambush_counter()
        ev = evaluate_fixture(f)
        assert len(ev.calibration_hints) > 0

    def test_calibration_hints_for_false_path(self):
        f = _build_false_path()
        ev = evaluate_fixture(f)
        has_h003 = any("H-003" in h for h in ev.calibration_hints)
        assert has_h003

    def test_calibration_hints_for_overheated(self):
        f = _build_overheated_crash()
        ev = evaluate_fixture(f)
        has_h004 = any("H-004" in h for h in ev.calibration_hints)
        assert has_h004


# ── Run Replay Evaluation ─────────────────────────────────────────


class TestRunReplayEvaluation:
    def test_all_fixtures(self):
        report = run_replay_evaluation()
        assert report.total_fixtures == 11
        assert report.passed + report.failed == 11
        assert len(report.results) == 11

    def test_by_candidate_type(self):
        report = run_replay_evaluation()
        assert len(report.by_candidate_type) > 0
        for ct, counts in report.by_candidate_type.items():
            assert counts["total"] == counts["passed"] + counts["failed"]

    def test_by_counter_type(self):
        report = run_replay_evaluation()
        assert len(report.by_counter_type) > 0

    def test_avg_verdict_score(self):
        report = run_replay_evaluation()
        assert 0 < report.avg_verdict_score <= 100

    def test_calibration_summary(self):
        report = run_replay_evaluation()
        assert len(report.calibration_summary) > 0

    def test_selective_fixtures(self):
        report = run_replay_evaluation(
            fixture_ids=[FIXTURE_POLICY_AMBUSH_SUCCESS, FIXTURE_OVERHEATED_CRASH]
        )
        assert report.total_fixtures == 2

    def test_custom_fixtures(self):
        f = ReplayFixture(
            fixture_id="custom",
            symbol="TEST.SZ",
            candidate_type=CandidateType.EVENT_WATCH.value,
            price_snapshot=PriceSnapshot(entry_price=10.0, prices={5: 10.0}),
        )
        report = run_replay_evaluation(fixtures=[f])
        assert report.total_fixtures == 1
        assert report.results[0].fixture_id == "custom"

    def test_empty_input(self):
        report = run_replay_evaluation(fixtures=[])
        assert report.total_fixtures == 0
        assert report.passed == 0
        assert report.failed == 0
        assert report.all_passed is True

    def test_unknown_fixture_id_skipped(self):
        report = run_replay_evaluation(fixture_ids=["nonexistent"])
        assert report.total_fixtures == 0


# ── Render Report ──────────────────────────────────────────────────


class TestRenderReplayReport:
    def test_full_render(self):
        report = run_replay_evaluation()
        md = render_replay_report(report)
        assert "昊天候选池回放评估报告" in md
        assert "汇总" in md
        assert "校准建议" in md

    def test_render_contains_all_fixtures(self):
        report = run_replay_evaluation()
        md = render_replay_report(report)
        for fid in ALL_REPLAY_FIXTURE_IDS:
            assert fid in md

    def test_render_counter_types(self):
        report = run_replay_evaluation()
        md = render_replay_report(report)
        for ct in ALL_COUNTER_TYPES:
            if ct in report.by_counter_type:
                assert ct in md

    def test_render_percentage_format(self):
        report = run_replay_evaluation()
        md = render_replay_report(report)
        assert "N/A" in md or "%" in md

    def test_render_empty_report(self):
        report = ReplayReport(date="2026-06-02", run_at="test")
        md = render_replay_report(report)
        assert "昊天候选池回放评估报告" in md

    def test_render_by_candidate_type_section(self):
        report = run_replay_evaluation()
        md = render_replay_report(report)
        assert "按候选类型" in md


# ── Save Report ────────────────────────────────────────────────────


class TestSaveReplayReport:
    def test_save_creates_file(self):
        report = run_replay_evaluation()
        with tempfile.TemporaryDirectory() as td:
            path = save_replay_report(report, output_dir=td)
            assert os.path.exists(path)
            assert path.endswith(".md")

    def test_filename_is_date(self):
        report = ReplayReport(date="2026-06-02", run_at="test")
        with tempfile.TemporaryDirectory() as td:
            path = save_replay_report(report, output_dir=td)
            assert "2026-06-02.md" in path

    def test_run_replay_and_save(self):
        with tempfile.TemporaryDirectory() as td:
            path = run_replay_and_save(output_dir=td)
            assert os.path.exists(path)
            with open(path, "r") as f:
                content = f.read()
            assert "昊天候选池回放评估报告" in content


# ── Acceptance Tests ───────────────────────────────────────────────


class TestAcceptanceH006:
    def test_fixed_fixture_stable_output(self):
        f = _build_policy_ambush_success()
        ev = evaluate_fixture(f)
        assert ev.returns_by_horizon["5"] == pytest.approx(6.0, abs=0.01)
        assert ev.returns_by_horizon["10"] == pytest.approx(12.0, abs=0.01)
        assert ev.returns_by_horizon["20"] == pytest.approx(18.0, abs=0.01)
        assert ev.returns_by_horizon["60"] == pytest.approx(28.0, abs=0.01)

    def test_zero_displayed_as_0(self):
        ev = ReplayEvaluation(max_gain_pct=0.0, max_drawdown_pct=0.0)
        d = ev.to_dict()
        assert d["max_gain_pct"] == "0.0%"
        assert d["max_drawdown_pct"] == "0.0%"

    def test_none_displayed_as_na(self):
        ev = ReplayEvaluation(max_gain_pct=None, max_drawdown_pct=None)
        d = ev.to_dict()
        assert d["max_gain_pct"] == "N/A"
        assert d["max_drawdown_pct"] == "N/A"

    def test_no_eval_results_write(self):
        with tempfile.TemporaryDirectory() as td:
            path = run_replay_and_save(output_dir=td)
            assert "eval_results" not in path

    def test_all_six_candidate_types_covered(self):
        types_covered = set()
        for fid in ALL_REPLAY_FIXTURE_IDS:
            f = get_replay_fixture(fid)
            types_covered.add(f.candidate_type)
        for ct in CandidateType:
            assert ct.value in types_covered, f"Missing coverage for {ct.value}"

    def test_all_five_counter_types_covered(self):
        counters = set()
        for fid in ALL_REPLAY_FIXTURE_IDS:
            f = get_replay_fixture(fid)
            for ce in f.counter_evidences:
                counters.add(ce.counter_type)
        for ct in ALL_COUNTER_TYPES:
            assert ct in counters, f"Missing counter type: {ct}"

    def test_all_four_horizons_covered(self):
        for fid in ALL_REPLAY_FIXTURE_IDS:
            f = get_replay_fixture(fid)
            for h in ALL_HORIZONS:
                assert h in f.price_snapshot.prices, f"{fid} missing {h}d"

    def test_counter_evidence_types_valid(self):
        for fid in ALL_REPLAY_FIXTURE_IDS:
            f = get_replay_fixture(fid)
            for ce in f.counter_evidences:
                assert ce.counter_type in ALL_COUNTER_TYPES

    def test_replay_report_stable(self):
        report1 = run_replay_evaluation()
        report2 = run_replay_evaluation()
        assert report1.total_fixtures == report2.total_fixtures
        assert len(report1.results) == len(report2.results)
        for r1, r2 in zip(report1.results, report2.results):
            assert r1.fixture_id == r2.fixture_id
            assert r1.verdict == r2.verdict


# ── Calibration Hint Generation ────────────────────────────────────


class TestCalibrationHints:
    def test_policy_fade_suggests_h002(self):
        f = _build_policy_fade()
        ev = evaluate_fixture(f)
        assert any("H-002" in h for h in ev.calibration_hints)

    def test_false_path_suggests_h003(self):
        f = _build_false_path()
        ev = evaluate_fixture(f)
        assert any("H-003" in h for h in ev.calibration_hints)

    def test_overheated_suggests_h004(self):
        f = _build_overheated_crash()
        ev = evaluate_fixture(f)
        assert any("H-004" in h for h in ev.calibration_hints)

    def test_capital_ignore_suggests_fund_flow(self):
        f = _build_capital_ignore()
        ev = evaluate_fixture(f)
        assert any("fund_flow" in h for h in ev.calibration_hints)

    def test_high_policy_low_fulfillment_hint(self):
        f = ReplayFixture(
            fixture_id="test_high_pol_low_fulfill",
            candidate_type=CandidateType.POLICY_AMBUSH.value,
            price_snapshot=PriceSnapshot(entry_price=10.0, prices={5: 10.5}),
            post_event=PostEventCheck(
                policy_persistence_score=80.0,
                announcement_fulfilled=False,
            ),
        )
        ev = evaluate_fixture(f)
        assert any("company_evidence" in h for h in ev.calibration_hints)

    def test_leader_role_low_score_hint(self):
        f = ReplayFixture(
            fixture_id="test_leader_fail",
            candidate_type=CandidateType.POLICY_CONFIRM.value,
            company_role="LEADER",
            price_snapshot=PriceSnapshot(
                entry_price=50.0,
                prices={5: 45.0, 10: 40.0, 20: 35.0, 60: 30.0},
            ),
            post_event=PostEventCheck(
                policy_reconfirmed=True,
                announcement_fulfilled=False,
            ),
            counter_evidences=[
                CounterEvidence(
                    counter_type=COUNTER_FALSE_PATH,
                    description="伪龙头",
                    horizon=10,
                    severity=0.8,
                ),
            ],
        )
        ev = evaluate_fixture(f)
        assert any("LEADER" in h or "CORE_SUPPLIER" in h for h in ev.calibration_hints)


# ── Integration ────────────────────────────────────────────────────


class TestIntegration:
    def test_fixture_to_dict_round_trip(self):
        f = _build_policy_ambush_success()
        d = f.to_dict()
        assert d["fixture_id"] == f.fixture_id
        assert d["price_snapshot"]["entry_price"] == f.price_snapshot.entry_price
        assert "returns" in d["price_snapshot"]

    def test_evaluation_to_dict_round_trip(self):
        f = _build_policy_confirm_success()
        ev = evaluate_fixture(f)
        d = ev.to_dict()
        assert d["fixture_id"] == f.fixture_id
        assert isinstance(d["returns_by_horizon"], dict)
        assert isinstance(d["beat_index"], dict)

    def test_report_to_dict_round_trip(self):
        report = run_replay_evaluation()
        d = report.to_dict()
        assert d["total_fixtures"] == report.total_fixtures
        assert len(d["results"]) == len(report.results)

    def test_ambush_score_integration(self):
        f = _build_policy_ambush_success()
        assert f.ambush_score > 0
        assert f.mandate_score_component > 0
        assert f.beneficiary_score_component > 0

    def test_candidate_type_enum_alignment(self):
        for fid in ALL_REPLAY_FIXTURE_IDS:
            f = get_replay_fixture(fid)
            valid_types = {ct.value for ct in CandidateType}
            assert f.candidate_type in valid_types, f"{fid} has invalid type: {f.candidate_type}"


# ── Edge Cases ─────────────────────────────────────────────────────


class TestEdgeCases:
    def test_zero_price(self):
        ps = PriceSnapshot(entry_price=0.0)
        assert ps.get_max_gain_pct() is None
        assert ps.get_max_drawdown_pct() is None

    def test_single_horizon(self):
        ps = PriceSnapshot(entry_price=100.0, prices={5: 110.0})
        assert ps.get_return_pct(5) == 10.0
        assert ps.get_return_pct(10) is None

    def test_fixture_no_counters(self):
        f = ReplayFixture(
            fixture_id="no_counters",
            candidate_type=CandidateType.TECH_TRADE.value,
            price_snapshot=PriceSnapshot(entry_price=10.0, prices={5: 10.5}),
        )
        ev = evaluate_fixture(f)
        assert ev.counter_evidences == []

    def test_render_with_no_counters(self):
        report = run_replay_evaluation(
            fixtures=[
                ReplayFixture(
                    fixture_id="minimal",
                    symbol="T.SZ",
                    candidate_type=CandidateType.TECH_TRADE.value,
                    price_snapshot=PriceSnapshot(entry_price=10.0, prices={5: 10.0}),
                )
            ]
        )
        md = render_replay_report(report)
        assert "minimal" in md

    def test_all_passed_flag(self):
        f = ReplayFixture(
            fixture_id="always_pass",
            candidate_type=CandidateType.POLICY_AMBUSH.value,
            mandate_score_component=70.0,
            beneficiary_score_component=60.0,
            price_snapshot=PriceSnapshot(
                entry_price=100.0,
                prices={5: 110.0, 10: 120.0, 20: 130.0, 60: 140.0},
            ),
            post_event=PostEventCheck(
                policy_reconfirmed=True,
                announcement_fulfilled=True,
                trend_confirmed=True,
                policy_persistence_score=90.0,
                benefit_realization_score=80.0,
            ),
        )
        report = run_replay_evaluation(fixtures=[f])
        assert report.all_passed is True

    def test_negative_return_formatting(self):
        ev = ReplayEvaluation(
            returns_by_horizon={"5": -5.3, "10": -10.0},
            max_gain_pct=0.0,
            max_drawdown_pct=10.0,
        )
        d = ev.to_dict()
        assert d["max_drawdown_pct"] == "10.0%"
        assert d["max_gain_pct"] == "0.0%"
