# [PLAYBOOK-001] lifecycle_contract
"""Tests for PLAYBOOK-001: 上车—在车上—下车战法字段契约与状态枚举.

Covers:
- Stage enum constants & validation
- Lot status enums (trial / confirm / attack)
- PlaybookContract dataclass defaults & field coverage
- playbook_contract_from_dict: safe construction, unknown-key tolerance,
  dirty-data normalization, score/position clamping
- playbook_contract_to_dict / merge / is_empty / summary
- normalize_playbook_stage: CRITICAL — unknown stage NEVER falls back to hold
- Safety: no strong action words in any text field
- Integration: ObservationItemResponse + ReportResponse carry optional fields
- Backward compat: old dicts without playbook fields don't break
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.tradeflow.playbook_contract import (
    PLAYBOOK_STAGES,
    PLAYBOOK_STAGE_LABELS,
    TRIAL_LOT_STATUSES,
    CONFIRM_LOT_STATUSES,
    ATTACK_LOT_STATUSES,
    STAGE_OBSERVE,
    STAGE_TRIAL,
    STAGE_CONFIRM,
    STAGE_ATTACK,
    STAGE_HOLD,
    STAGE_RISK,
    STAGE_EXIT,
    TRIAL_LOT_NONE,
    TRIAL_LOT_BUILT,
    TRIAL_LOT_FAILED,
    TRIAL_LOT_SUCCEEDED,
    CONFIRM_LOT_NONE,
    CONFIRM_LOT_ELIGIBLE,
    CONFIRM_LOT_ADDED,
    CONFIRM_LOT_CANCELLED,
    ATTACK_LOT_NONE,
    ATTACK_LOT_PULLBACK,
    ATTACK_LOT_BREAKOUT,
    ATTACK_LOT_ADDED,
    ATTACK_LOT_RETREAT,
    SCORE_MIN,
    SCORE_MAX,
    POSITION_PCT_MIN,
    POSITION_PCT_MAX,
    FORBIDDEN_STRONG_WORDS,
    PlaybookContract,
    is_valid_playbook_stage,
    normalize_playbook_stage,
    playbook_contract_from_dict,
    playbook_contract_to_dict,
    merge_playbook_contract_into_dict,
    playbook_contract_is_empty,
    playbook_stage_label,
    playbook_contract_summary,
    assert_no_strong_action_words,
    validate_playbook_contract_safety,
)


# ── Stage enum ──────────────────────────────────────────────────────────────

class TestStageEnum:
    def test_seven_stages_defined(self):
        assert len(PLAYBOOK_STAGES) == 7

    def test_stages_match_doc(self):
        assert PLAYBOOK_STAGES == [
            "observe", "trial", "confirm", "attack", "hold", "risk", "exit",
        ]

    @pytest.mark.parametrize("stage", PLAYBOOK_STAGES)
    def test_each_stage_has_label(self, stage):
        assert stage in PLAYBOOK_STAGE_LABELS
        assert isinstance(PLAYBOOK_STAGE_LABELS[stage], str)
        assert len(PLAYBOOK_STAGE_LABELS[stage]) > 0

    def test_labels_have_no_strong_action_words(self):
        for label in PLAYBOOK_STAGE_LABELS.values():
            for word in FORBIDDEN_STRONG_WORDS:
                assert word not in label

    def test_constants_are_strings(self):
        for s in [STAGE_OBSERVE, STAGE_TRIAL, STAGE_CONFIRM, STAGE_ATTACK,
                  STAGE_HOLD, STAGE_RISK, STAGE_EXIT]:
            assert isinstance(s, str)


class TestIsValidPlaybookStage:
    @pytest.mark.parametrize("stage", PLAYBOOK_STAGES)
    def test_valid_stages(self, stage):
        assert is_valid_playbook_stage(stage) is True

    def test_none_is_invalid(self):
        assert is_valid_playbook_stage(None) is False

    def test_empty_string_is_invalid(self):
        assert is_valid_playbook_stage("") is False

    def test_unknown_stage_is_invalid(self):
        assert is_valid_playbook_stage("unknown_stage") is False
        assert is_valid_playbook_stage("buy") is False
        assert is_valid_playbook_stage("sell") is False

    def test_non_string_is_invalid(self):
        assert is_valid_playbook_stage(123) is False
        assert is_valid_playbook_stage([]) is False
        assert is_valid_playbook_stage({}) is False


class TestNormalizePlaybookStage:
    """CRITICAL: unknown stages must NEVER fall back to hold."""

    @pytest.mark.parametrize("stage", PLAYBOOK_STAGES)
    def test_valid_stages_pass_through(self, stage):
        assert normalize_playbook_stage(stage) == stage

    def test_case_insensitive(self):
        assert normalize_playbook_stage("OBSERVE") == "observe"
        assert normalize_playbook_stage("Trial") == "trial"
        assert normalize_playbook_stage(" HOLD ") == "hold"

    def test_whitespace_stripped(self):
        assert normalize_playbook_stage("  observe  ") == "observe"

    def test_none_returns_none(self):
        assert normalize_playbook_stage(None) is None

    def test_empty_returns_none(self):
        assert normalize_playbook_stage("") is None
        assert normalize_playbook_stage("   ") is None

    def test_unknown_returns_none_NOT_hold(self):
        """This is the hardest safety constraint in PLAYBOOK-001."""
        assert normalize_playbook_stage("unknown") is None
        assert normalize_playbook_stage("buy") is None
        assert normalize_playbook_stage("BUY") is None
        assert normalize_playbook_stage("random_stage") is None

    def test_non_string_returns_none(self):
        assert normalize_playbook_stage(123) is None
        assert normalize_playbook_stage([]) is None


# ── Lot status enums ────────────────────────────────────────────────────────

class TestLotStatusEnums:
    def test_trial_lot_statuses(self):
        assert TRIAL_LOT_STATUSES == [
            TRIAL_LOT_NONE, TRIAL_LOT_BUILT, TRIAL_LOT_FAILED, TRIAL_LOT_SUCCEEDED,
        ]
        assert len(TRIAL_LOT_STATUSES) == 4

    def test_confirm_lot_statuses(self):
        assert CONFIRM_LOT_STATUSES == [
            CONFIRM_LOT_NONE, CONFIRM_LOT_ELIGIBLE, CONFIRM_LOT_ADDED, CONFIRM_LOT_CANCELLED,
        ]
        assert len(CONFIRM_LOT_STATUSES) == 4

    def test_attack_lot_statuses(self):
        assert ATTACK_LOT_STATUSES == [
            ATTACK_LOT_NONE, ATTACK_LOT_PULLBACK, ATTACK_LOT_BREAKOUT,
            ATTACK_LOT_ADDED, ATTACK_LOT_RETREAT,
        ]
        assert len(ATTACK_LOT_STATUSES) == 5

    def test_all_lot_statuses_are_strings(self):
        for s in TRIAL_LOT_STATUSES + CONFIRM_LOT_STATUSES + ATTACK_LOT_STATUSES:
            assert isinstance(s, str) and len(s) > 0


# ── PlaybookContract dataclass ──────────────────────────────────────────────

class TestPlaybookContractDefaults:
    def test_all_fields_default_none(self):
        c = PlaybookContract()
        assert c.playbook_stage is None
        assert c.industry_evidence_score is None
        assert c.earnings_validation_score is None
        assert c.fund_confirmation_score is None
        assert c.risk_pressure_score is None
        assert c.planned_max_position_pct is None
        assert c.current_position_pct is None
        assert c.core_position_qty is None
        assert c.tactical_position_qty is None
        assert c.defensive_cash_required_pct is None
        assert c.trial_lot_status is None
        assert c.confirm_lot_status is None
        assert c.attack_lot_status is None
        assert c.allow_add is None
        assert c.allow_replenish is None
        assert c.allow_chase is None
        assert c.add_trigger is None
        assert c.reduce_trigger is None
        assert c.exit_trigger is None
        assert c.investment_thesis is None
        assert c.last_operation is None
        assert c.next_action is None
        assert c.notes is None
        assert c.stage_updated_at is None

    def test_field_count_covers_doc_contract(self):
        from dataclasses import fields
        names = {f.name for f in fields(PlaybookContract)}
        expected = {
            "playbook_stage",
            "industry_evidence_score", "earnings_validation_score",
            "fund_confirmation_score", "risk_pressure_score",
            "planned_max_position_pct", "current_position_pct",
            "unrealized_pnl_pct",
            "core_position_qty", "tactical_position_qty",
            "defensive_cash_required_pct",
            "trial_lot_status", "confirm_lot_status", "attack_lot_status",
            "allow_add", "allow_replenish", "allow_chase",
            "add_trigger", "reduce_trigger", "exit_trigger",
            "investment_thesis", "last_operation", "next_action", "notes",
            "stage_updated_at",
        }
        assert expected.issubset(names)


# ── from_dict ───────────────────────────────────────────────────────────────

class TestPlaybookContractFromDict:
    def test_none_returns_empty_contract(self):
        c = playbook_contract_from_dict(None)
        assert playbook_contract_is_empty(c)

    def test_non_dict_returns_empty(self):
        c = playbook_contract_from_dict("not a dict")
        assert playbook_contract_is_empty(c)
        c2 = playbook_contract_from_dict([1, 2])
        assert playbook_contract_is_empty(c2)

    def test_empty_dict_returns_empty(self):
        c = playbook_contract_from_dict({})
        assert playbook_contract_is_empty(c)

    def test_full_valid_dict(self):
        data = {
            "playbook_stage": "confirm",
            "industry_evidence_score": 4,
            "earnings_validation_score": 3.5,
            "fund_confirmation_score": 2,
            "risk_pressure_score": 1,
            "planned_max_position_pct": 15,
            "current_position_pct": 5,
            "core_position_qty": 100,
            "tactical_position_qty": 50,
            "defensive_cash_required_pct": 20,
            "trial_lot_status": "succeeded",
            "confirm_lot_status": "eligible",
            "attack_lot_status": "none",
            "allow_add": True,
            "allow_replenish": False,
            "allow_chase": True,
            "add_trigger": "回踩缩量企稳",
            "reduce_trigger": "跌破10日线放量",
            "exit_trigger": "业绩低于预期",
            "investment_thesis": "政策驱动的产业链受益",
            "notes": "观察量能变化",
        }
        c = playbook_contract_from_dict(data)
        assert c.playbook_stage == "confirm"
        assert c.industry_evidence_score == 4.0
        assert c.earnings_validation_score == 3.5
        assert c.allow_add is True
        assert c.allow_replenish is False
        assert c.trial_lot_status == "succeeded"

    def test_unknown_keys_ignored(self):
        data = {"playbook_stage": "trial", "random_field": 42, "foo": "bar"}
        c = playbook_contract_from_dict(data)
        assert c.playbook_stage == "trial"

    def test_unknown_stage_normalized_to_none(self):
        """Critical: bad stage must not become hold."""
        data = {"playbook_stage": "random_stage"}
        c = playbook_contract_from_dict(data)
        assert c.playbook_stage is None

    def test_score_clamped_high(self):
        c = playbook_contract_from_dict({"industry_evidence_score": 99})
        assert c.industry_evidence_score == SCORE_MAX

    def test_score_clamped_low(self):
        c = playbook_contract_from_dict({"industry_evidence_score": -5})
        assert c.industry_evidence_score == SCORE_MIN

    def test_score_invalid_returns_none(self):
        c = playbook_contract_from_dict({"industry_evidence_score": "abc"})
        assert c.industry_evidence_score is None

    def test_position_pct_clamped(self):
        c = playbook_contract_from_dict({"planned_max_position_pct": 150})
        assert c.planned_max_position_pct == POSITION_PCT_MAX
        c2 = playbook_contract_from_dict({"current_position_pct": -10})
        assert c2.current_position_pct == POSITION_PCT_MIN

    def test_unrealized_pnl_pct_is_preserved_and_non_finite_is_dropped(self):
        c = playbook_contract_from_dict({"unrealized_pnl_pct": -12.5})
        assert c.unrealized_pnl_pct == -12.5
        summary = playbook_contract_summary(c)
        assert summary["unrealized_pnl_pct"] == -12.5

        dirty = playbook_contract_from_dict({"unrealized_pnl_pct": "nan"})
        assert dirty.unrealized_pnl_pct is None

    def test_lot_status_unknown_returns_none(self):
        c = playbook_contract_from_dict({"trial_lot_status": "invalid"})
        assert c.trial_lot_status is None
        c2 = playbook_contract_from_dict({"confirm_lot_status": "unknown"})
        assert c2.confirm_lot_status is None

    def test_lot_status_case_insensitive(self):
        c = playbook_contract_from_dict({"trial_lot_status": "BUILT"})
        assert c.trial_lot_status == "built"

    def test_bool_normalization(self):
        c = playbook_contract_from_dict({"allow_add": "true", "allow_replenish": 0})
        assert c.allow_add is True
        assert c.allow_replenish is False

    def test_bool_invalid_returns_none(self):
        c = playbook_contract_from_dict({"allow_chase": "maybe"})
        assert c.allow_chase is None

    def test_numeric_bool_only_accepts_zero_or_one(self):
        c = playbook_contract_from_dict({
            "allow_add": 2,
            "allow_replenish": -1,
            "allow_chase": 1,
        })
        assert c.allow_add is None
        assert c.allow_replenish is None
        assert c.allow_chase is True

    def test_int_qty_from_string(self):
        c = playbook_contract_from_dict({"core_position_qty": "100"})
        assert c.core_position_qty == 100

    def test_non_finite_qty_returns_none(self):
        c = playbook_contract_from_dict({
            "core_position_qty": "inf",
            "tactical_position_qty": float("inf"),
        })
        assert c.core_position_qty is None
        assert c.tactical_position_qty is None

    def test_empty_string_becomes_none(self):
        c = playbook_contract_from_dict({"add_trigger": "", "notes": "   "})
        assert c.add_trigger is None
        assert c.notes is None

    def test_old_data_without_playbook_fields(self):
        """Backward compat: old observation/report dicts work fine."""
        old_data = {"symbol": "000001.SZ", "decision": "HOLD", "status": "watching"}
        c = playbook_contract_from_dict(old_data)
        assert playbook_contract_is_empty(c)


# ── to_dict / merge / is_empty / summary ────────────────────────────────────

class TestPlaybookContractToDict:
    def test_roundtrip(self):
        c = PlaybookContract(playbook_stage="attack", industry_evidence_score=4)
        d = playbook_contract_to_dict(c)
        assert d["playbook_stage"] == "attack"
        assert d["industry_evidence_score"] == 4.0

    def test_none_values_preserved(self):
        c = PlaybookContract(playbook_stage="trial")
        d = playbook_contract_to_dict(c)
        assert d["playbook_stage"] == "trial"
        assert d["industry_evidence_score"] is None

    def test_non_contract_returns_empty(self):
        assert playbook_contract_to_dict("not a contract") == {}


class TestMergeIntoDict:
    def test_merges_non_none_fields(self):
        target = {"symbol": "000001.SZ", "name": "test"}
        c = PlaybookContract(playbook_stage="confirm", allow_add=True)
        result = merge_playbook_contract_into_dict(target, c)
        assert result["symbol"] == "000001.SZ"
        assert result["playbook_stage"] == "confirm"
        assert result["allow_add"] is True

    def test_does_not_overwrite_existing(self):
        target = {"playbook_stage": "hold"}
        c = PlaybookContract(playbook_stage="trial")
        result = merge_playbook_contract_into_dict(target, c)
        assert result["playbook_stage"] == "hold"

    def test_none_contract_returns_copy(self):
        target = {"a": 1}
        result = merge_playbook_contract_into_dict(target, None)
        assert result == {"a": 1}
        assert result is not target

    def test_empty_contract_no_op(self):
        target = {"a": 1}
        c = PlaybookContract()
        result = merge_playbook_contract_into_dict(target, c)
        assert result == {"a": 1}

    def test_original_target_not_mutated(self):
        target = {"a": 1}
        c = PlaybookContract(playbook_stage="trial")
        merge_playbook_contract_into_dict(target, c)
        assert "playbook_stage" not in target

    def test_null_placeholders_do_not_block_real_values(self):
        target = {
            "playbook_stage": None,
            "playbook_contract": {"playbook_stage": None},
        }
        result = merge_playbook_contract_into_dict(
            target, PlaybookContract(playbook_stage="confirm")
        )
        assert result["playbook_stage"] == "confirm"
        assert result["playbook_contract"]["playbook_stage"] == "confirm"


class TestIsEmpty:
    def test_empty_contract(self):
        assert playbook_contract_is_empty(PlaybookContract()) is True

    def test_none(self):
        assert playbook_contract_is_empty(None) is True

    def test_non_empty(self):
        assert playbook_contract_is_empty(PlaybookContract(playbook_stage="trial")) is False
        assert playbook_contract_is_empty(PlaybookContract(notes="x")) is False


class TestStageLabel:
    @pytest.mark.parametrize("stage,label", [
        (STAGE_OBSERVE, "观察"),
        (STAGE_TRIAL, "试错仓"),
        (STAGE_CONFIRM, "确认仓"),
        (STAGE_ATTACK, "进攻仓"),
        (STAGE_HOLD, "持有"),
        (STAGE_RISK, "风控"),
        (STAGE_EXIT, "退出"),
    ])
    def test_labels(self, stage, label):
        assert playbook_stage_label(stage) == label

    def test_none_returns_empty(self):
        assert playbook_stage_label(None) == ""

    def test_unknown_returns_empty_not_hold(self):
        assert playbook_stage_label("unknown") == ""
        assert playbook_stage_label("buy") == ""


class TestSummary:
    def test_empty_contract_returns_empty_dict(self):
        assert playbook_contract_summary(PlaybookContract()) == {}
        assert playbook_contract_summary(None) == {}

    def test_summary_has_stage_and_label(self):
        c = PlaybookContract(playbook_stage="confirm", industry_evidence_score=4)
        s = playbook_contract_summary(c)
        assert s["playbook_stage"] == "confirm"
        assert s["playbook_stage_label"] == "确认仓"
        assert s["industry_evidence_score"] == 4.0

    def test_summary_includes_scores(self):
        c = PlaybookContract(
            industry_evidence_score=3,
            earnings_validation_score=4,
            fund_confirmation_score=2,
            risk_pressure_score=1,
        )
        s = playbook_contract_summary(c)
        assert s["industry_evidence_score"] == 3.0
        assert s["earnings_validation_score"] == 4.0
        assert s["fund_confirmation_score"] == 2.0
        assert s["risk_pressure_score"] == 1.0

    def test_summary_includes_positions(self):
        c = PlaybookContract(planned_max_position_pct=15, current_position_pct=5)
        s = playbook_contract_summary(c)
        assert s["planned_max_position_pct"] == 15.0
        assert s["current_position_pct"] == 5.0

    def test_summary_includes_lot_status(self):
        c = PlaybookContract(trial_lot_status="built", confirm_lot_status="eligible")
        s = playbook_contract_summary(c)
        assert s["trial_lot_status"] == "built"
        assert s["confirm_lot_status"] == "eligible"

    def test_summary_excludes_none_only_fields(self):
        c = PlaybookContract(playbook_stage="trial")
        s = playbook_contract_summary(c)
        assert "allow_add" not in s
        assert "add_trigger" not in s


# ── Safety ──────────────────────────────────────────────────────────────────

class TestSafetyConstraints:
    def test_forbidden_words_defined(self):
        assert len(FORBIDDEN_STRONG_WORDS) > 0
        assert "立即买入" in FORBIDDEN_STRONG_WORDS
        assert "满仓" in FORBIDDEN_STRONG_WORDS

    def test_assert_no_strong_action_words_passes_clean(self):
        assert_no_strong_action_words("回踩缩量企稳后加仓")

    def test_assert_no_strong_action_words_raises(self):
        with pytest.raises(AssertionError):
            assert_no_strong_action_words("立即买入")

    def test_assert_passes_none(self):
        assert_no_strong_action_words(None)
        assert_no_strong_action_words("")

    def test_validate_safety_clean_contract(self):
        c = PlaybookContract(
            add_trigger="回踩缩量企稳",
            reduce_trigger="跌破关键位",
            notes="观察量能",
        )
        assert validate_playbook_contract_safety(c) is True

    def test_validate_safety_detects_violation(self):
        c = PlaybookContract(add_trigger="满仓梭哈")
        assert validate_playbook_contract_safety(c) is False

    def test_validate_safety_none(self):
        assert validate_playbook_contract_safety(None) is True

    def test_validate_safety_all_text_fields(self):
        for field_name in ["add_trigger", "reduce_trigger", "exit_trigger",
                           "investment_thesis", "last_operation", "next_action", "notes"]:
            kwargs = {field_name: "立即清仓"}
            c = PlaybookContract(**kwargs)
            assert validate_playbook_contract_safety(c) is False, \
                f"field {field_name} should be checked"


# ── Integration: API schemas carry optional playbook fields ─────────────────

class TestObservationItemResponseIntegration:
    def test_playbook_fields_exist(self):
        from api.tradeflow_schemas import ObservationItemResponse
        item = ObservationItemResponse(symbol="000001.SZ")
        assert item.playbook_stage is None
        assert item.playbook_contract == {}

    def test_playbook_stage_can_be_set(self):
        from api.tradeflow_schemas import ObservationItemResponse
        item = ObservationItemResponse(symbol="000001.SZ", playbook_stage="trial")
        assert item.playbook_stage == "trial"

    def test_old_data_without_playbook_fields(self):
        from api.tradeflow_schemas import ObservationItemResponse
        item = ObservationItemResponse(symbol="000001.SZ", status="watching")
        assert item.playbook_stage is None
        assert item.playbook_contract == {}

    def test_create_request_accepts_persisted_playbook_stage(self):
        from api.tradeflow_schemas import ObservationItemCreateRequest
        req = ObservationItemCreateRequest(
            symbol="000001.SZ",
            playbook_stage="trial",
            playbook_contract={"industry_evidence_score": 3},
        )
        assert req.playbook_stage == "trial"
        assert req.playbook_contract["industry_evidence_score"] == 3

    def test_update_request_accepts_persisted_playbook_stage(self):
        from api.tradeflow_schemas import ObservationItemUpdateRequest
        req = ObservationItemUpdateRequest(playbook_stage="confirm")
        assert req.playbook_stage == "confirm"

    def test_create_request_carries_playbook_contract(self):
        from api.tradeflow_schemas import ObservationItemCreateRequest
        req = ObservationItemCreateRequest(
            symbol="000001.SZ", playbook_stage="trial"
        )
        assert req.model_dump()["playbook_stage"] == "trial"

    def test_update_request_carries_playbook_contract(self):
        from api.tradeflow_schemas import ObservationItemUpdateRequest
        req = ObservationItemUpdateRequest(playbook_stage="trial")
        assert req.model_dump()["playbook_stage"] == "trial"

    def test_bulk_upsert_item_accepts_playbook_stage(self):
        from api.tradeflow_schemas import ObservationBulkUpsertItem
        item = ObservationBulkUpsertItem(symbol="000001.SZ", playbook_stage="trial")
        assert item.playbook_stage == "trial"


class TestReportResponseIntegration:
    def _make_report(self, **overrides):
        from api.main import ReportResponse
        base = dict(
            id="1",
            user_id="u1",
            symbol="000001.SZ",
            trade_date="2026-07-10",
            decision="HOLD",
            direction="中性",
            confidence=50,
            target_price=None,
            stop_loss_price=None,
        )
        base.update(overrides)
        return ReportResponse(**base)

    def test_playbook_fields_exist(self):
        resp = self._make_report()
        assert resp.playbook_stage is None
        assert resp.playbook_summary is None

    def test_playbook_stage_can_be_set(self):
        resp = self._make_report(playbook_stage="hold")
        assert resp.playbook_stage == "hold"

    def test_old_report_without_playbook_fields(self):
        resp = self._make_report(decision="HOLD", execution_action="HOLD")
        assert resp.playbook_stage is None
        assert resp.playbook_summary is None

    def test_serialization_roundtrip(self):
        resp = self._make_report(
            playbook_stage="confirm",
            playbook_summary={"playbook_stage": "confirm", "industry_evidence_score": 4},
        )
        d = resp.model_dump()
        assert d["playbook_stage"] == "confirm"
        assert d["playbook_summary"]["industry_evidence_score"] == 4

    def test_report_fields_mirror_result_data(self):
        from types import SimpleNamespace
        from api.main import _attach_report_data_blockers_for_response

        report = SimpleNamespace(
            symbol="000001.SZ",
            result_data={
                "playbook_stage": "confirm",
                "playbook_summary": {"playbook_stage": "confirm"},
            },
        )
        _attach_report_data_blockers_for_response(report)
        assert report.playbook_stage == "confirm"
        assert report.playbook_summary == {
            "playbook_stage": "confirm",
            "playbook_stage_label": "确认仓",
        }

    def test_report_fields_normalize_dirty_result_data(self):
        from types import SimpleNamespace
        from api.main import _attach_report_data_blockers_for_response

        report = SimpleNamespace(
            symbol="000001.SZ",
            result_data={
                "playbook_stage": "HOLD",
                "playbook_summary": {
                    "playbook_stage": "not-a-stage",
                    "industry_evidence_score": 9,
                },
            },
        )
        _attach_report_data_blockers_for_response(report)
        assert report.playbook_stage == "hold"
        assert report.playbook_summary == {
            "playbook_stage": "hold",
            "playbook_stage_label": "持有",
            "industry_evidence_score": 5.0,
        }

    def test_report_playbook_write_and_response_roundtrip(self):
        from types import SimpleNamespace
        from api.main import _attach_report_data_blockers_for_response
        from api.services.report_service import attach_report_playbook_contract

        result_data = attach_report_playbook_contract(
            {"final_trade_decision": "观察"},
            playbook_stage="trial",
            playbook_contract={"industry_evidence_score": 4},
        )
        report = SimpleNamespace(symbol="000001.SZ", result_data=result_data)
        _attach_report_data_blockers_for_response(report)
        assert report.playbook_stage == "trial"
        assert report.playbook_summary["industry_evidence_score"] == 4.0

    def test_summary_response_does_not_advertise_deferred_playbook_fields(self):
        from api.main import ReportListResponse, ReportSummaryResponse

        assert "playbook_stage" not in ReportSummaryResponse.model_fields
        assert "playbook_summary" not in ReportSummaryResponse.model_fields
        reports_field = ReportListResponse.model_fields["reports"]
        assert "ReportSummaryResponse" in str(reports_field.annotation)


# ── End-to-end: dict → contract → merge → summary ──────────────────────────

class TestEndToEndFlow:
    def test_raw_dict_to_summary(self):
        raw = {
            "playbook_stage": "attack",
            "industry_evidence_score": 5,
            "fund_confirmation_score": 4,
            "planned_max_position_pct": 15,
            "current_position_pct": 10,
            "trial_lot_status": "succeeded",
            "confirm_lot_status": "added",
            "attack_lot_status": "pullback",
            "allow_add": True,
        }
        c = playbook_contract_from_dict(raw)
        assert c.playbook_stage == "attack"

        summary = playbook_contract_summary(c)
        assert summary["playbook_stage"] == "attack"
        assert summary["playbook_stage_label"] == "进攻仓"
        assert summary["industry_evidence_score"] == 5.0

    def test_dirty_data_safe_normalization(self):
        dirty = {
            "playbook_stage": "RANDOM",  # unknown -> None
            "industry_evidence_score": 999,  # clamp to 5
            "current_position_pct": -50,  # clamp to 0
            "trial_lot_status": "XYZ",  # unknown -> None
            "allow_add": "yes",  # -> True
            "add_trigger": "立即清仓撤退",  # strong word!
        }
        c = playbook_contract_from_dict(dirty)
        assert c.playbook_stage is None
        assert c.industry_evidence_score == 5.0
        assert c.current_position_pct == 0.0
        assert c.trial_lot_status is None
        assert c.allow_add is True
        # Safety check catches the strong word
        assert validate_playbook_contract_safety(c) is False

    def test_non_finite_numeric_values_become_missing(self):
        c = playbook_contract_from_dict({
            "industry_evidence_score": "nan",
            "current_position_pct": float("inf"),
        })
        assert c.industry_evidence_score is None
        assert c.current_position_pct is None

    def test_merge_into_observation_dict(self):
        obs = {"symbol": "000001.SZ", "status": "watching", "name": "test"}
        c = PlaybookContract(
            playbook_stage="trial",
            industry_evidence_score=3,
            planned_max_position_pct=5,
        )
        merged = merge_playbook_contract_into_dict(obs, c)
        assert merged["symbol"] == "000001.SZ"
        assert merged["playbook_stage"] == "trial"
        assert merged["industry_evidence_score"] == 3.0
        assert merged["playbook_contract"] == {
            "playbook_stage": "trial",
            "industry_evidence_score": 3.0,
            "planned_max_position_pct": 5.0,
        }

    def test_empty_contract_no_pollution(self):
        obs = {"symbol": "000001.SZ"}
        c = PlaybookContract()
        merged = merge_playbook_contract_into_dict(obs, c)
        assert "playbook_stage" not in merged


# ── Backward compatibility regression ───────────────────────────────────────

class TestBackwardCompat:
    def test_old_report_dict_no_crash(self):
        """Simulate an old report result_data without playbook fields."""
        old_result_data = {
            "decision": "HOLD",
            "direction": "中性",
            "execution_action": "HOLD",
            "market_report": "市场平稳",
        }
        c = playbook_contract_from_dict(old_result_data)
        assert playbook_contract_is_empty(c)
        assert playbook_contract_summary(c) == {}

    def test_old_observation_dict_no_crash(self):
        old_obs = {
            "symbol": "000001.SZ",
            "status": "watching",
            "entry_low": 10.0,
            "entry_high": 12.0,
        }
        c = playbook_contract_from_dict(old_obs)
        assert playbook_contract_is_empty(c)

    def test_stage_none_not_hold_in_summary(self):
        c = playbook_contract_from_dict({"playbook_stage": None})
        assert c.playbook_stage is None
        summary = playbook_contract_summary(c)
        assert "playbook_stage" not in summary
        assert "playbook_stage_label" not in summary


# ── Mapping doc exists ──────────────────────────────────────────────────────

class TestDocMappingTable:
    def test_doc_has_mapping_section(self):
        from pathlib import Path
        doc = Path(__file__).parent.parent / "docs" / "trade_playbook_lifecycle.md"
        content = doc.read_text(encoding="utf-8")
        assert "## 11. 字段与代码 schema 映射表" in content
        assert "PlaybookContract" in content
        assert "normalize_playbook_stage" in content

    def test_doc_mentions_all_stages(self):
        from pathlib import Path
        doc = Path(__file__).parent.parent / "docs" / "trade_playbook_lifecycle.md"
        content = doc.read_text(encoding="utf-8")
        for stage in PLAYBOOK_STAGES:
            assert stage in content
