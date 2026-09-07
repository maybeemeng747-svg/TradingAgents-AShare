"""[UPSTREAM-081-001] Selective absorption of upstream 89754c5 (#202).

Covers the three real gaps the local tree had versus upstream v0.8.1:

1. Thread-pool saturation: `_fetch_all` gains a whole-round hard cap
   (``TA_DATA_FETCH_TIMEOUT``) so a stuck provider cannot pin the per-key
   lock and silently starve the asyncio default executor (origin 524).
   The `/healthz` starvation probe is observable and never re-executes jobs.
2. Stock identification: the deterministic regex path stays authoritative;
   when the intent LLM fails AND regex finds no code, the original text is
   resolved once against the warm local stock-name map (fail-closed on
   ambiguity / cold cache); unresolved text keeps the explicit error.
3. Error semantics: user-facing failure messages are humanized AND
   sanitized — they must never leak API keys, URLs, or internal stack
   frames.

Fix round 2 (review round2 3×P1 + 2×P2) additionally locks down:

4. The LHB force upgrade runs strictly inside the whole-round deadline.
5. All fetch rounds share a bounded daemon pool: leaked (stuck) threads are
   capped process-wide and observable on ``/healthz`` instead of growing
   one-thread-per-abandoned-worker per round.
6. ``Authorization: Bearer <token>`` (incl. JWT) is redacted whole, and the
   humanize idempotency shortcut can no longer bypass sanitization.
7. ``FETCH_LOCK_TIMEOUT`` is derived from the fetch budget so a custom
   ``TA_DATA_FETCH_TIMEOUT`` can no longer violate the queueing invariant.

Fix round 3 (review round3 2×P1) additionally locks down:

8. The shared pool's queue is bounded and enforceable: saturation raises an
   explicit rejection (counted on ``rejected_total``) instead of silently
   enqueuing work; at round timeout, queued (not-yet-started) futures are
   cancelled so they NEVER execute afterwards — the old behavior let
   "timed-out" tasks run once stuck workers freed up, escaping the
   whole-round deadline.
9. The forced LHB query obeys the same rule: after ``forced_timeout`` no
   force query may execute (queued futures are cancelled); a saturated pool
   degrades explicitly (``forced_rejected_saturated``) instead of faking a
   queued success.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

import api.main as api_main
from tradingagents.graph import data_collector as data_collector_module
from tradingagents.graph.data_collector import DataCollector, _run_bounded_fetch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTool:
    def __init__(self, name: str, result="ok", delay: float = 0.0) -> None:
        self.name = name
        self._result = result
        self._delay = delay

    def invoke(self, payload):
        time.sleep(self._delay)
        return self._result


class _GateTool(_FakeTool):
    """Simulates a stuck provider: invoke() blocks until the gate is set."""

    def __init__(self, name: str, gate: threading.Event) -> None:
        super().__init__(name, result="late")
        self._gate = gate

    def invoke(self, payload):
        self._gate.wait(timeout=10)
        return self._result


def _break_llm(monkeypatch, exc: Exception | None = None):
    """Make the intent-extraction LLM client fail like a dead provider."""
    from tradingagents.llm_clients import factory

    def _raise(*args, **kwargs):
        raise exc or ConnectionError(
            "Connection error: POST https://llm.example.invalid/v1/chat failed"
        )

    monkeypatch.setattr(factory, "create_llm_client", _raise)


def _warm_stock_map(monkeypatch, mapping: dict | None):
    monkeypatch.setattr(api_main, "_cn_stock_map", mapping)
    monkeypatch.setattr(
        api_main, "_cn_stock_reverse_map",
        {code: name for name, code in (mapping or {}).items()},
    )


def _ensure_test_db():
    from api.database import init_db

    init_db()


# ---------------------------------------------------------------------------
# 1. Thread-pool saturation: bounded fetch round
# ---------------------------------------------------------------------------


class TestBoundedFetchRound:
    def test_fetch_all_timeout_default_and_lock_margin(self):
        if "TA_DATA_FETCH_TIMEOUT" in os.environ or "TA_DATA_FETCH_LOCK_TIMEOUT" in os.environ:
            pytest.skip("custom fetch timeout envs configured")
        assert data_collector_module.FETCH_ALL_TIMEOUT == 300.0
        # Queueing collectors must be able to outlast a full round budget:
        # the lock timeout is derived from the fetch budget plus a margin
        # (fix round2 P2: a custom fetch timeout can no longer break this).
        assert (
            data_collector_module.FETCH_LOCK_TIMEOUT
            >= data_collector_module.FETCH_ALL_TIMEOUT + 60.0
        )

    def test_fast_sources_return_normally(self):
        tasks = {
            "stock_data": (_FakeTool("get_stock_data", "csv-data"), {}),
            "news": (_FakeTool("get_news", "news-data"), {}),
        }

        results, _hits = _run_bounded_fetch(tasks)

        assert results["stock_data"] == "csv-data"
        assert results["news"] == "news-data"

    def test_stuck_source_is_marked_failed_not_hung(self, monkeypatch):
        monkeypatch.setattr(data_collector_module, "FETCH_ALL_TIMEOUT", 0.2)
        tasks = {
            "stock_data": (_FakeTool("get_stock_data", delay=1.2), {}),
            "news": (_FakeTool("get_news", "news-data"), {}),
        }

        started = time.monotonic()
        results, _hits = _run_bounded_fetch(tasks)
        elapsed = time.monotonic() - started

        assert elapsed < 0.9, "round must not wait for the stuck worker"
        assert results["news"] == "news-data"
        timed_out = results["stock_data"]
        assert "数据拉取超时" in timed_out
        # The raw-evidence classifier must see FAILED, not fake success.
        assert DataCollector._infer_source_status(timed_out) == "FAILED"

    def test_abandoned_workers_do_not_block_the_round(self, monkeypatch):
        monkeypatch.setattr(data_collector_module, "FETCH_ALL_TIMEOUT", 0.1)

        release = threading.Event()

        class _BlockedTool(_FakeTool):
            def invoke(self, payload):
                release.wait(timeout=5)
                return "late"

        tasks = {"stock_data": (_BlockedTool("get_stock_data"), {})}
        try:
            started = time.monotonic()
            results, _hits = _run_bounded_fetch(tasks)
            elapsed = time.monotonic() - started

            assert elapsed < 0.8
            assert "数据拉取超时" in results["stock_data"]
        finally:
            release.set()


# ---------------------------------------------------------------------------
# 1b. Fix round2: shared bounded fetch pool (thread leaks capped + observable)
# ---------------------------------------------------------------------------


class TestSharedFetchPool:
    def test_pool_is_bounded_named_and_daemonized(self):
        pool = data_collector_module._BoundedFetchPool(2)
        gate = threading.Event()
        executed: list[str] = []

        def _blocked():
            gate.wait(timeout=10)
            return "x"

        def _queued_work(name: str):
            executed.append(name)
            return name

        try:
            running = [pool.submit(_blocked) for _ in range(2)]
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and pool.stats()["active"] < 2:
                time.sleep(0.02)

            queued = [
                pool.submit(lambda name=f"q{i}": _queued_work(name))
                for i in range(3)
            ]
            stats = pool.stats()
            assert stats["max_workers"] == 2
            assert stats["threads"] == 2
            assert stats["active"] == 2
            assert stats["queued"] == 3

            # Fix round3 deadline semantics: queued work past its round
            # deadline is cancelled and must NEVER execute once workers free
            # up (the old test expected every queued item to run eventually,
            # which is exactly the escaped-deadline regression).
            assert all(f.cancel() for f in queued)
        finally:
            gate.set()

        assert [f.result(timeout=10) for f in running] == ["x"] * 2
        time.sleep(0.1)
        assert executed == [], "cancelled queued tasks must never execute"
        assert all(t.name.startswith("ta-data-fetch-") for t in pool._threads)
        assert all(t.daemon for t in pool._threads)

    def test_submit_rejects_when_queue_is_full(self):
        """Fix round3: the queue is bounded; a saturated pool raises an
        explicit rejection (counted on ``rejected_total``) instead of
        silently enqueuing work that would run after its round deadline."""
        pool = data_collector_module._BoundedFetchPool(1, max_queued=2)
        gate = threading.Event()
        try:
            pool.submit(lambda: gate.wait(timeout=10))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and pool.stats()["active"] < 1:
                time.sleep(0.02)
            for _ in range(2):  # fill the only queue slots
                pool.submit(lambda: "queued")

            rejected_before = pool.stats()["rejected_total"]
            with pytest.raises(data_collector_module._FetchPoolSaturated):
                pool.submit(lambda: "overflow")
            assert pool.stats()["rejected_total"] == rejected_before + 1
        finally:
            gate.set()

    def test_submit_propagates_worker_exceptions(self):
        pool = data_collector_module._BoundedFetchPool(1)

        def _boom():
            raise ValueError("provider blew up")

        with pytest.raises(ValueError):
            pool.submit(_boom).result(timeout=5)

    def test_round_timeout_cancels_queued_work_so_it_never_runs(self, monkeypatch):
        """Regression (round3 P1): when stuck tasks occupy every worker, a
        round used to leave its queued futures alive — they executed after
        the round had already reported 超时跳过. The round must cancel
        queued futures at timeout so they never run, even after workers
        free up; abandoned_total must only count the already-running one."""
        monkeypatch.setattr(data_collector_module, "FETCH_ALL_TIMEOUT", 0.2)
        gate = threading.Event()
        test_pool = data_collector_module._BoundedFetchPool(1, max_queued=8)
        monkeypatch.setattr(data_collector_module, "_fetch_pool", test_pool)
        executed: list[str] = []

        class _QueuedTool(_FakeTool):
            def invoke(self, payload):
                executed.append(self.name)
                return "should-never-run"

        tasks = {
            "stock_data": (_GateTool("get_stock_data", gate), {}),  # occupies worker
            "news": (_QueuedTool("get_news", "news-data"), {}),  # stays queued
        }
        try:
            started = time.monotonic()
            results, _hits = _run_bounded_fetch(tasks)
            elapsed = time.monotonic() - started

            assert elapsed < 0.9
            assert "数据获取失败" in results["stock_data"]
            assert "数据获取失败" in results["news"]
            assert DataCollector._infer_source_status(results["news"]) == "FAILED"
        finally:
            gate.set()

        # Give a wrongly-persisted queue entry every chance to run.
        time.sleep(0.2)
        assert executed == [], "timed-out queued task must be cancelled, not run later"
        gauges = test_pool.stats()
        assert gauges["cancelled_total"] == 1
        assert gauges["abandoned_total"] == 1

    def test_bounded_fetch_degrades_explicitly_when_pool_saturated(self, monkeypatch):
        """Fix round3: submits into a saturated pool must produce explicit
        degraded status text (FAILED), never a silent drop or fake success."""
        gate = threading.Event()
        test_pool = data_collector_module._BoundedFetchPool(1, max_queued=1)
        monkeypatch.setattr(data_collector_module, "_fetch_pool", test_pool)
        executed: list[str] = []

        class _Probe(_FakeTool):
            def invoke(self, payload):
                executed.append(self.name)
                return "ran"

        try:
            test_pool.submit(lambda: gate.wait(timeout=10))  # occupies worker
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and test_pool.stats()["active"] < 1:
                time.sleep(0.02)
            test_pool.submit(lambda: gate.wait(timeout=10))  # fills queue slot

            results, hits = _run_bounded_fetch(
                {"news": (_Probe("get_news", "news-data"), {})}
            )
        finally:
            gate.set()

        assert executed == []
        assert "数据获取失败" in results["news"]
        assert "线程池饱和" in results["news"]
        assert DataCollector._infer_source_status(results["news"]) == "FAILED"
        assert "news" not in hits

    def test_abandoned_stuck_workers_do_not_accumulate_threads(self, monkeypatch):
        """The P1: per-round executors leaked one thread per abandoned worker.

        With the shared pool, the live thread count must not grow as more
        rounds abandon stuck workers.
        """
        monkeypatch.setattr(data_collector_module, "FETCH_ALL_TIMEOUT", 0.15)
        gate = threading.Event()
        pool = data_collector_module.get_fetch_pool()
        cap = pool.stats()["max_workers"]
        abandoned_before = pool.stats()["abandoned_total"]

        def _live_fetch_threads() -> int:
            return len(
                [
                    t
                    for t in threading.enumerate()
                    if t.name.startswith("ta-data-fetch-")
                ]
            )

        try:
            results, _hits = _run_bounded_fetch(
                {"stock_data": (_GateTool("get_stock_data", gate), {})}
            )
            assert "数据拉取超时" in results["stock_data"]
            live_after_first = _live_fetch_threads()

            for _ in range(3):  # three more rounds, each abandoning a worker
                _run_bounded_fetch(
                    {"stock_data": (_GateTool("get_stock_data", gate), {})}
                )

            assert _live_fetch_threads() == live_after_first, (
                "leaked threads must not accumulate across rounds"
            )
            gauges = data_collector_module.get_fetch_pool_stats()
            assert gauges["threads"] <= cap
            assert gauges["abandoned_total"] - abandoned_before == 4
        finally:
            gate.set()


# ---------------------------------------------------------------------------
# 1c. Fix round2: LHB force upgrade stays inside the whole-round deadline
# ---------------------------------------------------------------------------


class TestLhbForceWithinRoundBudget:
    def test_forced_lhb_success_within_budget_keeps_provenance(self, monkeypatch):
        monkeypatch.setattr(
            data_collector_module,
            "get_lhb_detail",
            _FakeTool("get_lhb_detail", result="LHB_HAS_DATA 龙虎榜明细"),
        )
        results: dict = {}
        hits: dict = {}

        data_collector_module._apply_forced_lhb(
            results, hits, "600519.SH", "2026-09-01",
            deadline=time.monotonic() + 5,
            force_reason="announcement_mentions_lhb",
        )

        assert results["lhb"] == "LHB_HAS_DATA 龙虎榜明细"
        assert results["_lhb_query_mode"] == "forced"
        assert results["_lhb_force_reason"] == "announcement_mentions_lhb"

    def test_forced_lhb_times_out_inside_remaining_budget(self, monkeypatch):
        gate = threading.Event()
        force_calls: list[dict] = []

        class _RecordingGateTool:
            name = "get_lhb_detail"

            def invoke(self, payload):
                force_calls.append(dict(payload))
                gate.wait(timeout=10)
                return "late"

        monkeypatch.setattr(
            data_collector_module, "get_lhb_detail", _RecordingGateTool()
        )
        results: dict = {}
        hits: dict = {}
        deadline = time.monotonic() + 0.3
        started = time.monotonic()
        try:
            data_collector_module._apply_forced_lhb(
                results, hits, "600519.SH", "2026-09-01",
                deadline=deadline, force_reason="fund_flow_anomaly",
            )
            elapsed = time.monotonic() - started
        finally:
            gate.set()

        assert elapsed < 2.5, "force query must not wait past the round deadline"
        assert "数据获取失败" in results["lhb"]
        assert "超时" in results["lhb"]
        assert DataCollector._infer_source_status(results["lhb"]) == "FAILED"
        assert results["_lhb_query_mode"] == "forced_timeout"
        assert results["_lhb_force_reason"] == "fund_flow_anomaly"
        assert "lhb" not in hits
        # Fix round3: forced_timeout ends ALL force activity for the round —
        # aside from the single in-flight attempt, nothing may execute after.
        time.sleep(0.2)
        assert len(force_calls) == 1

    def test_forced_lhb_queued_future_is_cancelled_and_never_executes(
        self, monkeypatch
    ):
        """Regression (round3 P1): with the pool saturated by stuck tasks,
        the forced LHB future used to stay queued past the deadline and run
        later — the force retry escaped the whole-round deadline even though
        the round already returned forced_timeout. The queued future must be
        cancelled: no force query may execute at all."""
        gate = threading.Event()
        test_pool = data_collector_module._BoundedFetchPool(1, max_queued=4)
        monkeypatch.setattr(data_collector_module, "_fetch_pool", test_pool)
        force_calls: list[dict] = []

        class _RecordingLhbTool:
            name = "get_lhb_detail"

            def invoke(self, payload):
                force_calls.append(dict(payload))
                gate.wait(timeout=10)
                return "late"

        monkeypatch.setattr(
            data_collector_module, "get_lhb_detail", _RecordingLhbTool()
        )

        try:
            # Occupy the only worker so the forced query stays queued.
            test_pool.submit(lambda: gate.wait(timeout=10))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and test_pool.stats()["active"] < 1:
                time.sleep(0.02)

            results: dict = {}
            hits: dict = {}
            data_collector_module._apply_forced_lhb(
                results, hits, "600519.SH", "2026-09-01",
                deadline=time.monotonic() + 0.3,
                force_reason="fund_flow_anomaly",
            )
        finally:
            gate.set()

        assert results["_lhb_query_mode"] == "forced_timeout"
        assert "数据获取失败" in results["lhb"]
        assert DataCollector._infer_source_status(results["lhb"]) == "FAILED"
        assert "lhb" not in hits
        time.sleep(0.2)  # give a wrongly-alive queue entry a chance to run
        assert force_calls == [], "queued force query must be cancelled, never executed"
        assert test_pool.stats()["cancelled_total"] == 1

    def test_forced_lhb_rejected_when_pool_saturated(self, monkeypatch):
        """Fix round3: a saturated pool must not silently enqueue the force
        query; it degrades explicitly (forced_rejected_saturated) instead of
        pretending the query was queued for success."""
        gate = threading.Event()
        test_pool = data_collector_module._BoundedFetchPool(1, max_queued=1)
        monkeypatch.setattr(data_collector_module, "_fetch_pool", test_pool)
        force_calls: list[dict] = []

        class _RecordingLhbTool:
            name = "get_lhb_detail"

            def invoke(self, payload):
                force_calls.append(dict(payload))
                gate.wait(timeout=10)
                return "late"

        monkeypatch.setattr(
            data_collector_module, "get_lhb_detail", _RecordingLhbTool()
        )

        try:
            test_pool.submit(lambda: gate.wait(timeout=10))  # occupies worker
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and test_pool.stats()["active"] < 1:
                time.sleep(0.02)
            test_pool.submit(lambda: gate.wait(timeout=10))  # fills queue slot

            results: dict = {}
            hits: dict = {}
            data_collector_module._apply_forced_lhb(
                results, hits, "600519.SH", "2026-09-01",
                deadline=time.monotonic() + 5,
                force_reason="fund_flow_anomaly",
            )
        finally:
            gate.set()

        assert force_calls == []
        assert "数据获取失败" in results["lhb"]
        assert "线程池饱和" in results["lhb"]
        assert DataCollector._infer_source_status(results["lhb"]) == "FAILED"
        assert results["_lhb_query_mode"] == "forced_rejected_saturated"
        assert results["_lhb_force_reason"] == "fund_flow_anomaly"

    def test_forced_lhb_skipped_when_budget_exhausted(self, monkeypatch):
        calls: list = []

        def _must_not_run(**payload):
            calls.append(payload)
            return "should never run"

        monkeypatch.setattr(data_collector_module, "get_lhb_detail", _must_not_run)
        results: dict = {}
        hits: dict = {}

        data_collector_module._apply_forced_lhb(
            results, hits, "600519.SH", "2026-09-01",
            deadline=time.monotonic() - 1,  # budget already gone
            force_reason="anomaly_condition",
        )

        assert calls == [], "force query must not be submitted without budget"
        assert "数据获取失败" in results["lhb"]
        assert "预算" in results["lhb"]
        assert DataCollector._infer_source_status(results["lhb"]) == "FAILED"
        assert results["_lhb_query_mode"] == "forced_skipped_budget"

    def test_fetch_all_keeps_forced_lhb_inside_round_deadline(self, monkeypatch):
        """Integration: _fetch_all wiring gives the force query the SAME
        deadline as the round — no budget-free execution after the cap."""
        for name in (
            "get_stock_data", "get_global_news", "get_board_fund_flow",
            "get_individual_fund_flow", "get_insider_transactions", "get_zt_pool",
            "get_hot_stocks_xq", "get_announcements", "get_margin_trading",
            "get_ratings", "get_research_report", "get_buybacks",
            "get_realtime_quotes", "get_fundamentals", "get_balance_sheet",
            "get_cashflow", "get_income_statement",
        ):
            monkeypatch.setattr(
                data_collector_module, name, _FakeTool(name, result="ok")
            )
        # News mentions 龙虎榜 → LHB force upgrade must trigger.
        monkeypatch.setattr(
            data_collector_module,
            "get_news",
            _FakeTool("get_news", result="600519 触发龙虎榜 异常波动"),
        )
        monkeypatch.setattr(
            data_collector_module, "_should_fetch_realtime_quote",
            lambda *a, **k: False,
        )
        monkeypatch.setattr(data_collector_module, "FETCH_ALL_TIMEOUT", 1.0)

        gate = threading.Event()

        class _StatefulLhbTool:
            name = "get_lhb_detail"

            def __init__(self) -> None:
                self.calls = 0
                self.force_calls = 0

            def invoke(self, payload):
                self.calls += 1
                if payload.get("force") is not True:
                    return "LHB_NORMAL_NO_DATA 无龙虎榜数据"
                self.force_calls += 1
                gate.wait(timeout=10)
                return "late"

        lhb_tool = _StatefulLhbTool()
        monkeypatch.setattr(data_collector_module, "get_lhb_detail", lhb_tool)

        try:
            started = time.monotonic()
            pool = data_collector_module._fetch_all("600519.SH", "2026-09-01")
            elapsed = time.monotonic() - started
        finally:
            gate.set()

        assert elapsed < 3.5, f"round must return inside its deadline, took {elapsed:.2f}s"
        assert pool["stock_data"] == "ok"
        assert pool["_lhb_force_reason"] == "anomaly_condition"
        assert pool["_lhb_query_mode"] == "forced_timeout"
        assert "数据获取失败" in pool["lhb"]
        assert DataCollector._infer_source_status(pool["lhb"]) == "FAILED"
        # Fix round3: after the round returned forced_timeout, the force
        # query must not (re)execute — exactly one in-flight attempt, and
        # once the gate releases, nothing new may run.
        time.sleep(0.2)
        assert lhb_tool.force_calls == 1, (
            "forced_timeout must end all LHB force activity for the round"
        )
        assert lhb_tool.calls == 2


# ---------------------------------------------------------------------------
# 1d. Fix round2: lock timeout derived from fetch budget (config invariant)
# ---------------------------------------------------------------------------


class TestFetchLockBudgetInvariant:
    _ENV_KEYS = ("TA_DATA_FETCH_TIMEOUT", "TA_DATA_FETCH_LOCK_TIMEOUT")

    def _reload_with(self, monkeypatch, *, fetch=None, lock=None):
        if fetch is None:
            monkeypatch.delenv("TA_DATA_FETCH_TIMEOUT", raising=False)
        else:
            monkeypatch.setenv("TA_DATA_FETCH_TIMEOUT", str(fetch))
        if lock is None:
            monkeypatch.delenv("TA_DATA_FETCH_LOCK_TIMEOUT", raising=False)
        else:
            monkeypatch.setenv("TA_DATA_FETCH_LOCK_TIMEOUT", str(lock))
        importlib.reload(data_collector_module)

    @pytest.fixture(autouse=True)
    def _restore_module_env(self):
        saved = {k: os.environ.get(k) for k in self._ENV_KEYS}
        yield
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(data_collector_module)

    def test_custom_fetch_timeout_derives_lock_margin(self, monkeypatch):
        """TA_DATA_FETCH_TIMEOUT=360 used to break the invariant; now the
        lock timeout is derived so the margin always holds."""
        self._reload_with(monkeypatch, fetch=360)

        assert data_collector_module.FETCH_ALL_TIMEOUT == 360.0
        assert data_collector_module.FETCH_LOCK_TIMEOUT == 420.0
        assert (
            data_collector_module.FETCH_LOCK_TIMEOUT
            >= data_collector_module.FETCH_ALL_TIMEOUT + 60.0
        )

    def test_explicit_larger_lock_timeout_is_honoured(self, monkeypatch):
        self._reload_with(monkeypatch, fetch=300, lock=700)

        assert data_collector_module.FETCH_LOCK_TIMEOUT == 700.0

    def test_default_values_unchanged(self, monkeypatch):
        self._reload_with(monkeypatch)

        assert data_collector_module.FETCH_ALL_TIMEOUT == 300.0
        assert data_collector_module.FETCH_LOCK_TIMEOUT == 360.0


# ---------------------------------------------------------------------------
# 2. /healthz executor starvation probe
# ---------------------------------------------------------------------------


class TestHealthzExecutorProbe:
    def test_starved_executor_returns_503_with_queue_depth(self, monkeypatch):
        monkeypatch.setattr(api_main, "_HEALTHZ_PROBE_TIMEOUT", 0.15)
        blocked = ThreadPoolExecutor(max_workers=1, thread_name_prefix="probe-test")
        blocked.submit(time.sleep, 0.5)  # occupy the only worker
        monkeypatch.setattr(api_main, "_default_executor", blocked)

        loop = asyncio.new_event_loop()
        loop.set_default_executor(blocked)
        try:
            response = loop.run_until_complete(api_main.healthz())
        finally:
            blocked.shutdown(wait=False, cancel_futures=True)
            loop.close()

        assert response.status_code == 503
        payload = json.loads(response.body)
        assert payload["status"] == "thread_pool_starved"
        # Queue depth is read after wait_for cancelled the queued no-op, so it
        # is informational; the observability contract is: key present, worker
        # count reported, and a 503 that no longer looks like a silent hang.
        assert "executor_queued" in payload
        assert payload["executor_threads"] >= 1

    def test_healthy_executor_returns_ok(self, monkeypatch):
        healthy = ThreadPoolExecutor(max_workers=2, thread_name_prefix="probe-ok")
        monkeypatch.setattr(api_main, "_default_executor", healthy)
        try:
            loop = asyncio.new_event_loop()
            loop.set_default_executor(healthy)
            try:
                payload = loop.run_until_complete(api_main.healthz())
            finally:
                loop.close()
        finally:
            healthy.shutdown(wait=True)

        assert payload["status"] == "ok"

    def test_probe_never_triggers_job_execution(self, monkeypatch):
        """The probe is a pure no-op: same task must never re-run from /healthz."""
        from fastapi.testclient import TestClient

        def _fail(*args, **kwargs):
            raise AssertionError("healthz probe must not execute jobs")

        monkeypatch.setattr(api_main, "_run_job", _fail)
        monkeypatch.setattr(api_main, "_run_job_inner", _fail)
        monkeypatch.setattr(api_main, "_create_tracked_task", _fail)

        client = TestClient(api_main.app, raise_server_exceptions=False)
        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_healthz_reports_fetch_pool_gauges(self, monkeypatch):
        """Fix round2 P1: leaked/backlogged fetch workers are observable."""
        from fastapi.testclient import TestClient

        healthy = ThreadPoolExecutor(max_workers=1, thread_name_prefix="probe-gauges")
        monkeypatch.setattr(api_main, "_default_executor", healthy)
        client = TestClient(api_main.app, raise_server_exceptions=False)
        try:
            response = client.get("/healthz")
        finally:
            healthy.shutdown(wait=True)

        assert response.status_code == 200
        gauges = response.json()["fetch_pool"]
        assert gauges["max_workers"] >= 1
        assert gauges["threads"] <= gauges["max_workers"]
        # Fix round3: queue cap + saturation rejections + deadline cancels
        # are observable alongside the round2 leak/backlog gauges.
        assert gauges["max_queued"] >= 1
        for key in ("active", "queued", "abandoned_total", "cancelled_total", "rejected_total"):
            assert key in gauges


# ---------------------------------------------------------------------------
# 3. Stock identification fallback
# ---------------------------------------------------------------------------


class TestStockIdentificationFallback:
    def test_llm_failure_with_name_in_text_resolves_from_warm_map(self, monkeypatch):
        _break_llm(monkeypatch)
        _warm_stock_map(monkeypatch, {"飞沃科技": "301232.SZ", "贵州茅台": "600519.SH"})

        symbol, _date, horizons, *_ = api_main._ai_extract_symbol_and_date(
            "分析一下 飞沃科技", {}
        )

        assert symbol == "301232.SZ"
        assert horizons == ["short"]

    def test_llm_failure_streaming_path_uses_same_fallback(self, monkeypatch):
        _break_llm(monkeypatch)
        _warm_stock_map(monkeypatch, {"飞沃科技": "301232.SZ"})

        result = asyncio.run(
            api_main._ai_extract_symbol_and_date_streaming(
                "分析一下 飞沃科技", {}, "job-fallback-test"
            )
        )

        assert result[0] == "301232.SZ"

    def test_ambiguous_name_fallback_fails_closed(self, monkeypatch):
        _break_llm(monkeypatch)
        _warm_stock_map(monkeypatch, {"飞沃科技": "301232.SZ", "贵州茅台": "600519.SH"})

        symbol, *_ = api_main._ai_extract_symbol_and_date(
            "比较 贵州茅台 和 飞沃科技", {}
        )

        assert symbol is None

    def test_no_stock_text_keeps_clear_error(self, monkeypatch):
        _break_llm(monkeypatch)
        _warm_stock_map(monkeypatch, {"飞沃科技": "301232.SZ"})

        symbol, *_ = api_main._ai_extract_symbol_and_date("今天天气怎么样", {})

        assert symbol is None

    def test_cold_cache_does_not_trigger_remote_load(self, monkeypatch):
        _warm_stock_map(monkeypatch, None)

        def _no_remote(*args, **kwargs):
            raise AssertionError("degraded path must not cold-load the stock map")

        monkeypatch.setattr(api_main, "_load_cn_stock_map", _no_remote)

        assert api_main._resolve_cn_name_from_text_cached("分析一下 飞沃科技") is None

    def test_explicit_code_wins_over_broken_llm(self, monkeypatch):
        """Deterministic regex stays authoritative: no LLM round-trip needed."""
        _break_llm(monkeypatch)
        _warm_stock_map(monkeypatch, {"飞沃科技": "301232.SZ"})
        monkeypatch.setattr(api_main, "market_today_str", lambda *a, **k: "2026-09-01")

        symbol, date, *_ = api_main._ai_extract_symbol_and_date(
            "分析 300845.SZ 今日走势", {}
        )

        assert symbol == "300845.SZ"
        assert date == "2026-09-01"

    def test_explicit_code_wins_over_unresolvable_llm_name(self, monkeypatch):
        """Even when the LLM returns garbage, a clear code in the text wins."""
        from tradingagents.llm_clients import factory

        llm = MagicMock()
        llm.invoke.return_value = (
            '{"stock_name": "不存在的公司XYZ", "date": null, "horizons": ["short"],'
            ' "focus_areas": [], "specific_questions": [], "user_context": {}}'
        )
        client = MagicMock()
        client.get_llm.return_value = llm
        monkeypatch.setattr(factory, "create_llm_client", lambda *a, **k: client)
        _warm_stock_map(monkeypatch, {"飞沃科技": "301232.SZ"})
        monkeypatch.setattr(api_main, "_search_cn_stock_by_name", lambda query: None)
        monkeypatch.setattr(api_main, "market_today_str", lambda *a, **k: "2026-09-01")

        symbol, *_ = api_main._ai_extract_symbol_and_date(
            "分析 300845.SZ 今日走势", {}
        )

        assert symbol == "300845.SZ"


# ---------------------------------------------------------------------------
# 4. Humanized + sanitized error semantics
# ---------------------------------------------------------------------------


class TestHumanizeAnalysisError:
    def test_insufficient_balance_hint(self):
        msg = api_main._humanize_analysis_error(
            "RuntimeError: Error code: 402, Insufficient Balance"
        )
        assert "余额不足" in msg
        assert "402" in msg

    def test_rate_limit_hint(self):
        msg = api_main._humanize_analysis_error(
            "APIError: Error code: 429, too_many_requests"
        )
        assert "限流" in msg

    def test_unsupported_model_hint(self):
        msg = api_main._humanize_analysis_error(
            "BadRequestError: Unsupported model longcat-flash-chat"
        )
        assert "模型名称不被服务商支持" in msg

    def test_connection_error_hint_redacts_url(self):
        msg = api_main._humanize_analysis_error(
            "ConnectionError: Connection error: POST "
            "https://api.xiaomimimo.com/v1/chat/completions failed"
        )
        assert "连接模型服务失败" in msg
        assert "xiaomimimo.com" not in msg
        assert "https" not in msg

    def test_credentials_never_leak(self):
        msg = api_main._humanize_analysis_error(
            "RuntimeError: Error code: 401, invalid api_key sk-abcdef0123456789"
        )
        assert "sk-abcdef0123456789" not in msg
        assert "<凭据已隐藏>" in msg

    def test_long_opaque_tokens_are_masked(self):
        msg = api_main._humanize_analysis_error(
            "Error: upstream rejected 9f8e7d6c5b4a32109f8e7d6c5b4a32109f8e7d6c"
        )
        assert "9f8e7d6c5b4a3210" not in msg

    def test_bearer_header_with_jwt_is_fully_masked(self):
        """Fix round2 P1: the credential must be swallowed whole, not just
        the literal "Authorization: Bearer" prefix."""
        msg = api_main._humanize_analysis_error(
            "RuntimeError: Error code: 401 Authorization: Bearer "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        assert "eyJhbGciOiJIUzI1NiJ9" not in msg
        assert "SflKxwRJSMeKKF2QT4" not in msg
        assert "Bearer" not in msg
        assert "<凭据已隐藏>" in msg

    def test_bearer_header_json_and_kv_forms_masked(self):
        raw_msgs = (
            '{"headers": {"Authorization": "Bearer sk-abcdef0123456789"}}',
            "authorization=Bearer abc123def456ghi789",
            "Authorization:Basic dXNlcjpwYXNzMTIzNDU2Nzg=",
        )
        for raw in raw_msgs:
            msg = api_main._humanize_analysis_error(f"Request failed: {raw}")
            assert "sk-abcdef0123456789" not in msg, raw
            assert "abc123def456ghi789" not in msg, raw
            assert "dXNlcjpwYXNz" not in msg, raw
            assert "<凭据已隐藏>" in msg, raw

    def test_standalone_bearer_and_jwt_masked(self):
        msg = api_main._humanize_analysis_error(
            "Response 401: Bearer eyJhbGciOiJub25lIn0.eyJhIjoxfQ.sig-val-here"
        )
        assert "eyJhbGciOiJub25lIn0" not in msg
        assert "sig-val-here" not in msg
        assert "<凭据已隐藏>" in msg

    def test_stack_lines_are_dropped(self):
        msg = api_main._humanize_analysis_error(
            'RuntimeError: boom\n  File "/srv/app/api/main.py", line 123\n'
            "    result = pending()\nRuntimeError: boom"
        )
        assert msg.count("\n") == 0
        assert 'File "' not in msg
        assert "/srv/app" not in msg

    def test_unrecognized_error_is_sanitized_but_preserved(self):
        msg = api_main._humanize_analysis_error(
            "SomeError: weird failure https://internal.example.com/v1"
        )
        assert "weird failure" in msg
        assert "internal.example.com" not in msg

    def test_scheme_less_domains_are_redacted(self):
        msg = api_main._humanize_analysis_error(
            "ConnectError: could not reach api.openai.com/v1/chat"
        )
        assert "api.openai.com" not in msg
        assert "连接模型服务失败" in msg

    def test_idempotent_and_empty(self):
        once = api_main._humanize_analysis_error("Error code: 429 throttling")
        assert api_main._humanize_analysis_error(once) == once
        assert api_main._humanize_analysis_error("") == ""

    def test_idempotent_shortcut_still_sanitizes(self):
        """Fix round2 P2: the "already humanized" shortcut must not bypass
        URL/credential redaction, and repeated sanitization stays stable."""
        crafted = (
            "您配置的大模型 API Key 余额不足。（原始错误："
            "see https://secret.example.com/v1?key=sk-abcdef0123456789）"
        )
        once = api_main._humanize_analysis_error(crafted)
        assert "secret.example.com" not in once
        assert "sk-abcdef0123456789" not in once
        assert "<url已隐藏>" in once
        assert api_main._humanize_analysis_error(once) == once

    def test_idempotent_shortcut_drops_stack_lines(self):
        multiline = (
            "模型服务端暂时故障。请稍后重试。（原始错误：boom）\n"
            '  File "/srv/app/api/main.py", line 9\n'
            "    raise RuntimeError: boom"
        )
        sanitized = api_main._humanize_analysis_error(multiline)
        assert "/srv/app" not in sanitized
        assert "raise RuntimeError" not in sanitized
        assert sanitized.count("\n") == 0
        assert api_main._humanize_analysis_error(sanitized) == sanitized

    def test_truncation_keeps_message_bounded(self):
        msg = api_main._humanize_analysis_error("ValueError: " + "x" * 5000)
        assert len(msg) <= api_main._ERROR_MAX_ORIGIN_LEN


class TestHumanizedErrorWiring:
    def _run_failing_job_inner(self, monkeypatch, exc: Exception) -> dict:
        _ensure_test_db()
        captured: dict = {}

        def _record_failure(job_id, **fields):
            captured.update(fields)

        monkeypatch.setattr(api_main, "_set_job", _record_failure)

        def _capture_event(job_id, event, data):
            captured.setdefault("events", []).append((event, data))

        monkeypatch.setattr(api_main, "_emit_job_event", _capture_event)

        def _boom(*args, **kwargs):
            raise exc

        monkeypatch.setattr(api_main._shared_data_collector, "ref", _boom)
        monkeypatch.setattr(api_main._shared_data_collector, "evict", lambda *a, **k: None)

        request = api_main.AnalyzeRequest(
            symbol="600519.SH", trade_date="2026-09-01", dry_run=False
        )
        asyncio.run(
            api_main._run_job_inner("job-humanize-test", request, False, False, "u", "api")
        )
        return captured

    def test_run_job_inner_failure_is_humanized_and_sanitized(self, monkeypatch):
        captured = self._run_failing_job_inner(
            monkeypatch,
            ConnectionError(
                "Connection error: POST https://llm.example.invalid/v1 failed"
            ),
        )

        assert captured["status"] == "failed"
        error = captured["error"]
        assert "连接模型服务失败" in error
        assert "llm.example.invalid" not in error
        failed_events = [d for name, d in captured["events"] if name == "job.failed"]
        assert failed_events and "连接模型服务失败" in failed_events[0]["error"]

    def test_run_job_inner_failure_hides_key_material(self, monkeypatch):
        captured = self._run_failing_job_inner(
            monkeypatch,
            RuntimeError(
                "Error code: 401, invalid api_key sk-secret0123456789abcdef"
            ),
        )

        error = captured["error"]
        assert "sk-secret0123456789abcdef" not in error
        assert "API Key 无效或已过期" in error

    def test_streaming_chat_failure_event_is_humanized(self, monkeypatch):
        """End-to-end: SSE job.failed for a broken extraction is user-readable."""
        from fastapi.testclient import TestClient

        _ensure_test_db()

        def _raise(*args, **kwargs):
            raise RuntimeError("Error code: 429, too_many_requests")

        monkeypatch.setattr(
            api_main, "_ai_extract_symbol_and_date_streaming", _raise
        )

        client = TestClient(api_main.app, raise_server_exceptions=False)
        # Register a user (dev flow) for the authenticated endpoint.
        r = client.post(
            "/v1/auth/request-code", json={"email": "upstream081@test.com"}
        )
        code = r.json()["dev_code"]
        r2 = client.post(
            "/v1/auth/verify-code",
            json={"email": "upstream081@test.com", "code": code},
        )
        token = r2.json()["access_token"]

        failed_errors: list[str] = []
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messages": [{"role": "user", "content": "分析一下 飞沃科技"}],
                "stream": True,
                "dry_run": True,
            },
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(event, dict) and "error" in event:
                    failed_errors.append(event["error"])

        assert any("限流" in err for err in failed_errors), failed_errors
