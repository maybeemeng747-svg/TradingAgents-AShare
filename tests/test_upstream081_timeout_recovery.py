"""[UPSTREAM-081-002] 长时分析软/硬超时与断线恢复测试。

参考上游 3e32c89（#204），验收口径：

1. 软超时（_resolve_job_timeout → _job_timeout_for_config）是非终态事件：
   job 保持 ``status="running"`` 且 ``overtime=True``，后台任务继续运行，
   最终只产生一次 completed/failed 终态（防双写）。
2. 硬超时（TA_JOB_HARD_TIMEOUT）独立 fail-closed：cancel 内层协程、标记
   failed、调用 ``report_service.mark_report_failed``，四态中的“硬超时”
   文案可区分。
3. 历史 ``failed(timeout)`` 记录读路径不得被改写为 running（恢复轮询据此
   继续等待或收口，但不无条件重写状态）。
4. 报告持久化失败不得伪装成 completed（_save_report_or_raise 抛回失败边界）。
5. Redis 与内存 store 的布尔字段（overtime）序列化语义一致。
6. 订阅端错过终态事件后按共享纯函数 ``terminal_replay_event`` 重放
   job.completed/job.failed；内存与 Redis store 决策逐字段一致，且该决策
   可不依赖活 Redis 单测（r1 review P1/P2 修复）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import api.main as api_main
from api.job_store import InMemoryJobStore, terminal_replay_event


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _RecordingJobStore(InMemoryJobStore):
    """InMemoryJobStore that records emitted events in order."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, dict]] = []
        self._event_hooks: dict[str,callable] = {}

    def emit_event(self, job_id: str, event: str, data: dict) -> None:
        self.events.append((event, data))
        hook = self._event_hooks.get(event)
        if hook is not None:
            hook()
        super().emit_event(job_id, event, data)

    def event_names(self) -> list[str]:
        return [name for name, _ in self.events]


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> _RecordingJobStore:
    store = _RecordingJobStore()
    monkeypatch.setattr(api_main, "_job_store_instance", store)
    return store


@pytest.fixture()
def no_db_failure_mark(monkeypatch: pytest.MonkeyPatch):
    """Stub report_service.mark_report_failed and return the mock."""
    mock = MagicMock(return_value=None)
    monkeypatch.setattr(api_main.report_service, "mark_report_failed", mock)
    return mock


def _patch_timeouts(
    monkeypatch: pytest.MonkeyPatch,
    soft: float,
    hard: float,
) -> None:
    monkeypatch.setattr(
        api_main,
        "_resolve_job_timeout",
        lambda request, user_id: soft,
    )
    monkeypatch.setattr(api_main, "_JOB_HARD_TIMEOUT", hard)


def _wedged_inner(
    release: asyncio.Event,
    *,
    on_start=None,
    on_release=None,
    cancelled_flag: list[bool] | None = None,
):
    """Build a _run_job_inner replacement that blocks until release."""

    async def _inner(job_id, request, stream_events=False, save_report=True,
                     user_id=None, request_source="api"):
        try:
            if on_start is not None:
                on_start(job_id)
            await release.wait()
            if on_release is not None:
                on_release(job_id)
        except asyncio.CancelledError:
            if cancelled_flag is not None:
                cancelled_flag.append(True)
            raise
        return None

    return _inner


def _run_job_async(job_id: str):
    return api_main._run_job(job_id, api_main.AnalyzeRequest(symbol="600519.SH"),
                             True, True, None, "api")


# ---------------------------------------------------------------------------
# 软超时：非终态事件 + 后台继续 + 单次终态（防双写）
# ---------------------------------------------------------------------------


