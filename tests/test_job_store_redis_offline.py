"""[UPSTREAM-081-002][r2 P2-1] RedisJobStore.subscribe() 生成器控制流离线测试。

r1 修复轮曾以 stub ``get_job`` 离线驱动修复后的 Redis 生成器做一次性 sanity
（ping×N → 终态补发 → done），但未入库：此前该控制流只能靠活 Redis 的
skip 守卫集成测试（tests/test_job_store_redis.py）覆盖。本模块把该 sanity
产品化——替身 ``get_job``（按时间序返回快照）+ 替身 pubsub（空/有限事件），
全程无网络、无活 Redis、无新第三方依赖，任何环境都可执行。

不覆盖的点（已在别处收口，勿重复）：
- 补发 payload 决策本身 → ``terminal_replay_event`` 纯函数测试
  （tests/test_upstream081_timeout_recovery.py）。
- Redis 序列化往返对决策的影响 → 同文件序列化往返用例。
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
import redis

from api.job_store import terminal_replay_event
from api.job_store_redis import RedisJobStore


# ---------------------------------------------------------------------------
# Offline doubles: no sockets, no server, no third-party fakes
# ---------------------------------------------------------------------------


class _StubPubSub:
    """Minimal pub/sub fake: yields scripted messages, then None forever."""

    def __init__(self, messages):
        self._messages = list(messages)

    def subscribe(self, channel):
        return 1

    def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        if self._messages:
            return {"type": "message", "data": self._messages.pop(0)}
        # Brief release so the listener thread observes stop_event promptly.
        time.sleep(0.005)
        return None

    def unsubscribe(self):
        return 0

    def close(self):
        return None


class _StubRedisClient:
    """Offline stand-in for redis.Redis.

    ``pubsub()`` hands the listener thread a scripted fake: an empty script
    keeps the queue empty (timeout/replay path); a finite script exercises
    the live pub/sub → asyncio.Queue bridge. Reads/writes are tripwires —
    ``get_job`` is stubbed per test and the replay path must not write.
    """

    def __init__(self, scripted_messages=()):
        self._scripted_messages = list(scripted_messages)
        self.pubsub_calls = 0

    def ping(self):
        return True

    def pubsub(self):
        self.pubsub_calls += 1
        return _StubPubSub(self._scripted_messages)

    def hgetall(self, key):
        raise AssertionError("hgetall reached; get_job should be stubbed per test")

    def publish(self, channel, data):
        raise AssertionError("subscribe() replay path must not write to Redis")


def _offline_store(monkeypatch: pytest.MonkeyPatch, *, scripted_messages=()) -> RedisJobStore:
    """Build a RedisJobStore without a live Redis (from_url → stub client)."""
    client = _StubRedisClient(scripted_messages)
    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: client)
    return RedisJobStore("redis://offline-stub:6379/15", prefix="ta_offline_test:")


class _ScriptedGetJob:
    """Return scripted job snapshots in time order, repeating the last one."""

    def __init__(self, *snapshots):
        self._snapshots = [dict(s) for s in snapshots]
        self.calls = 0

    def __call__(self, job_id):
        self.calls += 1
        index = min(self.calls - 1, len(self._snapshots) - 1)
        return dict(self._snapshots[index])


# ---------------------------------------------------------------------------
# ① 错过事件、终态已落库：ping×N → 补发终态 → done
# ---------------------------------------------------------------------------


def test_subscribe_replays_completed_after_ping_when_terminal_lands_late(
    monkeypatch: pytest.MonkeyPatch,
):
    """live 事件全错过（pubsub 恒空），get_job 按时间序 running→completed。

    事件序列必须为 ping → job.completed → done；补发 payload 与
    ``terminal_replay_event``（最终快照）逐字段一致，且全程零 Redis 写入。
    """
    final_snapshot = {
        "status": "completed",
        "decision": "BUY",
        "direction": "bullish",
        "result": {"direction": "bullish", "confidence": 0.8, "target_price": 12.5},
        "risk_items": [],
        "key_metrics": ["pe"],
        "confidence": 0.8,
        "target_price": 12.5,
        "stop_loss_price": 10.0,
    }
    scripted = _ScriptedGetJob({"status": "running", "error": None}, final_snapshot)

    store = _offline_store(monkeypatch)  # 空脚本：队列永远为空 → 只走超时路径
    monkeypatch.setattr(store, "get_job", scripted)

    async def scenario():
        return [event async for event in store.subscribe("j1", poll_interval=0.05)]

    collected = asyncio.run(scenario())

    # 序列：非终态 ping 一次，终态补发一次，随后 done（长度即序，无后续事件）
    assert [event["event"] for event in collected] == ["ping", "job.completed"]
    assert scripted.calls == 2  # 恰好两次超时轮询：running → ping；completed → 补发

    expected = terminal_replay_event("j1", final_snapshot)
    replay = collected[-1]
    assert replay["event"] == expected["event"]
    assert replay["data"] == expected["data"]
    assert "timestamp" in replay  # 调用方补时间戳
    assert "timestamp" in collected[0]["data"]  # ping 契约不回归


# ---------------------------------------------------------------------------
# ② 立即就是终态：首轮询即重放 job.failed → done，无 ping
# ---------------------------------------------------------------------------


def test_subscribe_replays_failed_immediately_when_already_terminal(
    monkeypatch: pytest.MonkeyPatch,
):
    """订阅时终态已落库：首次超时轮询即重放 job.failed，一次且无 ping。"""
    final_snapshot = {"status": "failed", "error": "boom: inner failure"}
    scripted = _ScriptedGetJob(final_snapshot)

    store = _offline_store(monkeypatch)
    monkeypatch.setattr(store, "get_job", scripted)

    async def scenario():
        return [event async for event in store.subscribe("j1", poll_interval=0.05)]

    collected = asyncio.run(scenario())

    assert [event["event"] for event in collected] == ["job.failed"]
    assert scripted.calls == 1

    expected = terminal_replay_event("j1", final_snapshot)
    replay = collected[-1]
    assert replay["event"] == expected["event"]
    assert replay["data"] == expected["data"]
    assert replay["data"] == {"job_id": "j1", "error": "boom: inner failure"}
    assert "timestamp" in replay


# ---------------------------------------------------------------------------
# ③ 有限 live 事件：按序交付，live 终态即 done，不触发轮询 get_job
# ---------------------------------------------------------------------------


def test_subscribe_delivers_scripted_live_events_then_done(
    monkeypatch: pytest.MonkeyPatch,
):
    """pubsub 返回有限事件：监听线程桥接按序交付，live 终态终止生成器。"""
    live_completed = {
        "event": "job.completed",
        "data": {"job_id": "j1", "decision": "HOLD"},
        "timestamp": "2026-09-08T00:00:00+00:00",
    }
    messages = [
        json.dumps(
            {"event": "agent.snapshot", "data": {"step": 1}, "timestamp": "t1"},
            ensure_ascii=False,
        ),
        json.dumps(live_completed, ensure_ascii=False),
    ]

    store = _offline_store(monkeypatch, scripted_messages=messages)

    def _forbidden(job_id):
        raise AssertionError("get_job polled although live events arrived")

    monkeypatch.setattr(store, "get_job", _forbidden)

    async def scenario():
        return [event async for event in store.subscribe("j1", poll_interval=5.0)]

    collected = asyncio.run(scenario())

    assert [event["event"] for event in collected] == ["agent.snapshot", "job.completed"]
    assert collected[0]["data"] == {"step": 1}
    assert collected[1]["data"] == live_completed["data"]  # JSON 桥接无失真
    assert all("timestamp" in event for event in collected)
