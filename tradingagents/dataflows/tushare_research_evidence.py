"""TA-TUSHARE-2000-001C: Tushare research evidence pack for the knowledge base.

Builds a normalized, read-only JSON evidence pack covering the Tushare
research endpoints (three statements, indicators, forecast, express, main
business composition, audit opinion, dividends) on top of the 001B
eight-state query contract.  TA only collects, normalizes and exports this
evidence: it does not compute knowledge-base research scores and never
writes into knowledge-base directories.

Every endpoint entry carries the query params, query time, data period,
response SHA-256, cache metadata, sanitized error and permission status.
Failed / not-queried / field-missing / stale endpoints carry ``records=None``
so an empty array can never masquerade as a successful query; a legitimate
``NORMAL_NO_DATA`` keeps ``records=[]`` with ``row_count=0``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from tradingagents.dataflows.providers.cn_tushare_provider import (
    CnTushareProvider,
    _normalize_ts_code,
)
from tradingagents.dataflows.tushare_capability import (
    canonical_frame_payload,
)
from tradingagents.dataflows.tushare_query_contract import (
    STATE_FIELD_MISSING,
    STATE_HAS_DATA,
    STATE_NORMAL_NO_DATA,
    STATE_NOT_QUERIED,
    STATE_STALE,
    execute_structured_query,
    not_queried_result,
)

EVIDENCE_SCHEMA_VERSION = "1.0.0"
TASK_ID = "TA-TUSHARE-2000-001C"

TOKEN_STATUS_HAS_KEY = "HAS_KEY"
TOKEN_STATUS_NO_KEY = "NO_KEY"

STATEMENT_WINDOW_DAYS = 1100

WINDOW_STATEMENT = "statement_window"
WINDOW_SYMBOL_ONLY = "symbol_only"


@dataclass(frozen=True)
class EvidenceEndpointSpec:
    endpoint: str
    category: str
    window: str
    required_fields: tuple[str, ...]
    description: str


EVIDENCE_ENDPOINT_SPECS: tuple[EvidenceEndpointSpec, ...] = (
    EvidenceEndpointSpec(
        "income",
        "financial_statement",
        WINDOW_STATEMENT,
        ("ts_code", "end_date"),
        "利润表",
    ),
    EvidenceEndpointSpec(
        "balancesheet",
        "financial_statement",
        WINDOW_STATEMENT,
        ("ts_code", "end_date"),
        "资产负债表",
    ),
    EvidenceEndpointSpec(
        "cashflow",
        "financial_statement",
        WINDOW_STATEMENT,
        ("ts_code", "end_date"),
        "现金流量表",
    ),
    EvidenceEndpointSpec(
        "fina_indicator",
        "financial_indicator",
        WINDOW_STATEMENT,
        ("ts_code", "end_date"),
        "财务指标",
    ),
    EvidenceEndpointSpec(
        "forecast",
        "performance_forecast",
        WINDOW_STATEMENT,
        ("ts_code", "ann_date"),
        "业绩预告",
    ),
    EvidenceEndpointSpec(
        "express",
        "performance_express",
        WINDOW_STATEMENT,
        ("ts_code", "ann_date"),
        "业绩快报",
    ),
    EvidenceEndpointSpec(
        "dividend",
        "dividend",
        WINDOW_SYMBOL_ONLY,
        ("ts_code", "end_date"),
        "分红",
    ),
    EvidenceEndpointSpec(
        "fina_audit",
        "audit_opinion",
        WINDOW_SYMBOL_ONLY,
        ("ts_code", "end_date"),
        "审计意见",
    ),
    EvidenceEndpointSpec(
        "fina_mainbz",
        "main_business",
        WINDOW_SYMBOL_ONLY,
        ("ts_code", "end_date"),
        "主营业务构成",
    ),
)

# States whose endpoint entries must never embed records: doing so would let
# failures or stale/contract-violating shapes masquerade as usable evidence.
_RECORDS_FORBIDDEN_STATES = frozenset(
    {
        "QUERY_FAILED",
        "PERMISSION_DENIED",
        "RATE_LIMITED",
        "FIELD_MISSING",
        "NOT_QUERIED",
        "STALE",
    }
)


def _statement_window(as_of: str) -> tuple[str, str]:
    end = date.fromisoformat(as_of)
    start = date.fromordinal(end.toordinal() - STATEMENT_WINDOW_DAYS)
    return start.isoformat().replace("-", ""), end.isoformat().replace("-", "")


def evidence_params(spec: EvidenceEndpointSpec, ts_code: str, as_of: str) -> dict[str, str]:
    params: dict[str, str] = {"ts_code": ts_code}
    if spec.window == WINDOW_STATEMENT:
        start, end = _statement_window(as_of)
        params["start_date"] = start
        params["end_date"] = end
    return params


def _records_for_state(state: str, frame: pd.DataFrame | None) -> list[dict[str, Any]] | None:
    if state in _RECORDS_FORBIDDEN_STATES:
        return None
    normalized = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    if normalized.empty:
        return []
    return json.loads(canonical_frame_payload(normalized))


def build_evidence_endpoint_record(
    query_fn: Callable[..., pd.DataFrame],
    spec: EvidenceEndpointSpec,
    *,
    ts_code: str,
    as_of: str,
    token: str = "",
    permission_status: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    params = evidence_params(spec, ts_code, as_of)
    result = execute_structured_query(
        query_fn,
        spec.endpoint,
        params,
        token=token,
        required_fields=spec.required_fields,
        now=now,
    )
    record = result.to_dict()
    record["category"] = spec.category
    record["description"] = spec.description
    record["permission_status"] = permission_status
    raw_frame = result.frame if isinstance(result.frame, pd.DataFrame) else None
    if raw_frame is not None and not raw_frame.empty:
        # The knowledge-base consumer must only see rows already disclosed at
        # as_of; the hash above still refers to the full raw response.
        eligible = CnTushareProvider._filter_as_of(raw_frame, as_of.replace("-", ""))
    else:
        eligible = raw_frame
    record["eligible_row_count"] = int(len(eligible)) if eligible is not None else None
    record["records"] = _records_for_state(result.state, eligible)
    return record


def build_tushare_research_evidence(
    *,
    symbol: str,
    as_of: str,
    query_fn: Callable[..., pd.DataFrame] | None = None,
    token: str = "",
    token_status: str = TOKEN_STATUS_HAS_KEY,
    collection_mode: str = "live",
    permission_tier: str | None = None,
    permission_matrix_ref: str | None = None,
    endpoint_permissions: Mapping[str, str] | None = None,
    generated_at: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Collect the Tushare research evidence pack for one symbol.

    ``token_status=NO_KEY`` fails closed for live collection: every endpoint
    is reported as ``NOT_QUERIED`` and the query function is never invoked.
    ``collection_mode="fixture"`` marks an offline sanitized-fixture run where
    no network call happens, so ``NO_KEY`` does not force ``NOT_QUERIED``;
    the pack records ``collection_mode`` for audit honesty.
    """

    if collection_mode not in ("live", "fixture"):
        raise ValueError("collection_mode must be 'live' or 'fixture'")
    ts_code = _normalize_ts_code(symbol)
    normalized_as_of = date.fromisoformat(as_of).isoformat()
    stamp = generated_at or (now or datetime.now)().astimezone().isoformat(timespec="seconds")
    endpoint_permissions = dict(endpoint_permissions or {})
    fail_closed_no_key = (
        token_status == TOKEN_STATUS_NO_KEY and collection_mode != "fixture"
    )

    endpoints: dict[str, dict[str, Any]] = {}
    if fail_closed_no_key:
        for spec in EVIDENCE_ENDPOINT_SPECS:
            params = evidence_params(spec, ts_code, normalized_as_of)
            record = not_queried_result(
                spec.endpoint, params, "TUSHARE_TOKEN NO_KEY: fail closed", now=now
            ).to_dict()
            record["category"] = spec.category
            record["description"] = spec.description
            record["permission_status"] = endpoint_permissions.get(spec.endpoint)
            record["eligible_row_count"] = None
            record["records"] = None
            endpoints[spec.endpoint] = record
    else:
        if query_fn is None:
            raise ValueError("query_fn is required for evidence collection")
        for spec in EVIDENCE_ENDPOINT_SPECS:
            endpoints[spec.endpoint] = build_evidence_endpoint_record(
                query_fn,
                spec,
                ts_code=ts_code,
                as_of=normalized_as_of,
                token=token,
                permission_status=endpoint_permissions.get(spec.endpoint),
                now=now,
            )

    by_state: dict[str, int] = {}
    for record in endpoints.values():
        by_state[record["state"]] = by_state.get(record["state"], 0) + 1
    if by_state.get(STATE_HAS_DATA):
        status = STATE_HAS_DATA
    elif any(
        by_state.get(state) for state in ("QUERY_FAILED", "PERMISSION_DENIED", "RATE_LIMITED", "STALE")
    ):
        status = "QUERY_FAILED"
    elif by_state.get(STATE_FIELD_MISSING):
        status = STATE_FIELD_MISSING
    elif by_state.get(STATE_NORMAL_NO_DATA):
        status = STATE_NORMAL_NO_DATA
    else:
        status = STATE_NOT_QUERIED

    pack: dict[str, Any] = {
        "schema": "tushare_research_evidence",
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "task_id": TASK_ID,
        "symbol": ts_code,
        "as_of": normalized_as_of,
        "generated_at": stamp,
        "token_status": token_status,
        "collection_mode": collection_mode,
        "permission_level": {
            "tier": permission_tier,
            "matrix_ref": permission_matrix_ref,
        },
        "status": status,
        "summary": {
            "endpoint_count": len(endpoints),
            "by_state": by_state,
            "eligible_rows_total": sum(
                record["eligible_row_count"] or 0
                for record in endpoints.values()
                if record.get("eligible_row_count") is not None
            ),
        },
        "endpoints": endpoints,
        "limitations": [
            "TA 只负责采集、标准化与导出，不计算知识库研究分，也不写知识库目录",
            "records 已按 as_of 过滤未来披露；response_sha256 指向未过滤的原始响应",
            "失败、未查询、字段缺失与过期缓存端点 records=None，不以空数组冒充成功",
        ],
    }
    validate_evidence_pack(pack)
    return pack