def test_soft_timeout_keeps_job_running_then_completes_once(
    store: _RecordingJobStore,
    monkeypatch: pytest.MonkeyPatch,
    no_db_failure_mark,
):
    """软超时后 job 仍 running + overtime=True；内层完成后只有一次 completed。"""
    release = asyncio.Event()
    job_id = "job-soft-ok"

    def _on_start(jid: str) -> None:
        api_main._set_job(jid, status="running")

    def _on_release(jid: str) -> None:
        # 内层工作流在软超时后于后台完成：只写一次终态
        api_main._set_job(
            jid,
            status="completed",
            decision="HOLD",
            error=None,
            overtime=False,
            overtime_at=None,
            finished_at=api_main._utcnow_iso(),
        )
        api_main._emit_job_event(jid, "job.completed", {"job_id": jid, "decision": "HOLD"})

    # 软超时事件到达后释放内层任务，让它在后台继续完成
    store._event_hooks["job.overtime"] = release.set

    monkeypatch.setattr(
        api_main, "_run_job_inner",
        _wedged_inner(release, on_start=_on_start, on_release=_on_release),
    )
    _patch_timeouts(monkeypatch, soft=0.05, hard=30.0)

    asyncio.run(_run_job_async(job_id))

    job = store.get_job(job_id)
    assert job["status"] == "completed"
    assert job["overtime"] is False
    assert job["overtime_at"] is None
    assert job["error"] is None

    names = store.event_names()
    # 软超时提示恰好一次；终态恰好一次；绝不出现 job.failed
    assert names.count("job.overtime") == 1
    assert names.count("job.completed") == 1
    assert "job.failed" not in names
    # 防双写：终态事件必须是最后一个
    assert names[-1] == "job.completed"
    no_db_failure_mark.assert_not_called()


def test_soft_timeout_marks_job_running_with_overtime(
    store: _RecordingJobStore,
    monkeypatch: pytest.MonkeyPatch,
    no_db_failure_mark,
):
    """软超时把 job 写成 running + overtime=True + 清空 error（非终态）。"""
    release = asyncio.Event()
    job_id = "job-ot-state"

    observed: dict = {}

    def _on_start(jid: str) -> None:
        api_main._set_job(jid, status="running")

    def _hook() -> None:
        observed.update(store.get_job(job_id))
        release.set()

    store._event_hooks["job.overtime"] = _hook
    monkeypatch.setattr(
        api_main, "_run_job_inner",
        _wedged_inner(release, on_start=_on_start),
    )
    _patch_timeouts(monkeypatch, soft=0.05, hard=30.0)

    asyncio.run(_run_job_async(job_id))

    assert observed["status"] == "running"
    assert observed["overtime"] is True
    assert observed["overtime_at"]
    assert observed["error"] is None

    overtime_events = [d for n, d in store.events if n == "job.overtime"]
    assert len(overtime_events) == 1
    data = overtime_events[0]
    assert data["soft_timeout_seconds"] == 0.05
    assert "后台仍在继续" in data["message"]
    assert "请勿重复提交" in data["message"]
    assert data["overtime_at"] == observed["overtime_at"]


# ---------------------------------------------------------------------------
# 硬超时：独立 fail-closed
# ---------------------------------------------------------------------------


def test_hard_timeout_cancels_and_fails_closed(
    store: _RecordingJobStore,
    monkeypatch: pytest.MonkeyPatch,
    no_db_failure_mark,
):
    """硬超时到达：cancel 内层协程、标 failed、写报告失败、发一次 job.failed。"""
    release = asyncio.Event()  # 永不释放：模拟僵死任务
    cancelled_flag: list[bool] = []
    job_id = "job-hard"

    def _on_start(jid: str) -> None:
        api_main._set_job(jid, status="running")

    monkeypatch.setattr(
        api_main, "_run_job_inner",
        _wedged_inner(release, on_start=_on_start, cancelled_flag=cancelled_flag),
    )
    _patch_timeouts(monkeypatch, soft=0.05, hard=0.15)

    asyncio.run(asyncio.wait_for(_run_job_async(job_id), timeout=10.0))

    job = store.get_job(job_id)
    assert job["status"] == "failed"
    assert "硬性运行上限" in job["error"]
    assert "任务超时（超过" not in job["error"]  # 不复用旧软超时终态文案
    assert job["overtime"] is False
    assert job["overtime_at"] is None
    assert job["finished_at"]

    names = store.event_names()
    assert names.count("job.failed") == 1
    assert "job.completed" not in names
    # 软超时先到：加班事件出现过，且硬超时事件可与之区分
    assert names.count("job.overtime") == 1
    failed_data = [d for n, d in store.events if n == "job.failed"][0]
    assert failed_data["hard_timeout_seconds"] == 0.15
    # 内层协程确实被 cancel，不可能再写终态（防双写）
    assert cancelled_flag == [True]
    no_db_failure_mark.assert_called_once()


