"""TA-TUSHARE-2000-001A: real Tushare 2000-point permission matrix primitives.

Single-stock, one-probe-per-endpoint capability audit helpers.  The module
never reads or stores credentials: callers (audit scripts) read the token
from the git-ignored environment file and pass it in only for redaction.
Every derived artifact exposes ``HAS_KEY``/``NO_KEY`` token status plus
sanitized error text; raw response bodies are reduced to structural
metadata (row/column counts, column names, date min/max) and a SHA-256
digest.  This module deliberately does not read the static
``SOURCE_CAPABILITY_MATRIX``: only real probe results may enter a matrix.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

MATRIX_SCHEMA_VERSION = "1.1"
TASK_ID = "TA-TUSHARE-2000-001A"

TOKEN_SOURCE_DOTENV = "dotenv_file"
TOKEN_SOURCE_INHERITED_ENV = "inherited_environment"

STATE_HAS_DATA = "HAS_DATA"
STATE_NORMAL_NO_DATA = "NORMAL_NO_DATA"
STATE_QUERY_FAILED = "QUERY_FAILED"
STATE_PERMISSION_DENIED = "PERMISSION_DENIED"
STATE_RATE_LIMITED = "RATE_LIMITED"
STATE_NOT_QUERIED = "NOT_QUERIED"

PERMISSION_ALLOWED = "allowed"
PERMISSION_DENIED = "denied"
PERMISSION_UNKNOWN = "unknown"

TOKEN_STATUS_HAS_KEY = "HAS_KEY"
TOKEN_STATUS_NO_KEY = "NO_KEY"

CATEGORY_FINANCIAL = "financial"
CATEGORY_GOVERNANCE = "governance"
CATEGORY_MARKET = "market"

_RATE_LIMIT_MARKERS = (
    "每分钟",
    "每小时",
    "每天最多",
    "访问频率",
    "频率过高",
    "超过最大访问",
)
_RATE_LIMIT_MARKERS_EN = ("rate limit", "too many requests")
_PERMISSION_MARKERS = (
    "没有权限",
    "无权限",
    "权限不足",
    "没有访问",
    "积分不足",
    "不在权限",
    "没有开通",
)
_PERMISSION_MARKERS_EN = ("permission", "not have access")

_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "connectionerror",
    "connectionreset",
    "ssl",
    "eof occurred",
    "remote end closed",
    "temporary failure",
    "temporarily unavailable",
)

_DATE_RANGE_FIELD_PRIORITY = ("trade_date", "ann_date", "f_ann_date", "end_date")

_CREDENTIAL_KEY_RE = re.compile(
    r"(?i)\b(authorization|cookie|token|secret|api[_-]?key|password|session)"
    r"(\s*[:=]\s*)((?:bearer\s+)?[^\s,;'\"]+)"
)
_CREDENTIAL_HEADER_HINT_RE = re.compile(r"""(?i)\b(authorization|cookie)\b[\\"']*\s*[:=]""")
_OPAQUE_BLOB_RE = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")

PARAM_KIND_TS_CODE = "ts_code"
PARAM_KIND_STATEMENT_WINDOW = "statement_window"
PARAM_KIND_RECENT_WINDOW = "recent_window"
PARAM_KIND_PROBE_DATE = "probe_date"
PARAM_KIND_PROBE_DATE_WITH_SYMBOL = "probe_date_with_symbol"
PARAM_KIND_EXCHANGE_PROBE_DATE = "exchange_probe_date"

ROW_CAP_DEFAULT = 2000
_ROW_CAP_NOTE = (
    "endpoint 单次探测命中 {cap} 行返回上限：权限结论（allowed）不受影响，但该行数不代表"
    "完整窗口或最小单股结果，需核对查询参数是否被上游忽略"
)


@dataclass(frozen=True)
class EndpointSpec:
    endpoint: str
    category: str
    param_kinds: tuple[str, ...]
    scope: str = "symbol"
    date_bound: bool = False
    note: str = ""


