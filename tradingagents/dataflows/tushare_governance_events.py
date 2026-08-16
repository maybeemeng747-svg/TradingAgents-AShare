"""TA-TUSHARE-2000-001D: read-only Tushare governance event pack for HaiRui.

Builds a normalized JSON governance-event pack (holder count, top-10 holders,
top-10 float holders, share pledge, holder trades, share float, repurchase)
for one A-share symbol on top of the 001B eight-state contract.  TA only
provides the export command/interface: it never writes into HaiRui or
knowledge-base directories and never produces official HaiRui verdicts.

Hard guarantees enforced by :func:`validate_governance_pack`:

- No-event (``NORMAL_NO_DATA``, ``records=[]``) and query failure
  (``records=None`` + sanitized error) are strictly distinguished.
- Every retained event keeps its announcement/report date columns, and each
  endpoint section records its source endpoint.
- The serialized pack never contains trading action words, so governance
  evidence cannot smuggle trading instructions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Mapping

import pandas as pd

from tradingagents.dataflows.providers.cn_tushare_provider import (
    CnTushareProvider,
    _normalize_ts_code,
)
from tradingagents.dataflows.tushare_capability import (
    canonical_frame_payload,
    extract_time_range,
    payload_sha256,
)
from tradingagents.dataflows.tushare_query_contract import (
    STATE_HAS_DATA,
    STATE_NORMAL_NO_DATA,
    STATE_NOT_QUERIED,
    execute_structured_query,
    not_queried_result,
)
from tradingagents.dataflows.tushare_research_evidence import (
    TOKEN_STATUS_HAS_KEY,
    TOKEN_STATUS_NO_KEY,
    _records_for_state,
    _statement_window,
)

GOVERNANCE_SCHEMA_VERSION = "1.0.0"
TASK_ID = "TA-TUSHARE-2000-001D"

WINDOW_SYMBOL_ONLY = "symbol_only"
WINDOW_MARKET_FILTERED = "market_filtered"

# Trading action vocabulary banned from governance output: the pack is
# factual evidence and must never carry executable trading instructions.
_ACTION_WORD_RE = re.compile(
    r"(?i)(买入|卖出|加仓|减仓|建仓|清仓|止盈|止损|做多|做空|满仓|\bbuy\b|\bsell\b)"
)


@dataclass(frozen=True)
class GovernanceEndpointSpec:
    endpoint: str
    category: str
    window: str
    required_fields: tuple[str, ...]
    description: str


GOVERNANCE_ENDPOINT_SPECS: tuple[GovernanceEndpointSpec, ...] = (
    GovernanceEndpointSpec(
        "stk_holdernumber",
        "holder_number",
        WINDOW_SYMBOL_ONLY,
        ("ts_code", "end_date"),
        "股东人数",
    ),
    GovernanceEndpointSpec(
        "top10_holders",
        "top10_holders",
        WINDOW_SYMBOL_ONLY,
        ("ts_code", "end_date"),
        "前十大股东",
    ),
    GovernanceEndpointSpec(
        "top10_floatholders",
        "top10_floatholders",
        WINDOW_SYMBOL_ONLY,
        ("ts_code", "end_date"),
        "前十大流通股东",
    ),
    GovernanceEndpointSpec(
        "pledge_stat",
        "pledge_stat",
        WINDOW_SYMBOL_ONLY,
        ("ts_code", "end_date"),
        "股权质押统计",
    ),
    GovernanceEndpointSpec(
        "pledge_detail",
        "pledge_detail",
        WINDOW_SYMBOL_ONLY,
        ("ts_code", "end_date"),
        "股权质押明细",
    ),
    GovernanceEndpointSpec(
        "stk_holdertrade",
        "holder_trade",
        WINDOW_SYMBOL_ONLY,
        ("ts_code", "ann_date"),
        "股东增减持",
    ),
    GovernanceEndpointSpec(
        "share_float",
        "share_float",
        WINDOW_SYMBOL_ONLY,
        ("ts_code", "ann_date"),
        "限售股解禁",
    ),
    GovernanceEndpointSpec(
        "repurchase",
        "repurchase",
        WINDOW_MARKET_FILTERED,
        ("ts_code", "ann_date"),
        "股票回购（市场窗口查询后本地按 ts_code 过滤）",
    ),
)

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


def governance_params(
    spec: GovernanceEndpointSpec, ts_code: str, as_of: str
) -> dict[str, str]:
    params: dict[str, str] = {"ts_code": ts_code}
    if spec.window == WINDOW_MARKET_FILTERED:
        params.pop("ts_code")  # repurchase does not accept ts_code upstream
        start, end = _statement_window(as_of)
        params["start_date"] = start
        params["end_date"] = end
    return params


def _annotate(record: dict[str, Any], spec: GovernanceEndpointSpec, permission: str | None) -> None:
    record["category"] = spec.category
    record["description"] = spec.description
    record["source_endpoint"] = spec.endpoint
    record["permission_status"] = permission


def build_governance_endpoint_record(
    query_fn: Callable[..., pd.DataFrame],
    spec: GovernanceEndpointSpec,
    *,
    ts_code: str,
    as_of: str,
    token: str = "",
    permission_status: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    params = governance_params(spec, ts_code, as_of)
    result = execute_structured_query(
        query_fn,
        spec.endpoint,
        params,
        token=token,
        required_fields=spec.required_fields,
        now=now,
    )
    record = result.to_dict()
    _annotate(record, spec, permission_status)
    raw_frame = result.frame if isinstance(result.frame, pd.DataFrame) else None
    frame = raw_frame
    if spec.window == WINDOW_MARKET_FILTERED and frame is not None and not frame.empty:
        # Keep only this symbol's events; the hash and counts below therefore
        # describe the symbol pack, never the unfiltered market window.
        matched = frame.loc[frame["ts_code"].astype(str).str.upper().eq(ts_code)]
        record["response_sha256"] = payload_sha256(canonical_frame_payload(matched))
        record["response_sha256_scope"] = "frame_symbol_filtered"
        record["row_count"] = int(len(matched))
        if matched.empty:
            record["state"] = STATE_NORMAL_NO_DATA
            record["data_period"] = None
        else:
            record["data_period"] = extract_time_range(matched)
        frame = matched
    if frame is not None and not frame.empty:
        eligible = CnTushareProvider._filter_as_of(frame, as_of.replace("-", ""))
    else:
        eligible = frame if frame is not None else None
    record["eligible_row_count"] = int(len(eligible)) if eligible is not None else None
    if (
        record["state"] == STATE_HAS_DATA
        and eligible is not None
        and eligible.empty
    ):
        # Transport succeeded with rows, but nothing was disclosed at as_of:
        # keep HAS_DATA transport truth while records stay empty and explicit.
        record["eligible_row_count"] = 0
    record["records"] = _records_for_state(record["state"], eligible)
    return record


def _latest_date(records: list[Mapping[str, Any]] | None) -> str | None:
    if not records:
        return None
    candidates = [
        str(value)
        for record in records
        for key, value in record.items()
        if key in ("ann_date", "f_ann_date", "end_date", "float_date")
        and value
    ]
    return max(candidates) if candidates else None


def build_governance_event_pack(
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
    """Collect the governance event pack for one symbol (read-only export)."""

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
        for spec in GOVERNANCE_ENDPOINT_SPECS:
            params = governance_params(spec, ts_code, normalized_as_of)
            record = not_queried_result(
                spec.endpoint, params, "TUSHARE_TOKEN NO_KEY: fail closed", now=now
            ).to_dict()
            _annotate(record, spec, endpoint_permissions.get(spec.endpoint))
            record["eligible_row_count"] = None
            record["records"] = None
            endpoints[spec.endpoint] = record
    else:
        if query_fn is None:
            raise ValueError("query_fn is required for governance collection")
        for spec in GOVERNANCE_ENDPOINT_SPECS:
            endpoints[spec.endpoint] = build_governance_endpoint_record(
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

    freshness: dict[str, dict[str, Any]] = {}
    anomalies: list[dict[str, Any]] = []
    for endpoint, record in endpoints.items():
        latest = _latest_date(record.get("records"))
        freshness[endpoint] = {
            "latest_event_date": latest,
            "as_of": normalized_as_of,
            "days_behind_as_of": (
                (
                    date.fromisoformat(normalized_as_of)
                    - date(
                        int(latest[:4]), int(latest[4:6]), int(latest[6:8])
                    )
                ).days
                if latest
                else None
            ),
        }
        if record["state"] not in (STATE_HAS_DATA, STATE_NORMAL_NO_DATA):
            anomalies.append(
                {
                    "endpoint": endpoint,
                    "state": record["state"],
                    "error": record.get("error"),
                }
            )

    _BLOCKING_FAILURE_STATES = frozenset(
        {
            "QUERY_FAILED",
            "PERMISSION_DENIED",
            "RATE_LIMITED",
            "FIELD_MISSING",
            "STALE",
        }
    )
    has_blocking_failure = any(
        by_state.get(state) for state in _BLOCKING_FAILURE_STATES
    )
    if by_state.get(STATE_HAS_DATA):
        status = STATE_HAS_DATA
    elif has_blocking_failure:
        status = "QUERY_FAILED"
    elif by_state.get(STATE_NORMAL_NO_DATA):
        status = STATE_NORMAL_NO_DATA
    else:
        status = STATE_NOT_QUERIED

    pack: dict[str, Any] = {
        "schema": "tushare_governance_events",
        "governance_schema_version": GOVERNANCE_SCHEMA_VERSION,
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
            "event_rows_total": sum(
                record["eligible_row_count"] or 0
                for record in endpoints.values()
                if record.get("eligible_row_count") is not None
            ),
            "anomaly_endpoint_count": len(anomalies),
        },
        "freshness": freshness,
        "anomalies": anomalies,
        "endpoints": endpoints,
        "limitations": [
            "TA 只提供导出命令/调用接口，不直接写海瑞目录，也不生成海瑞正式审判记录",
            "无事件（NORMAL_NO_DATA）与查询失败（错误状态）严格区分，不以空数组冒充成功",
            "每条事件保留公告/报告日期字段，source endpoint 记录在端点分组上",
            "输出不含交易动作词；records 已按 as_of 过滤未来披露",
        ],
    }
    validate_governance_pack(pack)
    return pack


def validate_governance_pack(pack: Mapping[str, Any]) -> None:
    """Fail closed on state confusion, missing dates or action words."""

    endpoints = pack.get("endpoints")
    if not isinstance(endpoints, dict) or not endpoints:
        raise ValueError("governance pack must contain endpoint records")
    for endpoint, record in endpoints.items():
        state = record.get("state")
        records = record.get("records")
        if state in _RECORDS_FORBIDDEN_STATES:
            if records is not None:
                raise ValueError(
                    f"{endpoint}: state {state} must not embed records"
                )
        elif state == STATE_NORMAL_NO_DATA:
            if records != []:
                raise ValueError(f"{endpoint}: NORMAL_NO_DATA must keep records=[]")
        elif state == STATE_HAS_DATA:
            if not isinstance(records, list) or not records:
                raise ValueError(f"{endpoint}: HAS_DATA requires non-empty records")
        else:
            raise ValueError(f"{endpoint}: unknown governance state {state!r}")
        if not record.get("source_endpoint"):
            raise ValueError(f"{endpoint}: source endpoint is mandatory")
        for event in records or []:
            if not any(event.get(key) for key in ("ann_date", "f_ann_date", "end_date", "float_date")):
                raise ValueError(
                    f"{endpoint}: events must keep announcement/report dates"
                )
    if pack.get("token_status") not in (TOKEN_STATUS_HAS_KEY, TOKEN_STATUS_NO_KEY):
        raise ValueError("token_status must be HAS_KEY/NO_KEY only")
    text = json.dumps(pack, ensure_ascii=False)
    match = _ACTION_WORD_RE.search(text)
    if match:
        raise ValueError(f"governance pack must not contain trading action words: {match.group(0)!r}")