def test_hard_timeout_equal_to_soft_skips_overtime_event(
    store: _RecordingJobStore,
    monkeypatch: pytest.MonkeyPatch,
    no_db_failure_mark,
):
    """软上限 ≥ 硬上限时不发误导性加班事件，直接 fail-closed。"""
    release = asyncio.Event()
    monkeypatch.setattr(api_main, "_run_job_inner", _wedged_inner(release))
    _patch_timeouts(monkeypatch, soft=0.08, hard=0.08)

    asyncio.run(asyncio.wait_for(_run_job_async("job-eq"), timeout=10.0))

    names = store.event_names()
    assert "job.overtime" not in names
    assert names.count("job.failed") == 1
    assert store.get_job("job-eq")["status"] == "failed"


def test_soft_timeout_disabled_hard_backstop_still_closes(
    store: _RecordingJobStore,
    monkeypatch: pytest.MonkeyPatch,
    no_db_failure_mark,
):
    """软超时禁用（0）时，硬超时兜底仍然独立生效。"""
    release = asyncio.Event()
    monkeypatch.setattr(api_main, "_run_job_inner", _wedged_inner(release))
    _patch_timeouts(monkeypatch, soft=0, hard=0.1)

    asyncio.run(asyncio.wait_for(_run_job_async("job-nosoft"), timeout=10.0))

    names = store.event_names()
    assert "job.overtime" not in names
    assert names.count("job.failed") == 1
    job = store.get_job("job-nosoft")
    assert job["status"] == "failed"
    assert "硬性运行上限" in job["error"]


# ---------------------------------------------------------------------------
# 真实失败与完成可区分
# ---------------------------------------------------------------------------


def test_inner_failure_marks_failed_with_error(
    store: _RecordingJobStore,
    monkeypatch: pytest.MonkeyPatch,
    no_db_failure_mark,
):
    """内层初始化失败（在自身 try 之外抛出）→ 外层标 failed，与完成可区分。"""

    async def _inner(job_id, request, stream_events=False, save_report=True,
                     user_id=None, request_source="api"):
        await asyncio.sleep(0.01)
        raise RuntimeError("boom: init failed")

    monkeypatch.setattr(api_main, "_run_job_inner", _inner)
    _patch_timeouts(monkeypatch, soft=30.0, hard=60.0)

    asyncio.run(_run_job_async("job-boom"))

    job = store.get_job("job-boom")
    assert job["status"] == "failed"
    assert "RuntimeError: boom" in job["error"]
    assert job["overtime"] is False
    names = store.event_names()
    assert names.count("job.failed") == 1
    assert "job.completed" not in names
    no_db_failure_mark.assert_called_once()


def test_save_report_or_raise_never_fakes_completion():
    """报告持久化失败必须抛回失败边界，不能被日志吞掉后伪装 completed。"""
    # 与生产用法一致：传入同步 DB 落盘函数，由 to_thread 执行
    def _ok():
        return "report-1"

    def _boom():
        raise ValueError("disk full")

    async def _scenario() -> None:
        assert await api_main._save_report_or_raise("j1", _ok, stage="save") == "report-1"

        with pytest.raises(RuntimeError, match="Failed to save report for job j2"):
            await api_main._save_report_or_raise("j2", _boom, stage="save")

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# Redis 与内存 store 布尔语义一致（序列化层，无需 Redis 连接）
# ---------------------------------------------------------------------------