ENDPOINT_SPECS: tuple[EndpointSpec, ...] = (
    EndpointSpec("income", CATEGORY_FINANCIAL, (PARAM_KIND_TS_CODE, PARAM_KIND_STATEMENT_WINDOW)),
    EndpointSpec("balancesheet", CATEGORY_FINANCIAL, (PARAM_KIND_TS_CODE, PARAM_KIND_STATEMENT_WINDOW)),
    EndpointSpec("cashflow", CATEGORY_FINANCIAL, (PARAM_KIND_TS_CODE, PARAM_KIND_STATEMENT_WINDOW)),
    EndpointSpec("fina_indicator", CATEGORY_FINANCIAL, (PARAM_KIND_TS_CODE, PARAM_KIND_STATEMENT_WINDOW)),
    EndpointSpec("forecast", CATEGORY_FINANCIAL, (PARAM_KIND_TS_CODE, PARAM_KIND_STATEMENT_WINDOW)),
    EndpointSpec("express", CATEGORY_FINANCIAL, (PARAM_KIND_TS_CODE, PARAM_KIND_STATEMENT_WINDOW)),
    EndpointSpec("dividend", CATEGORY_FINANCIAL, (PARAM_KIND_TS_CODE,)),
    EndpointSpec("fina_audit", CATEGORY_FINANCIAL, (PARAM_KIND_TS_CODE,)),
    EndpointSpec("fina_mainbz", CATEGORY_FINANCIAL, (PARAM_KIND_TS_CODE,)),
    EndpointSpec("top10_holders", CATEGORY_GOVERNANCE, (PARAM_KIND_TS_CODE,)),
    EndpointSpec("top10_floatholders", CATEGORY_GOVERNANCE, (PARAM_KIND_TS_CODE,)),
    EndpointSpec("pledge_stat", CATEGORY_GOVERNANCE, (PARAM_KIND_TS_CODE,)),
    EndpointSpec("pledge_detail", CATEGORY_GOVERNANCE, (PARAM_KIND_TS_CODE,)),
    EndpointSpec(
        "repurchase",
        CATEGORY_GOVERNANCE,
        (PARAM_KIND_TS_CODE,),
        note="repurchase 单 ts_code 最小查询（001A-R1 修复：原公告日窗口查询命中全市场行数上限，违反单股最小探测约束）",
    ),
    EndpointSpec("share_float", CATEGORY_GOVERNANCE, (PARAM_KIND_TS_CODE,)),
    EndpointSpec("stk_holdernumber", CATEGORY_GOVERNANCE, (PARAM_KIND_TS_CODE,)),
    EndpointSpec("stk_holdertrade", CATEGORY_GOVERNANCE, (PARAM_KIND_TS_CODE,)),
    EndpointSpec("daily", CATEGORY_MARKET, (PARAM_KIND_TS_CODE, PARAM_KIND_RECENT_WINDOW)),
    EndpointSpec("adj_factor", CATEGORY_MARKET, (PARAM_KIND_TS_CODE, PARAM_KIND_RECENT_WINDOW)),
    EndpointSpec("daily_basic", CATEGORY_MARKET, (PARAM_KIND_TS_CODE, PARAM_KIND_RECENT_WINDOW)),
    EndpointSpec("moneyflow", CATEGORY_MARKET, (PARAM_KIND_TS_CODE, PARAM_KIND_RECENT_WINDOW)),
    EndpointSpec(
        "margin",
        CATEGORY_MARKET,
        (PARAM_KIND_EXCHANGE_PROBE_DATE,),
        scope="exchange_aggregate",
        date_bound=True,
        note="margin 为交易所级融资融券汇总接口，不含个股维度；按 ts_code 所属交易所单日探测",
    ),
    EndpointSpec("top_list", CATEGORY_MARKET, (PARAM_KIND_PROBE_DATE_WITH_SYMBOL,), date_bound=True),
    EndpointSpec("block_trade", CATEGORY_MARKET, (PARAM_KIND_TS_CODE,)),
)