def validate_evidence_pack(pack: Mapping[str, Any]) -> None:
    """Fail closed when a pack would fake success or leak state confusion."""

    endpoints = pack.get("endpoints")
    if not isinstance(endpoints, dict) or not endpoints:
        raise ValueError("evidence pack must contain endpoint records")
    for endpoint, record in endpoints.items():
        state = record.get("state")
        records = record.get("records")
        if state in _RECORDS_FORBIDDEN_STATES:
            if records is not None:
                raise ValueError(
                    f"{endpoint}: state {state} must not embed records "
                    "(empty arrays may not masquerade as success)"
                )
        elif state == STATE_NORMAL_NO_DATA:
            if records != []:
                raise ValueError(f"{endpoint}: NORMAL_NO_DATA must keep records=[]")
            if record.get("row_count") != 0:
                raise ValueError(f"{endpoint}: NORMAL_NO_DATA must carry row_count=0")
        elif state == STATE_HAS_DATA:
            if not isinstance(records, list) or not records:
                raise ValueError(f"{endpoint}: HAS_DATA requires non-empty records")
            if not isinstance(record.get("row_count"), int) or record["row_count"] < 1:
                raise ValueError(f"{endpoint}: HAS_DATA requires row_count>=1")
        else:
            raise ValueError(f"{endpoint}: unknown evidence state {state!r}")
        for key in (
            "params",
            "queried_at",
            "data_period",
            "response_sha256",
            "cache",
            "error",
        ):
            if key not in record:
                raise ValueError(f"{endpoint}: missing audit field {key!r}")
    if pack.get("token_status") not in (TOKEN_STATUS_HAS_KEY, TOKEN_STATUS_NO_KEY):
        raise ValueError("token_status must be HAS_KEY/NO_KEY only")
