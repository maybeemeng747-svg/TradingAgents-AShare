"""
Unit tests for LLM rate limiter and retry logic.

Tests mock 429/5xx/timeout/non-retryable responses.
Covers: sync invoke, async ainvoke, semaphore contention, error classification.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import tradingagents.llm_clients.rate_limiter as rl


# Patch target for ChatOpenAI inside rate_limiter
CHAT_OPENAI_TARGET = "tradingagents.llm_clients.rate_limiter.ChatOpenAI"


# Reset global state between tests
@pytest.fixture(autouse=True)
def _reset_semaphore():
    rl._thread_semaphore = None
    rl._async_semaphore = None
    rl._max_concurrent = 3
    yield
    rl._thread_semaphore = None
    rl._async_semaphore = None
    rl._max_concurrent = 3


# ── Error Classification ──

class TestClassifyError:
    def test_429_status_code(self):
        err = Exception("rate limited")
        err.status_code = 429
        assert rl._classify_error(err) == "429"

    def test_429_in_message(self):
        assert rl._classify_error(Exception("Error 429: Too Many Requests")) == "429"

    def test_rate_limit_in_message(self):
        assert rl._classify_error(Exception("Rate limit exceeded")) == "429"

    def test_500_status_code(self):
        err = Exception("server error")
        err.status_code = 500
        assert rl._classify_error(err) == "5xx"

    def test_502_in_message(self):
        assert rl._classify_error(Exception("Bad Gateway 502")) == "5xx"

    def test_503_in_message(self):
        assert rl._classify_error(Exception("Service Unavailable 503")) == "5xx"

    def test_timeout_in_message(self):
        assert rl._classify_error(Exception("Request timed out")) == "timeout"

    def test_timeout_status_code(self):
        assert rl._classify_error(Exception("Connection timeout after 300s")) == "timeout"

    def test_non_retryable(self):
        assert rl._classify_error(Exception("Insufficient funds")) is None

    def test_402_not_retryable(self):
        err = Exception("Payment required")
        err.status_code = 402
        assert rl._classify_error(err) is None

    def test_auth_error_not_retryable(self):
        err = Exception("Invalid API key")
        err.status_code = 401
        assert rl._classify_error(err) is None


# ── Delay Computation ──

class TestComputeDelay:
    def test_429_exponential(self):
        assert rl._compute_delay("429", 0) == 1.0
        assert rl._compute_delay("429", 1) == 2.0
        assert rl._compute_delay("429", 2) == 4.0
        assert rl._compute_delay("429", 3) == 8.0

    def test_5xx_constant(self):
        assert rl._compute_delay("5xx", 0) == 1.0
        assert rl._compute_delay("5xx", 1) == 1.0

    def test_timeout_constant(self):
        assert rl._compute_delay("timeout", 0) == 2.0
        assert rl._compute_delay("timeout", 1) == 2.0


# ── Semaphore Configuration ──

class TestSemaphore:
    def test_default_semaphore(self):
        sem = rl.get_semaphore()
        assert sem._value == 3

    def test_configure_semaphore(self):
        rl.configure_semaphore(5)
        sem = rl.get_semaphore()
        assert sem._value == 5

    def test_set_max_concurrent(self):
        rl.set_max_concurrent(10)
        assert rl._max_concurrent == 10
        sem = rl.get_semaphore()
        assert sem._value == 10


# ── Sync Retry Inner Logic ──

class TestInvokeWithRetryInner:
    @patch(CHAT_OPENAI_TARGET)
    def test_success_no_retry(self, mock_cls):
        mock_result = MagicMock()
        mock_result.content = "ok"
        mock_cls.invoke = MagicMock(return_value=mock_result)

        llm_instance = MagicMock()
        result = rl._invoke_with_retry_inner(llm_instance, "test input")
        assert result.content == "ok"
        assert mock_cls.invoke.call_count == 1

    @patch("tradingagents.llm_clients.rate_limiter.time.sleep")
    @patch(CHAT_OPENAI_TARGET)
    def test_429_retries_3_times(self, mock_cls, mock_sleep):
        err = Exception("429 Too Many Requests")
        mock_cls.invoke = MagicMock(side_effect=[err, err, err, MagicMock(content="ok")])

        llm_instance = MagicMock()
        result = rl._invoke_with_retry_inner(llm_instance, "test input")
        assert result.content == "ok"
        assert mock_cls.invoke.call_count == 4  # 1 initial + 3 retries
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 4.0]

    @patch(CHAT_OPENAI_TARGET)
    def test_429_exhausted_raises(self, mock_cls):
        """429 exhausted retries should raise directly, no queue involved."""
        err = Exception("429 Too Many Requests")
        mock_cls.invoke = MagicMock(side_effect=err)

        llm_instance = MagicMock()
        with pytest.raises(Exception, match="429"):
            rl._invoke_with_retry_inner(llm_instance, "test input")
        assert mock_cls.invoke.call_count == 4  # 1 + 3 retries

    @patch("tradingagents.llm_clients.rate_limiter.time.sleep")
    @patch(CHAT_OPENAI_TARGET)
    def test_5xx_retries_once(self, mock_cls, mock_sleep):
        err = Exception("500 Internal Server Error")
        mock_cls.invoke = MagicMock(side_effect=[err, MagicMock(content="ok")])

        llm_instance = MagicMock()
        result = rl._invoke_with_retry_inner(llm_instance, "test input")
        assert result.content == "ok"
        assert mock_cls.invoke.call_count == 2
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0]

    @patch("tradingagents.llm_clients.rate_limiter.time.sleep")
    @patch(CHAT_OPENAI_TARGET)
    def test_timeout_retries_once(self, mock_cls, mock_sleep):
        err = Exception("Request timed out")
        mock_cls.invoke = MagicMock(side_effect=[err, MagicMock(content="ok")])

        llm_instance = MagicMock()
        result = rl._invoke_with_retry_inner(llm_instance, "test input")
        assert result.content == "ok"
        assert mock_cls.invoke.call_count == 2
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [2.0]

    @patch(CHAT_OPENAI_TARGET)
    def test_non_retryable_raises_immediately(self, mock_cls):
        err = Exception("Insufficient funds")
        mock_cls.invoke = MagicMock(side_effect=err)

        llm_instance = MagicMock()
        with pytest.raises(Exception, match="Insufficient"):
            rl._invoke_with_retry_inner(llm_instance, "test input")
        assert mock_cls.invoke.call_count == 1

    @patch(CHAT_OPENAI_TARGET)
    def test_402_raises_immediately(self, mock_cls):
        """402/auth errors must fail immediately without retry."""
        err = Exception("Payment Required")
        err.status_code = 402
        mock_cls.invoke = MagicMock(side_effect=err)

        llm_instance = MagicMock()
        with pytest.raises(Exception, match="Payment"):
            rl._invoke_with_retry_inner(llm_instance, "test input")
        assert mock_cls.invoke.call_count == 1

    @patch("tradingagents.llm_clients.rate_limiter.time.sleep")
    @patch(CHAT_OPENAI_TARGET)
    def test_mixed_errors_5xx_then_429(self, mock_cls, mock_sleep):
        """5xx then 429 then success — each category has independent retry counters."""
        err_5xx = Exception("500 Server Error")
        err_429 = Exception("429 Rate Limited")
        mock_cls.invoke = MagicMock(side_effect=[err_5xx, err_429, MagicMock(content="ok")])

        llm_instance = MagicMock()
        result = rl._invoke_with_retry_inner(llm_instance, "test input")
        assert result.content == "ok"
        assert mock_cls.invoke.call_count == 3


# ── Async Retry Inner Logic ──

class TestAinvokeWithRetryInner:
    def test_async_429_retries(self):
        """Async 429 retry works with asyncio.sleep."""
        call_count = 0
        ok_result = MagicMock(content="ok")
        err = Exception("429 rate limit")

        async def fake_ainvoke(self, input, config=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise err
            return ok_result

        with patch.object(rl.ChatOpenAI, 'ainvoke', fake_ainvoke):
            async def run():
                return await rl._ainvoke_with_retry_inner(MagicMock(), "test input")

            result = asyncio.run(run())
            assert result.content == "ok"
            assert call_count == 3

    def test_async_429_exhausted_raises(self):
        """Async 429 retries exhausted raises directly (no queue)."""
        err = Exception("429 rate limit")

        async def fake_ainvoke(self, input, config=None, **kwargs):
            raise err

        with patch.object(rl.ChatOpenAI, 'ainvoke', fake_ainvoke):
            async def run():
                return await rl._ainvoke_with_retry_inner(MagicMock(), "test input")

            with pytest.raises(Exception, match="429"):
                asyncio.run(run())

    def test_async_402_raises_immediately(self):
        """Async 402/auth errors fail immediately without retry."""
        err = Exception("Payment Required")
        err.status_code = 402
        call_count = 0

        async def fake_ainvoke(self, input, config=None, **kwargs):
            nonlocal call_count
            call_count += 1
            raise err

        with patch.object(rl.ChatOpenAI, 'ainvoke', fake_ainvoke):
            async def run():
                return await rl._ainvoke_with_retry_inner(MagicMock(), "test input")

            with pytest.raises(Exception, match="Payment"):
                asyncio.run(run())
            assert call_count == 1

    def test_async_5xx_retries_once(self):
        """Async 5xx retries exactly once."""
        call_count = 0

        async def fake_ainvoke(self, input, config=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("500 Server Error")
            return MagicMock(content="ok")

        with patch.object(rl.ChatOpenAI, 'ainvoke', fake_ainvoke):
            async def run():
                return await rl._ainvoke_with_retry_inner(MagicMock(), "test input")

            result = asyncio.run(run())
            assert result.content == "ok"
            assert call_count == 2

    def test_async_timeout_retries_once(self):
        """Async timeout retries exactly once."""
        call_count = 0

        async def fake_ainvoke(self, input, config=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Request timed out")
            return MagicMock(content="ok")

        with patch.object(rl.ChatOpenAI, 'ainvoke', fake_ainvoke):
            async def run():
                return await rl._ainvoke_with_retry_inner(MagicMock(), "test input")

            result = asyncio.run(run())
            assert result.content == "ok"
            assert call_count == 2


# ── Async Semaphore Contention ──

class TestAsyncSemaphoreContention:
    def test_async_semaphore_no_deadlock(self):
        """max_concurrent=1, two ainvoke tasks must run serially without deadlock."""
        rl.configure_semaphore(1)
        order = []

        async def fake_ainvoke(self, input, config=None, **kwargs):
            order.append(f"start-{input}")
            await asyncio.sleep(0.05)
            order.append(f"end-{input}")
            return MagicMock(content=f"ok-{input}")

        with patch.object(rl.ChatOpenAI, 'ainvoke', fake_ainvoke):
            async def run():
                t1 = asyncio.create_task(rl.ainvoke_with_retry(MagicMock(), "A"))
                t2 = asyncio.create_task(rl.ainvoke_with_retry(MagicMock(), "B"))
                r1, r2 = await asyncio.gather(t1, t2)
                return r1, r2, order

            r1, r2, order = asyncio.run(run())
            assert r1.content == "ok-A"
            assert r2.content == "ok-B"
            # With max_concurrent=1, tasks must be serial:
            # start-A, end-A, start-B, end-B  OR  start-B, end-B, start-A, end-A
            # We just need to verify they completed and didn't interleave
            assert "start-A" in order
            assert "start-B" in order
            assert "end-A" in order
            assert "end-B" in order
            assert len(order) == 4

    def test_async_semaphore_serial_order(self):
        """With max_concurrent=1, no two tasks overlap."""
        rl.configure_semaphore(1)
        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def fake_ainvoke(self, input, config=None, **kwargs):
            nonlocal active, max_active
            async with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.03)
            async with lock:
                active -= 1
            return MagicMock(content="ok")

        with patch.object(rl.ChatOpenAI, 'ainvoke', fake_ainvoke):
            async def run():
                tasks = [asyncio.create_task(rl.ainvoke_with_retry(MagicMock(), f"T{i}")) for i in range(4)]
                results = await asyncio.gather(*tasks)
                return results

            results = asyncio.run(run())
            assert len(results) == 4
            assert max_active <= 1  # Never more than 1 concurrent


# ── Sync Semaphore Integration ──

class TestSemaphoreIntegration:
    @patch(CHAT_OPENAI_TARGET)
    def test_semaphore_limits_concurrency(self, mock_cls):
        rl.configure_semaphore(2)

        active_count = 0
        max_active = 0
        lock = threading.Lock()

        def slow_invoke(*args, **kwargs):
            nonlocal active_count, max_active
            with lock:
                active_count += 1
                max_active = max(max_active, active_count)
            time.sleep(0.05)
            with lock:
                active_count -= 1
            return MagicMock(content="ok")

        mock_cls.invoke = slow_invoke

        results = []
        errors = []

        def worker():
            try:
                result = rl.invoke_with_retry(MagicMock(), "input")
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 6
        assert len(errors) == 0
        assert max_active <= 2


# ── Full invoke_with_retry (semaphore + retry) ──

class TestInvokeWithRetry:
    @patch(CHAT_OPENAI_TARGET)
    def test_full_success(self, mock_cls):
        mock_cls.invoke = MagicMock(return_value=MagicMock(content="hello"))
        result = rl.invoke_with_retry(MagicMock(), "test")
        assert result.content == "hello"

    @patch(CHAT_OPENAI_TARGET)
    def test_full_429_exhausted_raises(self, mock_cls):
        """429 exhausted raises directly, no queue."""
        mock_cls.invoke = MagicMock(side_effect=Exception("429 rate limit"))

        with pytest.raises(Exception, match="429"):
            rl.invoke_with_retry(MagicMock(), "test")

        assert mock_cls.invoke.call_count == 4  # 1 + 3 retries


# ── Full ainvoke_with_retry (async semaphore + retry) ──

class TestAinvokeWithRetry:
    def test_full_async_success(self):
        async def fake_ainvoke(self, input, config=None, **kwargs):
            return MagicMock(content="hello-async")

        with patch.object(rl.ChatOpenAI, 'ainvoke', fake_ainvoke):
            async def run():
                return await rl.ainvoke_with_retry(MagicMock(), "test")

            result = asyncio.run(run())
            assert result.content == "hello-async"

    def test_full_async_429_exhausted_raises(self):
        """Async 429 exhausted raises directly, no queue."""
        call_count = 0

        async def fake_ainvoke(self, input, config=None, **kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("429 rate limit")

        with patch.object(rl.ChatOpenAI, 'ainvoke', fake_ainvoke):
            async def run():
                return await rl.ainvoke_with_retry(MagicMock(), "test")

            with pytest.raises(Exception, match="429"):
                asyncio.run(run())
            assert call_count == 4  # 1 + 3 retries
