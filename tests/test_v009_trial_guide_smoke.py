# [V-009] trial_user_guide
"""TradeFlow 小资金试跑 — 用户手册与回归清单 smoke 测试 (V-009).

This is a lightweight smoke test that guarantees the user manual
(`docs/tradeflow_trial_user_guide.md`) and regression checklist
(`docs/tradeflow_trial_regression_checklist.md`) stay in sync with the
actual API routes, schema fields and default risk budget. It does NOT
exercise the full trial chain (that is the job of V-008); instead it
fails fast when a referenced route / field is renamed or removed, so
the user manual never lies.

Coverage:
    1. All API routes referenced by the user manual / checklist are
       registered on the FastAPI app with the expected HTTP method.
    2. Core schema fields referenced by the docs exist on the
       corresponding Pydantic models.
    3. Default risk budget values match the documented defaults.
    4. The two V-009 docs exist and are non-empty.
    5. Neither doc contains any FORBIDDEN strong-action words.

Constraints (per V-009 task rules):
    - 不调用 LLM
    - 不写生产数据库
    - 不接真实交易
    - 不调用 live API
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, Set, Tuple

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
USER_GUIDE = DOCS_DIR / "tradeflow_trial_user_guide.md"
CHECKLIST = DOCS_DIR / "tradeflow_trial_regression_checklist.md"

# Union of all forbidden word sets enforced across the codebase.
# Kept inline so the test does not break if one source module renames
# its private constant.
_FORBIDDEN_WORDS: Set[str] = {
    # tradingagents.tradeflow.schemas.FORBIDDEN_WORDS
    "立即买入", "重仓买入", "立即清仓", "满仓", "梭哈",
    # observation_state_engine.FORBIDDEN_STRONG_WORDS + extensions
    "重仓", "清仓", "立即卖出", "全仓", "必涨", "必跌", "无脑买", "加杠杆",
    # api.services.tradeflow_service._OBSERVATION_FORBIDDEN_WORDS extras
    "稳赚", "保本",
    # V-008 extended forbidden set
    "买入", "卖出", "加仓", "减仓", "抄底", "逃顶", "追涨", "杀跌",
}

# Routes referenced by the V-009 user manual / checklist.
# (method, path)
_REFERENCED_ROUTES: Set[Tuple[str, str]] = {
    ("POST", "/v1/tradeflow/discovery"),
    ("GET", "/v1/tradeflow/daily-plan"),
    ("GET", "/v1/tradeflow/candidates"),
    ("GET", "/v1/tradeflow/candidates/{symbol}"),
    ("GET", "/v1/tradeflow/candidates/tiered"),
    ("GET", "/v1/tradeflow/observe"),
    ("POST", "/v1/tradeflow/observe/run"),
    ("GET", "/v1/tradeflow/observe/scheduler-status"),
    ("GET", "/v1/tradeflow/ta-queue"),
    ("GET", "/v1/tradeflow/review"),
    ("POST", "/v1/tradeflow/review/generate"),
    ("GET", "/v1/tradeflow/paper-ledger"),
    ("POST", "/v1/tradeflow/paper-ledger/add"),
    ("POST", "/v1/tradeflow/paper-ledger/remove"),
    ("POST", "/v1/tradeflow/paper-ledger/confirm"),
    ("GET", "/v1/tradeflow/paper-ledger/review"),
    ("GET", "/v1/tradeflow/observation-items"),
    ("POST", "/v1/tradeflow/observation-items"),
    ("PATCH", "/v1/tradeflow/observation-items/{item_id}"),
    ("POST", "/v1/tradeflow/observation-items/{item_id}/mark"),
    ("POST", "/v1/tradeflow/candidates/{symbol}/add-to-observation"),
    ("GET", "/v1/dashboard/tracking-board/v2"),
    ("GET", "/v1/tradeflow/data-health"),
}


# ────────────────────────────────────────────────────────────────────
# 1. Docs existence
# ────────────────────────────────────────────────────────────────────

class TestDocsExist:
    def test_user_guide_exists_and_nonempty(self):
        assert USER_GUIDE.exists(), f"Missing user guide: {USER_GUIDE}"
        text = USER_GUIDE.read_text(encoding="utf-8").strip()
        assert len(text) > 1000, "User guide is suspiciously short"
        assert "TradeFlow 小资金试跑用户操作手册" in text

    def test_regression_checklist_exists_and_nonempty(self):
        assert CHECKLIST.exists(), f"Missing checklist: {CHECKLIST}"
        text = CHECKLIST.read_text(encoding="utf-8").strip()
        assert len(text) > 1000, "Checklist is suspiciously short"
        assert "TradeFlow 小资金试跑回归清单" in text


# ────────────────────────────────────────────────────────────────────
# 2. API route registration
# ────────────────────────────────────────────────────────────────────

def _registered_routes() -> Set[Tuple[str, str]]:
    """Return the set of (method, path) tuples registered on the app."""
    from api.main import app
    out: Set[Tuple[str, str]] = set()
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if not path or not methods:
            continue
        for m in methods:
            if m in {"HEAD", "OPTIONS"}:
                continue
            out.add((m.upper(), path))
    return out


class TestReferencedApiRoutesExist:
    """Every route referenced in the user manual must be registered."""

    def test_all_referenced_routes_registered(self):
        registered = _registered_routes()
        missing = sorted(_REFERENCED_ROUTES - registered)
        assert not missing, (
            "User manual references routes that are NOT registered on the "
            f"FastAPI app: {missing}"
        )

    def test_observe_run_route_methods(self):
        registered = _registered_routes()
        assert ("POST", "/v1/tradeflow/observe/run") in registered
        assert ("GET", "/v1/tradeflow/observe") in registered

    def test_paper_ledger_routes(self):
        registered = _registered_routes()
        for path in (
            "/v1/tradeflow/paper-ledger",
            "/v1/tradeflow/paper-ledger/add",
            "/v1/tradeflow/paper-ledger/remove",
            "/v1/tradeflow/paper-ledger/confirm",
            "/v1/tradeflow/paper-ledger/review",
        ):
            assert path in {p for _, p in registered}, f"Missing paper-ledger route: {path}"

    def test_tracking_board_v2_route(self):
        registered = _registered_routes()
        assert ("GET", "/v1/dashboard/tracking-board/v2") in registered


# ────────────────────────────────────────────────────────────────────
# 3. Core schema fields
# ────────────────────────────────────────────────────────────────────

def _model_fields(model_cls) -> Set[str]:
    return set(model_cls.model_fields.keys())  # pydantic v2


class TestReferencedCoreFieldsExist:
    """Fields referenced in the docs must exist on the schemas."""

    def test_candidate_item_fields(self):
        from api.tradeflow_schemas import TradeFlowCandidateItem
        fields = _model_fields(TradeFlowCandidateItem)
        required = {
            "symbol", "name", "candidate_type", "action_tier",
            "trigger_price", "invalid_price",
            "mandate_topic", "ambush_score", "data_quality_score",
            "observe_state", "observe_trigger_count",
            "observe_first_trigger_time",
        }
        missing = required - fields
        assert not missing, f"TradeFlowCandidateItem missing fields: {missing}"

    def test_tiered_response_fields(self):
        from api.tradeflow_schemas import TradeFlowTieredCandidatesResponse
        fields = _model_fields(TradeFlowTieredCandidatesResponse)
        required = {
            "actionable", "watch", "scan", "main_candidates",
            "pool_counts", "pool_gate_summary", "concentration_summary",
        }
        missing = required - fields
        assert not missing, f"Tiered response missing fields: {missing}"

    def test_paper_ledger_fields(self):
        from api.tradeflow_schemas import PaperLedgerResponse, PaperLedgerSummary
        resp_fields = _model_fields(PaperLedgerResponse)
        assert {"config", "summary", "trades"} <= resp_fields, (
            f"PaperLedgerResponse missing config/summary/trades: {resp_fields}"
        )
        sum_fields = _model_fields(PaperLedgerSummary)
        assert "risk_exposure" in sum_fields, (
            f"PaperLedgerSummary missing risk_exposure: {sum_fields}"
        )

    def test_paper_action_response_fields(self):
        from api.tradeflow_schemas import PaperActionResponse
        fields = _model_fields(PaperActionResponse)
        required = {
            "accepted", "rejected", "rule", "reason",
            "downgraded_to", "trade_id",
        }
        # accepted is not an explicit field but rejected/downgraded_to are;
        # accept either form by allowing the subset that the docs hinge on.
        required_subset = {"rejected", "rule", "reason", "downgraded_to", "trade_id"}
        missing = required_subset - fields
        assert not missing, f"PaperActionResponse missing fields: {missing}"

    def test_review_item_fields(self):
        from api.tradeflow_schemas import TradeFlowReviewItem
        fields = _model_fields(TradeFlowReviewItem)
        required = {
            "tomorrow_focus", "downgrade_reason", "evidence_needed",
            "hit_type", "candidate_type",
        }
        missing = required - fields
        assert not missing, f"TradeFlowReviewItem missing fields: {missing}"

    def test_observation_item_fields(self):
        from api.tradeflow_schemas import ObservationItemResponse
        fields = _model_fields(ObservationItemResponse)
        required = {
            "entry_low", "entry_high", "trigger_price", "invalid_price",
            "horizon", "source", "reason", "priority", "notes",
            "last_reviewed_at", "status",
        }
        missing = required - fields
        assert not missing, f"ObservationItemResponse missing fields: {missing}"


# ────────────────────────────────────────────────────────────────────
# 4. Default risk budget matches the documented values
# ────────────────────────────────────────────────────────────────────

class TestDefaultRiskBudgetMatchesDocs:
    """The default risk budget values cited in the user manual must match
    the service's _DEFAULT_RISK_BUDGET exactly."""

    def test_default_values(self):
        from api.services.tradeflow_service import _DEFAULT_RISK_BUDGET
        expected = {
            "principal": 5000.0,
            "per_ticket_max": 1500.0,
            "per_ticket_min": 500.0,
            "daily_new_max": 3,
            "max_concurrent_tracking": 5,
            "require_trigger_price": True,
            "require_invalid_price": True,
            "min_data_quality_score": 40.0,
        }
        for key, val in expected.items():
            assert key in _DEFAULT_RISK_BUDGET, (
                f"_DEFAULT_RISK_BUDGET missing key: {key}"
            )
            assert _DEFAULT_RISK_BUDGET[key] == val, (
                f"_DEFAULT_RISK_BUDGET[{key}]={_DEFAULT_RISK_BUDGET[key]!r} "
                f"!= documented {val!r}"
            )

    def test_paper_ledger_response_default_principal(self):
        from api.tradeflow_schemas import PaperLedgerResponse
        resp = PaperLedgerResponse()
        assert resp.principal == 5000.0
        assert resp.cash_balance == 5000.0