def test_redis_bool_serialization_matches_memory_store():
    """overtime 布尔经 Redis 序列化后必须还原为真实布尔，与内存 store 一致。

    get_job_status 用 bool(job.get("overtime", False)) 读取；若 Redis 以
    str(True)="True" 存储，任何字符串读回都是真值，两边语义就会分裂。
    """
    from api.job_store_redis import _deserialize_value, _serialize_value

    for value in (False, True):
        raw = _serialize_value(value)
        restored = _deserialize_value(raw)
        assert isinstance(restored, bool)
        assert restored is value
        # 与内存 store 的原生布尔完全一致
        memory = InMemoryJobStore()
        memory.set_job("j", overtime=value)
        assert memory.get_job("j")["overtime"] is restored

    # None / 常规标量语义保持不变
    assert _deserialize_value(_serialize_value(None)) is None
    assert _deserialize_value(_serialize_value("2026-09-07T00:00:00+00:00")) == "2026-09-07T00:00:00+00:00"


# ---------------------------------------------------------------------------
# [r1 P1/P2 修复] 错过终态事件后的重放决策：共享纯函数，无需活 Redis
# ---------------------------------------------------------------------------


def test_terminal_replay_event_completed_payload_matches_memory_contract():
    """completed 快照 → job.completed，字段与回退规则符合内存 store 既有契约。"""
    job = {
        "status": "completed",
        "decision": "BUY",
        "direction": None,
        "result": {"direction": "bullish", "confidence": 0.8, "target_price": 12.5, "stop_loss_price": 10.0},
        "risk_items": None,
        "key_metrics": ["pe"],
        "confidence": None,
        "target_price": None,
        "stop_loss_price": None,
    }
    replay = terminal_replay_event("j1", job)
    assert replay["event"] == "job.completed"
    data = replay["data"]
    assert data["job_id"] == "j1"
    assert data["decision"] == "BUY"
    assert data["direction"] == "bullish"  # 顶层缺失时回退 result.direction
    assert data["result"] == job["result"]
    assert data["risk_items"] == []  # None → 空列表
    assert data["key_metrics"] == ["pe"]
    assert data["confidence"] == 0.8  # 顶层缺失时回退 result
    assert data["target_price"] == 12.5
    assert data["stop_loss_price"] == 10.0
    # 时间戳由调用方补，决策函数不产时间戳
    assert "timestamp" not in replay


def test_terminal_replay_event_failed_and_non_terminal():
    """failed → job.failed（error 兜底）；非终态/缺状态 → 不重放。"""
    replay = terminal_replay_event("j1", {"status": "failed", "error": "boom"})
    assert replay["event"] == "job.failed"
    assert replay["data"] == {"job_id": "j1", "error": "boom"}

    # error 缺失/None → 固定兜底文案，不产生 None error
    assert terminal_replay_event("j1", {"status": "failed"})["data"]["error"] == "job failed"
    assert terminal_replay_event("j1", {"status": "failed", "error": None})["data"]["error"] == "job failed"

    # 非终态或快照缺失 → None（订阅端继续 ping，不断流）
    assert terminal_replay_event("j1", {"status": "running"}) is None
    assert terminal_replay_event("j1", {}) is None

    # result 非法（非 dict）时不得让 SSE 生成器崩溃，只丢弃回退字段
    degraded = terminal_replay_event("j1", {"status": "completed", "result": "garbage"})
    assert degraded["data"]["direction"] is None
    assert degraded["data"]["result"] == "garbage"


def test_memory_store_subscribe_replays_via_shared_decision_function():
    """内存 store 超时轮询补发的终态与纯函数决策完全一致（防两处漂移）。"""

    async def scenario():
        memory = InMemoryJobStore()
        memory.set_job(
            "j1",
            status="completed",
            decision="HOLD",
            result={"direction": "neutral"},
            risk_items=[],
            key_metrics=[],
        )
        events = [event async for event in memory.subscribe("j1", poll_interval=0.05)]
        assert len(events) == 1
        expected = terminal_replay_event("j1", memory.get_job("j1"))
        assert events[0]["event"] == expected["event"]
        assert events[0]["data"] == expected["data"]
        assert events[0]["timestamp"]

    asyncio.run(scenario())


