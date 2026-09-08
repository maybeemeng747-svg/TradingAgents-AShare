"""Tests for RedisJobStore.

Skipped automatically when Redis is not reachable on localhost.
Uses DB 15 to avoid conflicts with production data.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import redis

from api.job_store import InMemoryJobStore  # noqa: E402

# ---------------------------------------------------------------------------
# Skip guard: skip the entire module when Redis is unreachable
# ---------------------------------------------------------------------------

_REDIS_TEST_URL = os.environ.get("REDIS_TEST_URL", "redis://localhost:6379/15")

def _redis_available() -> bool:
    try:
        r = redis.Redis.from_url(_REDIS_TEST_URL, decode_responses=True)
        r.ping()
        r.close()
        return True
    except Exception:
        return False

pytestmark = pytest.mark.skipif(
    not _redis_available(),
    reason="Redis not available at " + _REDIS_TEST_URL,
)

from api.job_store_redis import RedisJobStore  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store():
    """Create a RedisJobStore with a unique prefix and flush matching keys after."""
    prefix = f"ta_test:{uuid.uuid4().hex[:8]}:"
    s = RedisJobStore(_REDIS_TEST_URL, prefix=prefix)
    yield s
    # Cleanup: delete all keys with this test prefix
    s.clear()
    s._r.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_set_and_get(store: RedisJobStore):
    store.set_job("j1", status="running", symbol="AAPL")
    job = store.get_job("j1")
    assert job["status"] == "running"
    assert job["symbol"] == "AAPL"


def test_get_missing(store: RedisJobStore):
    assert store.get_job("nonexistent") == {}


def test_merge(store: RedisJobStore):
    store.set_job("j1", status="running")
    store.set_job("j1", symbol="AAPL")
    job = store.get_job("j1")
    assert job["status"] == "running"
    assert job["symbol"] == "AAPL"
    # Overwrite existing field
    store.set_job("j1", status="completed")
    job = store.get_job("j1")
    assert job["status"] == "completed"
    assert job["symbol"] == "AAPL"


def test_none_roundtrip(store: RedisJobStore):
    """None values should round-trip correctly."""
    store.set_job("j1", result=None)
    job = store.get_job("j1")
    assert job["result"] is None


def test_bool_roundtrip_matches_memory_store(store: RedisJobStore):
    """[UPSTREAM-081-002] Booleans must round-trip as real bools.

    ``overtime`` is produced by the soft-timeout path and consumed by
    ``get_job_status`` (``bool(job.get("overtime", False))``). If Redis stored
    ``str(True) == "True"``, any string encoding would be truthy on read and
    Redis semantics would diverge from the in-memory store.
    """
    store.set_job("j1", status="running", overtime=False, overtime_at=None)
    store.set_job("j2", status="running", overtime=True, overtime_at="2026-09-07T00:00:00+00:00")

    j1 = store.get_job("j1")
    j2 = store.get_job("j2")

    # Exact types matter: no "False"/"True" string encoding may survive.
    assert j1["overtime"] is False
    assert j2["overtime"] is True
    assert isinstance(j1["overtime"], bool)
    assert isinstance(j2["overtime"], bool)
    # None still round-trips as None for overtime_at.
    assert j1["overtime_at"] is None
    assert j2["overtime_at"] == "2026-09-07T00:00:00+00:00"

    # Same writes through the in-memory store must observe identical values.
    memory = InMemoryJobStore()
    memory.set_job("j1", status="running", overtime=False, overtime_at=None)
    memory.set_job("j2", status="running", overtime=True, overtime_at="2026-09-07T00:00:00+00:00")
    assert memory.get_job("j1")["overtime"] is j1["overtime"]
    assert memory.get_job("j2")["overtime"] is j2["overtime"]
    assert memory.get_job("j1")["overtime_at"] == j1["overtime_at"]
    assert memory.get_job("j2")["overtime_at"] == j2["overtime_at"]


def test_complex_value_roundtrip(store: RedisJobStore):
    """Dict and list values should round-trip via JSON serialization."""
    store.set_job("j1", report={"score": 8, "tags": ["buy", "hold"]}, items=[1, 2, 3])
    job = store.get_job("j1")
    assert job["report"] == {"score": 8, "tags": ["buy", "hold"]}
    assert job["items"] == [1, 2, 3]


def test_delete(store: RedisJobStore):
    store.set_job("j1", status="running")
    store.delete_job("j1")
    assert store.get_job("j1") == {}
    # Deleting a non-existent job should not raise
    store.delete_job("j1")


def test_clear(store: RedisJobStore):
    store.set_job("j1", status="running")
    store.set_job("j2", status="completed")
    store.clear()
    assert store.get_job("j1") == {}
    assert store.get_job("j2") == {}


def test_pubsub(store: RedisJobStore):
    """emit_event + subscribe: events should be delivered in order and
    terminate on a terminal event."""

    async def scenario():
        store.set_job("j1", status="running")

        # Start subscription *before* emitting so the listener is ready
        collected = []
        gen = store.subscribe("j1", poll_interval=5.0)
        # We need the subscriber thread to be up before publishing.
        # Advance the generator to start the thread, then emit events.
        # Use a small delay to let the thread subscribe.
        it = gen.__aiter__()

        # Emit after a short delay to give listener time to subscribe
        async def _emit_later():
            await asyncio.sleep(0.3)
            store.emit_event("j1", "agent.snapshot", {"step": 1})
            store.emit_event("j1", "agent.snapshot", {"step": 2})
            store.emit_event("j1", "job.completed", {"result": "ok"})

        emit_task = asyncio.create_task(_emit_later())

        async for event in gen:
            collected.append(event)

        await emit_task

        assert len(collected) == 3
        assert collected[0]["event"] == "agent.snapshot"
        assert collected[0]["data"] == {"step": 1}
        assert collected[1]["event"] == "agent.snapshot"
        assert collected[1]["data"] == {"step": 2}
        assert collected[2]["event"] == "job.completed"
        assert collected[2]["data"] == {"result": "ok"}
        for ev in collected:
            assert "timestamp" in ev

    asyncio.run(scenario())


def test_subscribe_timeout_ping(store: RedisJobStore):
    """When no events arrive and the job is still running, subscribe yields a
    ping. When the job becomes completed on the next timeout, it replays the
    terminal event (memory-store semantics, [UPSTREAM-081-002]) and then
    terminates."""

    async def scenario():
        store.set_job("j1", status="running")

        collected = []
        count = 0
        async for event in store.subscribe("j1", poll_interval=0.2):
            collected.append(event)
            count += 1
            if count == 1:
                # After first ping, mark job completed so the next timeout
                # replays the terminal event instead of silently dropping
                # the subscriber.
                store.set_job("j1", status="completed")

        assert len(collected) == 2
        assert collected[0]["event"] == "ping"
        assert "timestamp" in collected[0]["data"]
        assert collected[1]["event"] == "job.completed"
        assert collected[1]["data"]["job_id"] == "j1"
        assert "timestamp" in collected[1]

    asyncio.run(scenario())


def test_subscribe_replays_completed_terminal_when_live_event_missed(store: RedisJobStore):
    """[UPSTREAM-081-002] A subscriber that never receives the live pub/sub
    terminal event still gets one replayed ``job.completed`` with the full
    payload (decision/direction/result/... read back from the job hash), and
    the generator terminates on it. Event ordering is unchanged: only the
    previously-missing terminal replay is added."""

    async def scenario():
        store.set_job(
            "j1",
            status="running",
            decision="BUY",
            result={"direction": "bullish", "confidence": 0.8, "target_price": 12.5},
        )

        collected = []
        pings = 0
        async for event in store.subscribe("j1", poll_interval=0.2):
            collected.append(event)
            if event["event"] == "ping":
                pings += 1
                if pings == 1:
                    # Terminal state lands in Redis only; no pub/sub event is
                    # ever published, so the subscriber must recover via the
                    # timeout replay path.
                    store.set_job("j1", status="completed")
                if pings > 5:
                    pytest.fail("job.completed replay never arrived")

        names = [e["event"] for e in collected]
        assert names[-1] == "job.completed"
        assert names.count("job.completed") == 1
        assert "job.failed" not in names

        replay = collected[-1]
        assert replay["data"]["job_id"] == "j1"
        assert replay["data"]["decision"] == "BUY"
        assert replay["data"]["direction"] == "bullish"
        assert replay["data"]["result"] == {"direction": "bullish", "confidence": 0.8, "target_price": 12.5}
        assert replay["data"]["confidence"] == 0.8
        assert replay["data"]["target_price"] == 12.5
        assert replay["data"]["risk_items"] == []
        assert replay["data"]["key_metrics"] == []
        assert "timestamp" in replay

    asyncio.run(scenario())


def test_subscribe_replays_failed_terminal_when_live_event_missed(store: RedisJobStore):
    """[UPSTREAM-081-002] Same recovery semantics for failure: the subscriber
    gets exactly one replayed ``job.failed`` carrying the persisted error."""

    async def scenario():
        store.set_job("j1", status="running")

        collected = []
        pings = 0
        async for event in store.subscribe("j1", poll_interval=0.2):
            collected.append(event)
            if event["event"] == "ping":
                pings += 1
                if pings == 1:
                    store.set_job("j1", status="failed", error="boom: inner failure")
                if pings > 5:
                    pytest.fail("job.failed replay never arrived")

        names = [e["event"] for e in collected]
        assert names[-1] == "job.failed"
        assert names.count("job.failed") == 1
        assert "job.completed" not in names
        assert collected[-1]["data"] == {"job_id": "j1", "error": "boom: inner failure"}
        assert "timestamp" in collected[-1]

    asyncio.run(scenario())