EXCLUDED_ENDPOINTS: tuple[dict[str, str], ...] = (
    {
        "endpoint": "vip_*（5000 积分 VIP 全市场批量接口族）",
        "classification": "unknown",
        "extra_paid": "unknown",
        "reason": "官方标注 5000 积分 VIP 权限，未实测，明确排除",
    },
    {
        "endpoint": "top_inst",
        "classification": "unknown",
        "extra_paid": "unknown",
        "reason": "龙虎榜机构明细，任务范围明确排除，未实测",
    },
    {
        "endpoint": "实时行情类接口",
        "classification": "unknown",
        "extra_paid": "likely",
        "reason": "实时行情属额外付费能力，未实测",
    },
    {
        "endpoint": "历史分钟行情类接口",
        "classification": "unknown",
        "extra_paid": "likely",
        "reason": "分钟线属额外付费能力，未实测",
    },
    {
        "endpoint": "新闻类接口",
        "classification": "unknown",
        "extra_paid": "unknown",
        "reason": "任务范围明确排除，未实测",
    },
    {
        "endpoint": "公告全文类接口",
        "classification": "unknown",
        "extra_paid": "likely",
        "reason": "公告全文属额外付费能力，未实测",
    },
    {
        "endpoint": "券商研报类接口",
        "classification": "unknown",
        "extra_paid": "unknown",
        "reason": "任务范围明确排除，未实测",
    },
    {
        "endpoint": "董秘问答类接口",
        "classification": "unknown",
        "extra_paid": "unknown",
        "reason": "任务范围明确排除，未实测",
    },
    {
        "endpoint": "其余未实测接口",
        "classification": "unknown",
        "extra_paid": "unknown",
        "reason": "凡未在本矩阵实测的接口一律保持 unknown，不做积分等级推测",
    },
)


def sanitize_error_text(text: Any, *secrets: str) -> str:
    """Return a credential-free error excerpt safe for committed artifacts."""

    message = str(text or "").strip()
    for secret in secrets:
        cleaned = str(secret or "").strip()
        if cleaned:
            message = message.replace(cleaned, "[REDACTED]")
    message = _CREDENTIAL_KEY_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", message
    )
    message = _OPAQUE_BLOB_RE.sub("[REDACTED]", message)
    return message[:300]


def contains_credential_header_hint(text: Any) -> list[str]:
    """Return credential header names appearing in plain or serialized form.

    001A-R1A fix (final review P1#2): serialized header dicts such as
    ``{"Cookie": "sid"}`` or ``{'Authorization': 'Bearer x'}`` put a quote
    between the header name and the separator, so naive ``"cookie:"``
    substring scans miss them while ``sanitize_error_text`` leaves the
    short value intact.  The optional quote/backslash regex (backslashes
    included so JSON-escaped artifacts like ``{\\"Cookie\\": ...}`` stay
    detectable) closes that fail-closed detection gap; empty result means
    no credential header hint found.
    """

    return sorted(
        {
            match.group(1).lower()
            for match in _CREDENTIAL_HEADER_HINT_RE.finditer(str(text or ""))
        }
    )


def classify_error_message(message: str) -> str:
    text = str(message or "")
    lowered = text.lower()
    if any(marker in text for marker in _RATE_LIMIT_MARKERS) or any(
        marker in lowered for marker in _RATE_LIMIT_MARKERS_EN
    ):
        return STATE_RATE_LIMITED
    if any(marker in text for marker in _PERMISSION_MARKERS) or any(
        marker in lowered for marker in _PERMISSION_MARKERS_EN
    ):
        return STATE_PERMISSION_DENIED
    return STATE_QUERY_FAILED