def test_redis_replay_decision_identical_to_memory_without_live_redis():
    """同一 job 快照经 Redis 序列化往返后，重放决策与内存视图逐字段一致。

    本用例不依赖活 Redis：只走 _serialize_value/_deserialize_value 往返，
    Redis 未运行的环境同样执行（r1 review P2 的语义盲区在此收口）。
    """
    from api.job_store_redis import _deserialize_value, _serialize_value

    job = {
        "status": "completed",
        "decision": "BUY",
        "result": {"direction": "bullish", "confidence": 0.8, "target_price": 12.5},
        "risk_items": None,
        "key_metrics": [],
        "confidence": None,
        "overtime": False,
        "error": None,
    }
    redis_view = {k: _deserialize_value(_serialize_value(v)) for k, v in job.items()}
    # 序列化往返不得改变重放决策（布尔/None/嵌套 dict 全部还原）
    assert terminal_replay_event("j1", redis_view) == terminal_replay_event("j1", job)


# ---------------------------------------------------------------------------
# 状态读路径：overtime 字段与历史 failed(timeout) 不改写
# ---------------------------------------------------------------------------


def test_job_status_exposes_overtime_fields(store: _RecordingJobStore):
    """get_job_status 暴露 overtime/overtime_at，running 态可被前端识别。"""
    api_main._set_job(
        "job-ot",
        job_id="job-ot",
        status="running",
        created_at=api_main._utcnow_iso(),
        started_at=api_main._utcnow_iso(),
        symbol="600519.SH",
        trade_date="2026-09-07",
        overtime=True,
        overtime_at=api_main._utcnow_iso(),
        user_id=None,
    )

    resp = api_main.get_job_status("job-ot", current_user=SimpleNamespace(id="u1"))
    assert resp.status == "running"
    assert resp.overtime is True
    assert resp.overtime_at


def test_legacy_failed_timeout_is_never_rewritten_to_running(
    store: _RecordingJobStore,
):
    """历史 failed(timeout) 记录读路径保持 failed，不被无条件重写。"""
    legacy_error = "任务超时（超过 1800 秒），已自动终止"
    api_main._set_job(
        "job-legacy",
        job_id="job-legacy",
        status="failed",
        created_at=api_main._utcnow_iso(),
        symbol="600519.SH",
        trade_date="2026-09-07",
        error=legacy_error,
        overtime=False,
        overtime_at=None,
        user_id=None,
    )
    before = store.get_job("job-legacy")

    resp = api_main.get_job_status("job-legacy", current_user=SimpleNamespace(id="u1"))

    # 读路径是只读的：状态、错误文案与存储内容完全不变
    assert resp.status == "failed"
    assert resp.overtime is False
    assert resp.error == legacy_error
    assert store.get_job("job-legacy") == before


# ---------------------------------------------------------------------------
# 完成收口清除历史 failed 痕迹（报告服务）
# ---------------------------------------------------------------------------


def test_create_report_clears_stale_failure_on_completion():
    """旧看门狗把报告标 failed 后，成功落盘必须清除 error，不得残留失败态。"""
    from api.database import get_db_ctx, init_db
    from api.services import report_service

    init_db()
    with get_db_ctx() as db:
        report = report_service.create_report(
            db,
            symbol="600519.SH",
            trade_date="2026-09-07",
            decision=None,
            result_data={"symbol": "600519.SH"},
            report_id="job-stale-err",
        )
        report.status = "failed"
        report.error = "任务超时（超过 1800 秒），已自动终止"
        db.commit()

        finalized = report_service.create_report(
            db,
            symbol="600519.SH",
            trade_date="2026-09-07",
            decision="HOLD",
            result_data={"symbol": "600519.SH", "direction": "neutral"},
            report_id="job-stale-err",
        )

        assert finalized.status == "completed"
        assert finalized.error is None
