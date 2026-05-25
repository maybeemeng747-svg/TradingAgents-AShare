"""
LLM Rate Limiter & Retry

Provides two capabilities:
1. Retry with exponential backoff for 429/5xx/timeout errors
2. Global semaphore to limit concurrent LLM calls (thread-safe for sync, asyncio for async)
"""

import asyncio
import logging
import threading
import time
from typing import Any, Optional

from langchain_openai import ChatOpenAI

_logger = logging.getLogger(__name__)

# ── Global Concurrency Semaphore ──
# Controls max concurrent LLM calls across the entire process.
# We maintain separate semaphores for sync (threading) and async (asyncio) paths.

_thread_semaphore: Optional[threading.Semaphore] = None
_semaphore_lock = threading.Lock()
_max_concurrent: int = 3  # default

# asyncio.Semaphore is created lazily per event-loop.
# We cache it on the module; since all async work runs in the same loop, this is safe.
_async_semaphore: Optional[asyncio.Semaphore] = None


def configure_semaphore(max_concurrent: int = 3) -> None:
    """Configure the global LLM concurrency semaphore.

    Must be called before any LLM calls. If called multiple times,
    only the first call takes effect.
    """
    global _thread_semaphore, _max_concurrent
    with _semaphore_lock:
        if _thread_semaphore is None:
            _max_concurrent = max_concurrent
            _thread_semaphore = threading.Semaphore(max_concurrent)
            _logger.info(f"[RateLimiter] Thread semaphore initialized: max={max_concurrent}")
        else:
            _logger.warning(
                f"[RateLimiter] Semaphore already initialized (max={_max_concurrent}), ignoring reconfigure"
            )


def get_semaphore() -> threading.Semaphore:
    """Get the global thread semaphore, initializing with defaults if needed."""
    global _thread_semaphore
    if _thread_semaphore is None:
        configure_semaphore(_max_concurrent)
    return _thread_semaphore


def _get_async_semaphore() -> asyncio.Semaphore:
    """Get or create the asyncio semaphore.

    Cached on the module. All async work in this process runs in the same
    event loop, so this is safe. Must be called from the async path (inside
    a running event loop).
    """
    global _async_semaphore
    if _async_semaphore is None:
        _async_semaphore = asyncio.Semaphore(_max_concurrent)
    return _async_semaphore


def set_max_concurrent(max_concurrent: int) -> None:
    """Set max concurrent calls (only effective before first use)."""
    global _max_concurrent
    _max_concurrent = max_concurrent


# ── Retry Configuration ──

RETRY_CONFIG = {
    "429": {"max_retries": 3, "base_delay": 1.0, "backoff_factor": 2.0},
    "5xx": {"max_retries": 1, "base_delay": 1.0, "backoff_factor": 1.0},
    "timeout": {"max_retries": 1, "base_delay": 2.0, "backoff_factor": 1.0},
}


def _classify_error(error: Exception) -> Optional[str]:
    """Classify an error to determine retry strategy.

    Returns one of: '429', '5xx', 'timeout', or None (non-retryable).
    """
    error_str = str(error).lower()
    status_code = getattr(error, "status_code", None)

    # Check status_code attribute (httpx/openai errors)
    if status_code == 429:
        return "429"
    if status_code is not None and 500 <= status_code < 600:
        return "5xx"

    # Check error message for known patterns
    if "429" in error_str or "rate limit" in error_str or "rate_limit" in error_str:
        return "429"
    if any(
        code in error_str
        for code in ("500", "502", "503", "504", "server error", "internal server error")
    ):
        return "5xx"
    if "timeout" in error_str or "timed out" in error_str or "deadline" in error_str:
        return "timeout"

    return None


def _compute_delay(category: str, attempt: int) -> float:
    """Compute retry delay in seconds for given category and attempt (0-indexed)."""
    cfg = RETRY_CONFIG[category]
    delay = cfg["base_delay"] * (cfg["backoff_factor"] ** attempt)
    return delay


# ── Core: invoke_with_retry (sync) ──

def invoke_with_retry(llm_instance: Any, input: Any, config: Any = None, **kwargs: Any) -> Any:
    """Call LLM invoke with retry logic and concurrency control.

    Uses threading.Semaphore for concurrency limiting.
    Retries on 429 (exponential backoff, 3 attempts), 5xx (1 attempt), timeout (1 attempt).
    Non-retryable errors (402, auth) fail immediately.
    On exhausted retries, raises the last error.
    """
    sem = get_semaphore()
    sem.acquire()
    try:
        return _invoke_with_retry_inner(llm_instance, input, config, **kwargs)
    finally:
        sem.release()


def _invoke_with_retry_inner(llm_instance: Any, input: Any, config: Any = None, **kwargs: Any) -> Any:
    """Inner retry loop for sync path."""
    retries_done = {"429": 0, "5xx": 0, "timeout": 0}

    while True:
        try:
            result = ChatOpenAI.invoke(llm_instance, input=input, config=config, **kwargs)
            return result
        except Exception as e:
            category = _classify_error(e)
            if category is None:
                _logger.error(f"[LLM Retry] Non-retryable error: {type(e).__name__}: {e}")
                raise

            cfg = RETRY_CONFIG[category]
            if retries_done[category] >= cfg["max_retries"]:
                _logger.error(
                    f"[LLM Retry] {category} retries exhausted "
                    f"({retries_done[category]}/{cfg['max_retries']}): "
                    f"{type(e).__name__}: {e}"
                )
                raise

            delay = _compute_delay(category, retries_done[category])
            retries_done[category] += 1
            _logger.warning(
                f"[LLM Retry] {category} error (attempt {retries_done[category]}/{cfg['max_retries']}), "
                f"retrying in {delay:.1f}s: {type(e).__name__}: {e}"
            )
            time.sleep(delay)


# ── Core: ainvoke_with_retry (async) ──

async def ainvoke_with_retry(llm_instance: Any, input: Any, config: Any = None, **kwargs: Any) -> Any:
    """Async version of invoke_with_retry.

    Uses asyncio.Semaphore for concurrency limiting (never blocks the event loop).
    Retries on 429 (exponential backoff, 3 attempts), 5xx (1 attempt), timeout (1 attempt).
    Non-retryable errors (402, auth) fail immediately.
    On exhausted retries, raises the last error.
    """
    sem = _get_async_semaphore()
    async with sem:
        return await _ainvoke_with_retry_inner(llm_instance, input, config, **kwargs)


async def _ainvoke_with_retry_inner(llm_instance: Any, input: Any, config: Any = None, **kwargs: Any) -> Any:
    """Async inner retry loop."""
    retries_done = {"429": 0, "5xx": 0, "timeout": 0}

    while True:
        try:
            result = await ChatOpenAI.ainvoke(llm_instance, input=input, config=config, **kwargs)
            return result
        except Exception as e:
            category = _classify_error(e)
            if category is None:
                _logger.error(f"[LLM Retry] Non-retryable error: {type(e).__name__}: {e}")
                raise

            cfg = RETRY_CONFIG[category]
            if retries_done[category] >= cfg["max_retries"]:
                _logger.error(
                    f"[LLM Retry] {category} retries exhausted "
                    f"({retries_done[category]}/{cfg['max_retries']}): "
                    f"{type(e).__name__}: {e}"
                )
                raise

            delay = _compute_delay(category, retries_done[category])
            retries_done[category] += 1
            _logger.warning(
                f"[LLM Retry] {category} error (attempt {retries_done[category]}/{cfg['max_retries']}), "
                f"retrying in {delay:.1f}s: {type(e).__name__}: {e}"
            )
            await asyncio.sleep(delay)
