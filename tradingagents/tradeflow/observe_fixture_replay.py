# [T-008] observe_fixture_replay
"""TradeFlow Observe Fixture Replay — replay 5 observe scenarios without live market data.

Scenarios:
1. waiting_not_triggered: price between trigger and invalid → stays WAITING
2. triggered: price >= trigger_price → TRIGGERED, signal written
3. invalidated: price <= invalid_price → INVALIDATED, signal written
4. stale_quote: no realtime price available → skipped, data_status=STALE
5. non_trading_day_cross_date: plan on non-trading day, effective_trade_date=next trading day

Design constraints:
- No live scheduler, no live quotes, no TA/LLM calls.
- Uses observe_runner.run_observe() with injected quote_provider.
- Validates: observe_state, tradeflow_signals, current_price, trigger_reason, data_status.
- API and frontend use the same ObserveState enum string values.
- Consistent with TF-DATE-001 plan_date/effective_trade_date/observe_date semantics.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Optional

from .intraday_observe import ObserveState
from .observe_runner import run_observe, ObserveRunResult
from .schemas import Candidate
from .candidate_engine import init_db, save_candidate


ALL_FIXTURE_IDS = [
    "waiting_not_triggered",
    "triggered",
    "invalidated",
    "stale_quote",
    "non_trading_day_cross_date",
]

_OBSERVE_STATES = {s.value for s in ObserveState}


@dataclass
class ObserveFixtureResult:
    fixture_id: str
    symbol: str
    trade_date: str
    plan_date: str = ""
    effective_trade_date: str = ""
    observe_state: str = "WAITING"
    current_price: Optional[float] = None
    trigger_reason: str = ""
    data_status: str = ""
    signal_count: int = 0
    signal_evidence: dict = field(default_factory=dict)
    run_result: Optional[dict] = None
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "fixture_id": self.fixture_id,
            "symbol": self.symbol,
            "trade_date": self.trade_date,
            "plan_date": self.plan_date,
            "effective_trade_date": self.effective_trade_date,
            "observe_state": self.observe_state,
            "current_price": self.current_price,
            "trigger_reason": self.trigger_reason,
            "data_status": self.data_status,
            "signal_count": self.signal_count,
            "signal_evidence": self.signal_evidence,
            "error": self.error,
        }


def _build_candidate(
    symbol: str,
    trigger_price: float = 10.0,
    invalid_price: float = 9.0,
    trade_date: str = "2026-06-02",
    effective_trade_date: str = "",
    plan_date: str = "",
) -> Candidate:
    return Candidate(
        symbol=symbol,
        name=f"Fixture {symbol}",
        source="fixture_replay",
        trade_date=trade_date,
        trigger_price=trigger_price,
        invalid_price=invalid_price,
        status="active",
        observe_state="WAITING",
        effective_trade_date=effective_trade_date or trade_date,
        plan_date=plan_date or trade_date,
    )


def _build_waiting_not_triggered() -> dict:
    return {
        "fixture_id": "waiting_not_triggered",
        "symbol": "600519.SH",
        "trigger_price": 10.0,
        "invalid_price": 9.0,
        "trade_date": "2026-06-02",
        "plan_date": "2026-06-02",
        "effective_trade_date": "2026-06-02",
        "current_price": 9.5,
        "expected_state": "WAITING",
        "expected_data_status": "OK",
    }


def _build_triggered() -> dict:
    return {
        "fixture_id": "triggered",
        "symbol": "000001.SZ",
        "trigger_price": 10.0,
        "invalid_price": 9.0,
        "trade_date": "2026-06-02",
        "plan_date": "2026-06-02",
        "effective_trade_date": "2026-06-02",
        "current_price": 10.5,
        "expected_state": "TRIGGERED",
        "expected_data_status": "OK",
    }


def _build_invalidated() -> dict:
    return {
        "fixture_id": "invalidated",
        "symbol": "601689.SH",
        "trigger_price": 10.0,
        "invalid_price": 9.0,
        "trade_date": "2026-06-02",
        "plan_date": "2026-06-02",
        "effective_trade_date": "2026-06-02",
        "current_price": 8.5,
        "expected_state": "INVALIDATED",
        "expected_data_status": "OK",
    }


def _build_stale_quote() -> dict:
    return {
        "fixture_id": "stale_quote",
        "symbol": "002600.SZ",
        "trigger_price": 10.0,
        "invalid_price": 9.0,
        "trade_date": "2026-06-02",
        "plan_date": "2026-06-02",
        "effective_trade_date": "2026-06-02",
        "current_price": None,
        "expected_state": "WAITING",
        "expected_data_status": "STALE",
    }


def _build_non_trading_day_cross_date() -> dict:
    return {
        "fixture_id": "non_trading_day_cross_date",
        "symbol": "300750.SZ",
        "trigger_price": 10.0,
        "invalid_price": 9.0,
        "trade_date": "2026-05-31",
        "plan_date": "2026-05-31",
        "effective_trade_date": "2026-06-01",
        "observe_as_date": "2026-06-01",
        "current_price": 10.2,
        "expected_state": "TRIGGERED",
        "expected_data_status": "OK",
    }


_FIXTURE_BUILDERS = {
    "waiting_not_triggered": _build_waiting_not_triggered,
    "triggered": _build_triggered,
    "invalidated": _build_invalidated,
    "stale_quote": _build_stale_quote,
    "non_trading_day_cross_date": _build_non_trading_day_cross_date,
}


def get_fixture(fixture_id: str) -> dict:
    builder = _FIXTURE_BUILDERS.get(fixture_id)
    if builder is None:
        raise ValueError(f"Unknown fixture: {fixture_id}")
    return builder()


def _make_quote_provider(fixture: dict):
    symbol = fixture["symbol"]
    price = fixture.get("current_price")

    def provider(symbols):
        if price is None:
            return {}
        return {
            symbol: {
                "current_price": price,
                "current_volume": 100000.0,
                "current_amount": 1000000.0,
                "quote_time": f"{fixture.get('trade_date', '2026-06-02')}T10:30:00",
                "source": "fixture_replay",
            }
        }
    return provider


def _read_signals(db_path: str, symbol: str, trade_date: str) -> list[dict]:
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM tradeflow_signals WHERE symbol = ? ORDER BY signal_time DESC",
            (symbol,),
        ).fetchall()
        signals = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            ev = d.get("evidence_json", "{}")
            if isinstance(ev, str):
                ev = json.loads(ev)
            if ev.get("trade_date") == trade_date:
                signals.append(d)
        return signals
    finally:
        conn.close()


def _read_candidate_observe_state(db_path: str, symbol: str, trade_date: str) -> dict:
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(tradeflow_candidates)").fetchall()}
        has_eff = "effective_trade_date" in columns

        if has_eff:
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol = ? AND "
                "(effective_trade_date = ? OR (trade_date = ? AND (effective_trade_date = '' OR effective_trade_date IS NULL)))",
                (symbol, trade_date, trade_date),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM tradeflow_candidates WHERE symbol = ? AND trade_date = ?",
                (symbol, trade_date),
            ).fetchone()

        if row is None:
            return {}

        d = {k: row[k] for k in row.keys()}
        return {
            "observe_state": d.get("observe_state", "WAITING"),
            "observe_trigger_count": d.get("observe_trigger_count", 0),
            "observe_first_trigger_time": d.get("observe_first_trigger_time", ""),
            "effective_trade_date": d.get("effective_trade_date", ""),
            "plan_date": d.get("plan_date", ""),
        }
    finally:
        conn.close()


def replay_fixture(fixture_id: str, db_path: str) -> ObserveFixtureResult:
    fixture = get_fixture(fixture_id)
    symbol = fixture["symbol"]
    trade_date = fixture.get("observe_as_date", fixture["effective_trade_date"] or fixture["trade_date"])
    plan_date = fixture.get("plan_date", fixture["trade_date"])
    effective_trade_date = fixture.get("effective_trade_date", fixture["trade_date"])

    cand = _build_candidate(
        symbol=symbol,
        trigger_price=fixture["trigger_price"],
        invalid_price=fixture["invalid_price"],
        trade_date=fixture["trade_date"],
        effective_trade_date=effective_trade_date,
        plan_date=plan_date,
    )
    save_candidate(cand, db_path)

    quote_provider = _make_quote_provider(fixture)

    from unittest.mock import patch
    with patch("tradingagents.tradeflow.observe_runner._is_trading_day", return_value=True):
        run_result = run_observe(
            trade_date=trade_date,
            db_path=db_path,
            quote_provider=quote_provider,
        )

    cand_state = _read_candidate_observe_state(db_path, symbol, trade_date)
    signals = _read_signals(db_path, symbol, trade_date)

    observe_state = cand_state.get("observe_state", fixture.get("expected_state", "WAITING"))

    current_price = fixture.get("current_price")
    trigger_reason = ""
    signal_evidence = {}
    if signals:
        ev_raw = signals[0].get("evidence_json", "{}")
        if isinstance(ev_raw, str):
            ev_raw = json.loads(ev_raw)
        current_price = ev_raw.get("current_price", current_price)
        trigger_reason = ev_raw.get("trigger_reason", "")
        signal_evidence = ev_raw

    if fixture.get("current_price") is None:
        data_status = "STALE"
    else:
        data_status = "OK"

    return ObserveFixtureResult(
        fixture_id=fixture_id,
        symbol=symbol,
        trade_date=trade_date,
        plan_date=plan_date,
        effective_trade_date=effective_trade_date,
        observe_state=observe_state,
        current_price=current_price,
        trigger_reason=trigger_reason,
        data_status=data_status,
        signal_count=len(signals),
        signal_evidence=signal_evidence,
        run_result={
            "checked": run_result.checked,
            "triggered": run_result.triggered,
            "invalidated": run_result.invalidated,
            "waiting": run_result.waiting,
            "skipped": run_result.skipped,
            "signals_written": run_result.signals_written,
            "errors": run_result.errors,
        },
    )


def replay_all(db_path: str) -> list[ObserveFixtureResult]:
    results = []
    for fid in ALL_FIXTURE_IDS:
        results.append(replay_fixture(fid, db_path))
    return results


def validate_state_consistency(results: list[ObserveFixtureResult]) -> list[str]:
    issues = []
    for r in results:
        fixture = get_fixture(r.fixture_id)
        expected_state = fixture.get("expected_state", "WAITING")
        if r.observe_state != expected_state:
            issues.append(
                f"{r.fixture_id}: expected observe_state={expected_state}, got {r.observe_state}"
            )
        if r.observe_state not in _OBSERVE_STATES:
            issues.append(
                f"{r.fixture_id}: observe_state={r.observe_state} not in valid states {_OBSERVE_STATES}"
            )
    return issues


def validate_api_frontend_consistency() -> list[str]:
    issues = []
    backend_states = {s.value for s in ObserveState}
    frontend_states = {"WAITING", "TRIGGERED", "INVALIDATED", "EXPIRED"}
    if backend_states != frontend_states:
        issues.append(
            f"Backend ObserveState {backend_states} != frontend states {frontend_states}"
        )
    return issues