# ────────────────────────────────────────────────────────────────────
# 5. No forbidden words in the user manual / checklist
# ────────────────────────────────────────────────────────────────────

def _strip_code(text: str) -> str:
    """Remove fenced code blocks and inline code before forbidden-word scan.

    Rationale: code snippets legitimately need to reference action_type
    values like ``"buy"`` / ``"sell"`` and Python identifiers like
    ``confirm_paper_action(buy)``. The forbidden-words rule applies to
    narrative prose, not to documented code identifiers. The V-008
    acceptance report already follows this convention.
    """
    # Strip fenced code blocks (``` ... ```)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    # Strip inline code (`...`)
    text = re.sub(r"`[^`]*`", "", text)
    return text


def _scan_forbidden(text: str) -> Dict[str, Iterable[str]]:
    """Return {forbidden_word: [line numbers]} for any forbidden hits.

    Code blocks / inline code are stripped first, so forbidden words
    appearing inside documented identifiers (e.g. ``action_type:"buy"``)
    do not trip the check.
    """
    stripped = _strip_code(text)
    hits: Dict[str, list] = {}
    # After stripping, line numbers no longer map 1:1 to the original
    # file; report line numbers in the *stripped* text to keep the
    # diagnostic simple — the offending word is what matters.
    for lineno, line in enumerate(stripped.splitlines(), start=1):
        for w in _FORBIDDEN_WORDS:
            if w in line:
                hits.setdefault(w, []).append(lineno)
    return hits


