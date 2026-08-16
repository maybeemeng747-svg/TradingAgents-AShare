"""TA-TUSHARE-2000-001B: unified eight-state Tushare query result contract.

Every Tushare query outcome is represented as a :class:`TushareQueryResult`
carrying endpoint, sanitized query params, query time, data period, response
SHA-256, cache metadata and a credential-free error excerpt.  The eight
states are the audit source of truth:

- ``HAS_DATA``: rows returned and all caller-required fields present.
- ``NORMAL_NO_DATA``: upstream answered successfully with zero rows.
- ``QUERY_FAILED``: generic upstream/transport failure.
- ``PERMISSION_DENIED``: upstream rejected the integral/permission level.
- ``RATE_LIMITED``: upstream throttled the call.
- ``FIELD_MISSING``: rows exist but contract-required columns are absent.
- ``NOT_QUERIED``: no upstream attempt happened (not configured, skipped).
- ``STALE``: only an expired cached copy exists; must not masquerade as fresh.

Failure states never carry numeric row counts or data periods, and a
legitimate business zero inside a returned frame stays data.  The module
reuses the 001A sanitization/hash primitives and never reads credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from .tushare_capability import (
    canonical_frame_payload,
    classify_error_message,
    extract_time_range,
    payload_sha256,
    sanitize_error_text,
)

CONTRACT_SCHEMA_VERSION = "1.0"
TASK_ID = "TA-TUSHARE-2000-001B"

STATE_HAS_DATA = "HAS_DATA"
STATE_NORMAL_NO_DATA = "NORMAL_NO_DATA"
STATE_QUERY_FAILED = "QUERY_FAILED"
STATE_PERMISSION_DENIED = "PERMISSION_DENIED"
STATE_RATE_LIMITED = "RATE_LIMITED"
STATE_FIELD_MISSING = "FIELD_MISSING"
STATE_NOT_QUERIED = "NOT_QUERIED"
STATE_STALE = "STALE"

QUERY_STATES: tuple[str, ...] = (
    STATE_HAS_DATA,
    STATE_NORMAL_NO_DATA,
    STATE_QUERY_FAILED,
    STATE_PERMISSION_DENIED,
    STATE_RATE_LIMITED,
    STATE_FIELD_MISSING,
    STATE_NOT_QUERIED,
    STATE_STALE,
)
ALL_QUERY_STATES = frozenset(QUERY_STATES)

# States that must never surface numeric row counts or data periods: they are
# not data outcomes, so writing 0/empty would fake a measured result.
# FIELD_MISSING is excluded: its frame is real evidence and always has rows,
# so row_count stays >= 1 and is never a zero masquerading as data.
_NON_DATA_STATES = frozenset(
    {
        STATE_QUERY_FAILED,
        STATE_PERMISSION_DENIED,
        STATE_RATE_LIMITED,
        STATE_NOT_QUERIED,
    }
)

RESPONSE_SCOPE_FRAME = "frame"
RESPONSE_SCOPE_STALE_FRAME = "stale_frame"
RESPONSE_SCOPE_SANITIZED_ERROR = "sanitized_error"


@dataclass(frozen=True)
class TushareCacheMetadata:
    """Auditable cache provenance for a query result."""

    hit: bool
    expired: bool
    stale_served: bool
    upstream_called: bool
    age_seconds: float | None = None
    ttl_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hit": bool(self.hit),
            "expired": bool(self.expired),
            "stale_served": bool(self.stale_served),
            "upstream_called": bool(self.upstream_called),
            "age_seconds": self.age_seconds,
            "ttl_seconds": self.ttl_seconds,
        }


def default_cache_metadata(*, upstream_called: bool = True) -> TushareCacheMetadata:
    return TushareCacheMetadata(
        hit=False,
        expired=False,
        stale_served=False,
        upstream_called=upstream_called,
        age_seconds=None,
        ttl_seconds=None,
    )


@dataclass(frozen=True)
class TushareQueryResult:
    """Structured, credential-free outcome of one Tushare endpoint query."""

    endpoint: str
    params: dict[str, str]
    state: str
    queried_at: str
    row_count: int | None
    data_period: dict[str, str] | None
    response_sha256: str | None
    response_sha256_scope: str | None
    cache: TushareCacheMetadata = field(default_factory=default_cache_metadata)
    error: str | None = None
    error_type: str | None = None
    missing_fields: tuple[str, ...] = ()
    attempts: int = 1
    retry_reason: str | None = None
    frame: pd.DataFrame | None = None
    exception: BaseException | None = None

    def __post_init__(self) -> None:
        if self.state not in ALL_QUERY_STATES:
            raise ValueError(f"unknown Tushare query state: {self.state!r}")
        if self.state in _NON_DATA_STATES:
            if self.row_count is not None or self.data_period is not None:
                raise ValueError(
                    f"{self.state} must not carry numeric row_count/data_period"
                )
        if self.state == STATE_FIELD_MISSING and not self.missing_fields:
            raise ValueError("FIELD_MISSING requires missing_fields evidence")
        if self.state != STATE_FIELD_MISSING and self.missing_fields:
            raise ValueError("missing_fields is only valid for FIELD_MISSING")
        if self.state == STATE_FIELD_MISSING and self.row_count == 0:
            raise ValueError("FIELD_MISSING row_count must reflect real rows, not 0")
        if self.state == STATE_STALE:
            if self.frame is None:
                raise ValueError("STALE requires the stale frame as evidence")
        if self.state == STATE_STALE and self.row_count is None:
            raise ValueError("STALE must report the stale frame row count")
        if (
            self.state not in _NON_DATA_STATES
            and self.frame is not None
            and self.row_count != int(len(self.frame))
        ):
            raise ValueError("row_count must match the attached frame")

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable audit record without frames or exceptions."""

        record: dict[str, Any] = {
            "contract_schema_version": CONTRACT_SCHEMA_VERSION,
            "endpoint": self.endpoint,
            "params": dict(self.params),
            "state": self.state,
            "queried_at": self.queried_at,
            "row_count": self.row_count,
            "data_period": dict(self.data_period) if self.data_period else None,
            "response_sha256": self.response_sha256,
            "response_sha256_scope": self.response_sha256_scope,
            "cache": self.cache.to_dict(),
            "error": self.error,
            "error_type": self.error_type,
            "missing_fields": list(self.missing_fields),
            "attempts": self.attempts,
            "retry_reason": self.retry_reason,
        }
        return record