def is_transient_network_error(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


def classify_exception(exc: BaseException) -> str:
    return classify_error_message(str(exc))


def canonical_frame_payload(frame: pd.DataFrame | None) -> str:
    if frame is None or len(frame) == 0:
        return "[]"
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            try:
                missing = bool(pd.isna(value))
            except (TypeError, ValueError):
                missing = False
            if missing or value is None:
                clean[str(key)] = None
            elif isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                clean[str(key)] = None
            elif isinstance(value, (str, int, bool)):
                clean[str(key)] = value
            else:
                clean[str(key)] = str(value)
        records.append(clean)
    return json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def payload_sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_time_range(frame: pd.DataFrame | None) -> dict[str, str] | None:
    if frame is None or frame.empty:
        return None
    for column in _DATE_RANGE_FIELD_PRIORITY:
        if column not in frame.columns:
            continue
        values = frame[column].astype(str).str.replace(r"\D", "", regex=True)
        values = values[values.str.len().eq(8)]
        if not values.empty:
            return {"field": column, "min": values.min(), "max": values.max()}
    return None


def build_params(spec: EndpointSpec, probe_context: Mapping[str, str]) -> dict[str, str]:
    ts_code = probe_context["ts_code"]
    params: dict[str, str] = {}
    for kind in spec.param_kinds:
        if kind == PARAM_KIND_TS_CODE:
            params["ts_code"] = ts_code
        elif kind == PARAM_KIND_STATEMENT_WINDOW:
            params["start_date"] = probe_context["statement_start"]
            params["end_date"] = probe_context["probe_date"]
        elif kind == PARAM_KIND_RECENT_WINDOW:
            params["start_date"] = probe_context["window_start"]
            params["end_date"] = probe_context["probe_date"]
        elif kind == PARAM_KIND_PROBE_DATE:
            params["trade_date"] = probe_context["probe_date"]
        elif kind == PARAM_KIND_PROBE_DATE_WITH_SYMBOL:
            params["ts_code"] = ts_code
            params["trade_date"] = probe_context["probe_date"]
        elif kind == PARAM_KIND_EXCHANGE_PROBE_DATE:
            params["trade_date"] = probe_context["probe_date"]
            params["exchange"] = probe_context["exchange"]
        else:
            raise ValueError(f"unknown param kind: {kind}")
    return params


def with_fallback_date(params: Mapping[str, str], fallback_date: str) -> dict[str, str]:
    updated = dict(params)
    updated["trade_date"] = fallback_date
    return updated


def permission_status_for(state: str) -> str:
    if state == STATE_PERMISSION_DENIED:
        return PERMISSION_DENIED
    if state in (STATE_HAS_DATA, STATE_NORMAL_NO_DATA, STATE_RATE_LIMITED):
        return PERMISSION_ALLOWED
    return PERMISSION_UNKNOWN


def _empty_vs_error(state: str) -> str:
    if state == STATE_HAS_DATA:
        return "data_returned"
    if state == STATE_NORMAL_NO_DATA:
        return "empty_frame_without_error"
    if state == STATE_QUERY_FAILED:
        return "generic_error_raised"
    if state == STATE_PERMISSION_DENIED:
        return "permission_error_raised"
    if state == STATE_RATE_LIMITED:
        return "rate_limit_error_raised"
    return "not_queried"


def _base_record(
    spec: EndpointSpec,
    params: Mapping[str, str],
    *,
    tested_at: str,
) -> dict[str, Any]:
    return {
        "endpoint": spec.endpoint,
        "category": spec.category,
        "scope": spec.scope,
        "note": spec.note or None,
        "params": dict(params),
        "tested_at": tested_at,
        "attempts": 1,
        "retry_reason": None,
        "first_attempt_state": None,
        "state": None,
        "permission_status": None,
        "extra_paid": False,
        "row_count": None,
        "column_count": None,
        "columns": None,
        "time_range": None,
        "response_sha256": None,
        "response_sha256_scope": None,
        "empty_vs_error": None,
        "rate_limit": {
            "per_minute": None,
            "per_hour": None,
            "verified_in_run": False,
            "note": "单次探测无法验证限频；仅记录探测时是否触发限流错误",
        },
        "latency_ms": None,
        "error": None,
    }


def _apply_frame_result(record: dict[str, Any], frame: pd.DataFrame | None) -> None:
    normalized = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    record["row_count"] = int(len(normalized))
    record["column_count"] = int(normalized.shape[1]) if not normalized.empty else 0
    record["columns"] = [str(column) for column in normalized.columns] if not normalized.empty else []
    record["time_range"] = extract_time_range(normalized)
    payload = canonical_frame_payload(normalized)
    record["response_sha256"] = payload_sha256(payload)
    record["response_sha256_scope"] = "frame"
    record["state"] = STATE_HAS_DATA if len(normalized) else STATE_NORMAL_NO_DATA


def _apply_error_result(
    record: dict[str, Any],
    exc: BaseException,
    *,
    token: str,
) -> None:
    state = classify_exception(exc)
    record["state"] = state
    record["error"] = sanitize_error_text(exc, token)
    record["response_sha256"] = payload_sha256(sanitize_error_text(exc, token))
    record["response_sha256_scope"] = "sanitized_error"
    record["row_count"] = None
    record["column_count"] = None
    record["columns"] = None
    record["time_range"] = None
    if state == STATE_RATE_LIMITED:
        record["rate_limit"]["verified_in_run"] = True


def probe_endpoint(
    query_fn: Callable[..., pd.DataFrame],
    spec: EndpointSpec,
    probe_context: Mapping[str, str],
    *,
    token: str = "",
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Execute at most one probe (plus at most one recorded retry).

    Retry policy: (1) date-bound endpoints that return an empty frame on the
    probe date may retry once on the fallback trading date; (2) a transient
    network error may retry once with identical params.  Permission denial
    and rate limiting must never be retried.
    """

    timestamp = (now or datetime.now)().astimezone().isoformat(timespec="seconds")
    params = build_params(spec, probe_context)
    record = _base_record(spec, params, tested_at=timestamp)
    attempt_params = params
    started = datetime.now()
    try:
        frame = query_fn(spec.endpoint, **attempt_params)
    except Exception as exc:
        _apply_error_result(record, exc, token=token)
        record["first_attempt_state"] = record["state"]
        if record["state"] == STATE_QUERY_FAILED and is_transient_network_error(str(exc)):
            retry_params = attempt_params
            retry_reason = "transient_network_error_single_retry"
            record["attempts"] = 2
            record["retry_reason"] = retry_reason
            try:
                frame = query_fn(spec.endpoint, **retry_params)
            except Exception as retry_exc:
                _apply_error_result(record, retry_exc, token=token)
            else:
                _apply_frame_result(record, frame)
        record["latency_ms"] = int((datetime.now() - started).total_seconds() * 1000)
        record["permission_status"] = permission_status_for(record["state"])
        record["empty_vs_error"] = _empty_vs_error(record["state"])
        return record
    _apply_frame_result(record, frame)
    record["first_attempt_state"] = record["state"]
    if spec.date_bound and record["state"] == STATE_NORMAL_NO_DATA:
        fallback = probe_context.get("fallback_date", "")
        if fallback:
            record["attempts"] = 2
            record["retry_reason"] = "probe_date_no_rows_retry_on_fallback_trading_date"
            try:
                frame = query_fn(
                    spec.endpoint,
                    **with_fallback_date(attempt_params, fallback),
                )
            except Exception as retry_exc:
                _apply_error_result(record, retry_exc, token=token)
            else:
                _apply_frame_result(record, frame)
    record["latency_ms"] = int((datetime.now() - started).total_seconds() * 1000)
    record["permission_status"] = permission_status_for(record["state"])
    record["empty_vs_error"] = _empty_vs_error(record["state"])
    return record


def not_queried_record(
    spec: EndpointSpec,
    probe_context: Mapping[str, str],
    reason: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    timestamp = (now or datetime.now)().astimezone().isoformat(timespec="seconds")
    record = _base_record(spec, build_params(spec, probe_context), tested_at=timestamp)
    record["state"] = STATE_NOT_QUERIED
    record["first_attempt_state"] = STATE_NOT_QUERIED
    record["permission_status"] = PERMISSION_UNKNOWN
    record["empty_vs_error"] = _empty_vs_error(STATE_NOT_QUERIED)
    record["error"] = sanitize_error_text(reason)
    return record


def build_matrix(
    probe_context: Mapping[str, str],
    records: Sequence[Mapping[str, Any]],
    *,
    token_status: str,
    token_source: str = "",
    token_env_override_applied: bool | None = None,
    generated_at: str | None = None,
    excluded: Sequence[Mapping[str, str]] = EXCLUDED_ENDPOINTS,
    state_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    by_state: dict[str, int] = {}
    by_permission: dict[str, int] = {}
    for record in records:
        by_state[record["state"]] = by_state.get(record["state"], 0) + 1
        by_permission[record["permission_status"]] = (
            by_permission.get(record["permission_status"], 0) + 1
        )
    evidence: dict[str, dict[str, Any]] = {
        state: {
            "fixture": None,
            "observed_in_real_run": False,
            "real_run_examples": [],
        }
        for state in (
            STATE_HAS_DATA,
            STATE_NORMAL_NO_DATA,
            STATE_QUERY_FAILED,
            STATE_PERMISSION_DENIED,
            STATE_RATE_LIMITED,
            STATE_NOT_QUERIED,
        )
    }
    for state, info in (state_evidence or {}).items():
        if state in evidence:
            evidence[state].update(info)
    for record in records:
        state = record["state"]
        if state in evidence and not evidence[state]["observed_in_real_run"]:
            evidence[state]["observed_in_real_run"] = True
        if state in evidence and record["endpoint"] not in evidence[state]["real_run_examples"]:
            evidence[state]["real_run_examples"].append(record["endpoint"])
    for state in evidence:
        if not evidence[state]["observed_in_real_run"]:
            evidence[state]["real_run_examples"] = []
    return {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "task_id": TASK_ID,
        "generated_at": generated_at
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        "token_status": token_status,
        "token_source": token_source or None,
        "token_env_override_applied": token_env_override_applied,
        "probe": {
            "symbol": probe_context["ts_code"],
            "exchange": probe_context.get("exchange"),
            "probe_date": probe_context["probe_date"],
            "fallback_date": probe_context.get("fallback_date"),
            "window_start": probe_context.get("window_start"),
            "statement_start": probe_context.get("statement_start"),
            "policy": (
                "每个 endpoint 最多一次正常探测；仅瞬态网络错误或日期型空结果允许一次带原因记录的重试；"
                "权限拒绝与限流绝不重试；探测间 sleep 限速"
            ),
        },
        "summary": {
            "probed_endpoints": len(records),
            "by_state": by_state,
            "by_permission": by_permission,
        },
        "endpoints": [dict(record) for record in records],
        "excluded": [dict(item) for item in excluded],
        "state_evidence": evidence,
        "notes": [
            "矩阵结果只来自本次真实探测，不引用静态 SOURCE_CAPABILITY_MATRIX，也不按积分等级推测",
            "矩阵不包含真实响应正文、Token、Cookie 或 Authorization；错误文本经脱敏清洗",
            "原始响应以结构化元数据（行/列数、列名、日期范围）与 SHA-256 摘要形式留存",
            *notes,
        ],
    }


_CATEGORY_LABELS = {
    CATEGORY_FINANCIAL: "财务",
    CATEGORY_GOVERNANCE: "治理事件",
    CATEGORY_MARKET: "市场行为",
}


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def annotate_row_cap_hits(matrix: Mapping[str, Any], *, cap: int = ROW_CAP_DEFAULT) -> None:
    """Flag any record that reached the upstream row cap.

    Scope-agnostic on purpose (001A-R1): even a symbol-scoped probe can hit
    the cap when upstream ignores filter params, and that must stay visible.
    """

    for record in matrix["endpoints"]:
        if record.get("row_count") is not None and record["row_count"] >= cap:
            record["row_cap_suspected"] = True
    if any(record.get("row_cap_suspected") for record in matrix["endpoints"]):
        notes = matrix["notes"]
        note = _ROW_CAP_NOTE.format(cap=cap)
        if note not in notes:
            notes.append(note)


def render_markdown(matrix: Mapping[str, Any]) -> str:
    probe = matrix["probe"]
    summary = matrix["summary"]
    lines: list[str] = []
    lines.append(f"# Tushare 2000 积分真实权限矩阵（{matrix['task_id']}）")
    lines.append("")
    lines.append(f"- 生成时间：{matrix['generated_at']}")
    lines.append(f"- Token 状态：`{matrix['token_status']}`（不记录 Token 任何明文信息）")
    token_source = matrix.get("token_source")
    if token_source:
        source_label = (
            "git-ignored .env 文件"
            if token_source == TOKEN_SOURCE_DOTENV
            else "继承环境变量"
        )
        source_line = f"- Token 来源：`{token_source}`（{source_label}）"
        if matrix.get("token_env_override_applied"):
            source_line += "；继承环境变量中的 TUSHARE_TOKEN 已被显式 .env 值覆盖，矩阵归属以 .env 为准"
        lines.append(source_line)
    lines.append(
        f"- 探测标的：`{probe['symbol']}`（{probe['exchange']}），"
        f"探测日 `{probe['probe_date']}`，回退日 `{probe['fallback_date']}`"
    )
    lines.append(f"- 探测策略：{probe['policy']}")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 实测 endpoint 数：{summary['probed_endpoints']}")
    for state, count in sorted(summary["by_state"].items()):
        lines.append(f"- `{state}`：{count}")
    for status, count in sorted(summary["by_permission"].items()):
        lines.append(f"- 权限 `{status}`：{count}")
    lines.append("")
    lines.append("## 实测权限矩阵")
    lines.append("")
    lines.append(
        "| endpoint | 类别 | 状态 | 权限 | 行数 | 数据时间范围 | 额外付费 | 重试 | SHA-256(前12位) |"
    )
    lines.append("|---|---|---|---|---:|---|---|---|---|")
    for record in matrix["endpoints"]:
        digest = record.get("response_sha256") or ""
        time_range = record.get("time_range")
        time_range_text = (
            f"{time_range['min']}~{time_range['max']}({time_range['field']})"
            if time_range
            else "-"
        )
        retry = "是" if record.get("attempts", 1) > 1 else "否"
        lines.append(
            "| {endpoint} | {category} | `{state}` | {permission} | {rows} | {time_range} "
            "| {extra_paid} | {retry} | `{digest}` |".format(
                endpoint=_md_escape(record["endpoint"]),
                category=_CATEGORY_LABELS.get(record["category"], record["category"]),
                state=_md_escape(record["state"]),
                permission=_md_escape(record["permission_status"]),
                rows=record.get("row_count") if record.get("row_count") is not None else "-",
                time_range=_md_escape(time_range_text),
                extra_paid="是" if record.get("extra_paid") else "否",
                retry=retry,
                digest=digest[:12] if digest else "-",
            )
        )
    lines.append("")
    lines.append("### 探测参数与说明")
    lines.append("")
    lines.append("| endpoint | 查询参数摘要 | scope | 说明 |")
    lines.append("|---|---|---|---|")
    for record in matrix["endpoints"]:
        params = record.get("params") or {}
        digest = ", ".join(f"{key}={value}" for key, value in sorted(params.items()))
        lines.append(
            f"| {_md_escape(record['endpoint'])} | {_md_escape(digest)} "
            f"| {_md_escape(record.get('scope'))} | {_md_escape(record.get('note'))} |"
        )
    lines.append("")
    lines.append("## 明确排除（未实测，不推测）")
    lines.append("")
    lines.append("| 范围 | 定性 | 是否额外付费 | 原因 |")
    lines.append("|---|---|---|---|")
    for item in matrix["excluded"]:
        lines.append(
            f"| {_md_escape(item['endpoint'])} | `{item['classification']}` "
            f"| {item['extra_paid']} | {_md_escape(item['reason'])} |"
        )
    lines.append("")
    lines.append("## 状态证据")
    lines.append("")
    lines.append("| 状态 | 真实运行观察到 | 真实示例 | fixture 证据 |")
    lines.append("|---|---|---|---|")
    for state, info in matrix["state_evidence"].items():
        observed = "是" if info["observed_in_real_run"] else "否（未验证）"
        examples = ", ".join(info.get("real_run_examples") or []) or "-"
        fixture = info.get("fixture") or "-"
        lines.append(f"| `{state}` | {observed} | {_md_escape(examples)} | {_md_escape(fixture)} |")
    lines.append("")
    lines.append("## 备注")
    lines.append("")
    for note in matrix["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)