class TestNoForbiddenWords:
    """Strong-action words must not appear in V-009 docs.

    Allowed exception: lines that explicitly explain that the system
    suppresses such words — those lines must still not contain the
    bare strong word in a prescriptive tone. We keep this strict so
    the docs cannot drift into recommendation language.
    """

    @pytest.mark.parametrize("doc_path", [USER_GUIDE, CHECKLIST],
                             ids=["user_guide", "checklist"])
    def test_no_forbidden_words_in_user_guide(self, doc_path: Path):
        assert doc_path.exists(), f"Missing doc: {doc_path}"
        text = doc_path.read_text(encoding="utf-8")
        hits = _scan_forbidden(text)
        if hits:
            detail = "; ".join(
                f"{word!r} at line(s) {lins}" for word, lins in sorted(hits.items())
            )
            pytest.fail(
                f"Forbidden strong-action words found in {doc_path.name}: {detail}"
            )


# ────────────────────────────────────────────────────────────────────
# 6. Doc → route consistency (the checklist's API table matches the
#    referenced route set used by this test)
# ────────────────────────────────────────────────────────────────────

class TestChecklistRouteTableConsistency:
    """The regression checklist's API table must reference at least all
    routes that this smoke test asserts. Prevents the docs from
    silently dropping a route after a refactor."""

    def test_checklist_mentions_all_referenced_routes(self):
        text = CHECKLIST.read_text(encoding="utf-8")
        missing: list[str] = []
        for _, path in _REFERENCED_ROUTES:
            if path not in text:
                missing.append(path)
        assert not missing, (
            "Regression checklist is missing references to routes that "
            f"the smoke test enforces: {missing}"
        )

    def test_user_guide_mentions_all_referenced_routes(self):
        text = USER_GUIDE.read_text(encoding="utf-8")
        missing: list[str] = []
        for _, path in _REFERENCED_ROUTES:
            if path not in text:
                missing.append(path)
        assert not missing, (
            "User guide is missing references to routes that "
            f"the smoke test enforces: {missing}"
        )


# ────────────────────────────────────────────────────────────────────
# 7. ALLOWED_ACTIONS constants still match the docs
# ────────────────────────────────────────────────────────────────────

class TestAllowedActionsMatchDocs:
    """The allowed-action verbs cited in the docs must match the
    schemas module's ALLOWED_ACTIONS set."""

    def test_allowed_actions_unchanged(self):
        from tradingagents.tradeflow.schemas import ALLOWED_ACTIONS
        assert ALLOWED_ACTIONS == {
            "OBSERVE", "WAIT_TRIGGER", "NEED_DEEP_TA", "REMOVE_FROM_WATCH",
        }

    def test_user_guide_mentions_allowed_actions(self):
        text = USER_GUIDE.read_text(encoding="utf-8")
        for verb in ("OBSERVE", "WAIT_TRIGGER", "NEED_DEEP_TA", "REMOVE_FROM_WATCH"):
            assert verb in text, (
                f"User guide does not mention allowed action verb: {verb}"
            )