def _now_iso(now: Callable[[], datetime] | None = None) -> str:
    return (now or datetime.now)().astimezone().isoformat(timespec="seconds")


def _string_params(params: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in dict(params).items()}


def classify_frame(
    frame: pd.DataFrame | None, required_fields: Sequence[str] = ()
) -> tuple[str, tuple[str, ...]]:
    """Map a returned frame to HAS_DATA / NORMAL_NO_DATA / FIELD_MISSING."""

    normalized = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    if normalized.empty:
        return STATE_NORMAL_NO_DATA, ()
    if required_fields:
        columns = set(map(str, normalized.columns))
        missing = tuple(name for name in required_fields if str(name) not in columns)
        if missing:
            return STATE_FIELD_MISSING, missing
    return STATE_HAS_DATA, ()


def frame_result(
    endpoint: str,
    params: Mapping[str, Any],
    frame: pd.DataFrame | None,
    *,
    required_fields: Sequence[str] = (),
    cache: TushareCacheMetadata | None = None,
    attempts: int = 1,
    retry_reason: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> TushareQueryResult:
    normalized = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    state, missing = classify_frame(normalized, required_fields)
    payload = canonical_frame_payload(normalized)
    return TushareQueryResult(
        endpoint=endpoint,
        params=_string_params(params),
        state=state,
        queried_at=_now_iso(now),
        row_count=int(len(normalized)),
        data_period=extract_time_range(normalized) if not normalized.empty else None,
        response_sha256=payload_sha256(payload),
        response_sha256_scope=RESPONSE_SCOPE_FRAME,
        cache=cache or default_cache_metadata(upstream_called=True),
        error=None,
        error_type=None,
        missing_fields=missing,
        attempts=attempts,
        retry_reason=retry_reason,
        frame=normalized,
        exception=None,
    )


def error_result(
    endpoint: str,
    params: Mapping[str, Any],
    exc: BaseException,
    *,
    token: str = "",
    state: str | None = None,
    cache: TushareCacheMetadata | None = None,
    raise_as: BaseException | None = None,
    attempts: int = 1,
    retry_reason: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> TushareQueryResult:
    resolved = state or classify_error_message(str(exc))
    if resolved not in (
        STATE_QUERY_FAILED,
        STATE_PERMISSION_DENIED,
        STATE_RATE_LIMITED,
        STATE_NOT_QUERIED,
    ):
        resolved = STATE_QUERY_FAILED
    text = sanitize_error_text(str(exc), token)
    return TushareQueryResult(
        endpoint=endpoint,
        params=_string_params(params),
        state=resolved,
        queried_at=_now_iso(now),
        row_count=None,
        data_period=None,
        response_sha256=payload_sha256(text),
        response_sha256_scope=RESPONSE_SCOPE_SANITIZED_ERROR,
        cache=cache or default_cache_metadata(upstream_called=True),
        error=text or None,
        error_type=type(exc).__name__,
        missing_fields=(),
        attempts=attempts,
        retry_reason=retry_reason,
        frame=None,
        exception=raise_as if raise_as is not None else exc,
    )


def stale_result(
    endpoint: str,
    params: Mapping[str, Any],
    frame: pd.DataFrame,
    *,
    age_seconds: float,
    ttl_seconds: float,
    exc: BaseException | None = None,
    error: str = "",
    token: str = "",
    raise_as: BaseException | None = None,
    attempts: int = 1,
    retry_reason: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> TushareQueryResult:
    """Build a STALE result: only an expired cached copy is available."""

    normalized = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    raw_error = error or (str(exc) if exc is not None else "")
    text = sanitize_error_text(raw_error, token)
    return TushareQueryResult(
        endpoint=endpoint,
        params=_string_params(params),
        state=STATE_STALE,
        queried_at=_now_iso(now),
        row_count=int(len(normalized)),
        data_period=extract_time_range(normalized) if not normalized.empty else None,
        response_sha256=payload_sha256(canonical_frame_payload(normalized)),
        response_sha256_scope=RESPONSE_SCOPE_STALE_FRAME,
        cache=TushareCacheMetadata(
            hit=False,
            expired=True,
            stale_served=False,
            upstream_called=True,
            age_seconds=float(age_seconds),
            ttl_seconds=float(ttl_seconds),
        ),
        error=text or None,
        error_type=type(exc).__name__ if exc is not None else None,
        missing_fields=(),
        attempts=attempts,
        retry_reason=retry_reason,
        frame=normalized,
        exception=raise_as if raise_as is not None else exc,
    )


def not_queried_result(
    endpoint: str,
    params: Mapping[str, Any],
    reason: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> TushareQueryResult:
    text = sanitize_error_text(reason)
    return TushareQueryResult(
        endpoint=endpoint,
        params=_string_params(params),
        state=STATE_NOT_QUERIED,
        queried_at=_now_iso(now),
        row_count=None,
        data_period=None,
        response_sha256=payload_sha256(text),
        response_sha256_scope=RESPONSE_SCOPE_SANITIZED_ERROR,
        cache=default_cache_metadata(upstream_called=False),
        error=text or None,
        error_type=None,
        missing_fields=(),
        frame=None,
        exception=None,
    )


def execute_structured_query(
    query_fn: Callable[..., pd.DataFrame],
    endpoint: str,
    params: Mapping[str, Any],
    *,
    token: str = "",
    required_fields: Sequence[str] = (),
    cache_entry: tuple[pd.DataFrame, float, float] | None = None,
    now: Callable[[], datetime] | None = None,
) -> TushareQueryResult:
    """Execute one endpoint query and classify it under the eight-state contract.

    ``cache_entry`` is an optional ``(frame, age_seconds, ttl_seconds)`` tuple
    describing a local cached copy.  A fresh entry short-circuits the upstream
    call (``cache.hit=True``); an expired entry that coexists with an upstream
    failure yields ``STALE`` instead of masking as fresh data.
    """

    string_params = _string_params(params)
    if cache_entry is not None:
        cached_frame, age_seconds, ttl_seconds = cache_entry
        if age_seconds <= ttl_seconds:
            return frame_result(
                endpoint,
                string_params,
                cached_frame,
                required_fields=required_fields,
                cache=TushareCacheMetadata(
                    hit=True,
                    expired=False,
                    stale_served=False,
                    upstream_called=False,
                    age_seconds=float(age_seconds),
                    ttl_seconds=float(ttl_seconds),
                ),
                now=now,
            )
    try:
        frame = query_fn(endpoint, **dict(params))
    except Exception as exc:
        if cache_entry is not None:
            _, age_seconds, ttl_seconds = cache_entry
            return stale_result(
                endpoint,
                string_params,
                cache_entry[0],
                age_seconds=age_seconds,
                ttl_seconds=ttl_seconds,
                exc=exc,
                token=token,
                now=now,
            )
        return error_result(
            endpoint, string_params, exc, token=token, now=now
        )
    return frame_result(
        endpoint,
        string_params,
        frame,
        required_fields=required_fields,
        cache=TushareCacheMetadata(
            hit=False,
            expired=False,
            stale_served=False,
            upstream_called=True,
            age_seconds=0.0,
            ttl_seconds=float(cache_entry[2]) if cache_entry is not None else None,
        ),
        now=now,
    )
