"""Generic portfolio position import service.

Manages imported holdings from any source. Positions are stored as snapshots
in ``ImportedPortfolioPositionDB`` with a configurable ``source`` tag.
No dependency on any specific broker SDK.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from api.database import ImportedPortfolioPositionDB
from api.services import scheduled_service
from tradingagents.agents.utils.context_utils import normalize_user_context


logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")

# [TRACK-009] holdings_import_contract — strong action verbs must NEVER leak
# into imported free-text fields (e.g. stock name). Mirror the controller
# contract so the de-facto list stays in one place.
_STRONG_ACTION_PATTERNS: tuple[str, ...] = (
    "立即买入", "立即卖出", "满仓", "清仓", "全仓",
)

# [TRACK-009] holdings_import_contract — fields the import path tracks.
# Order matters: this is the canonical field list used for diff/reporting.
_TRACKED_FIELDS: tuple[str, ...] = (
    "name",
    "current_position",
    "available_position",
    "average_cost",
    "market_value",
    "current_position_pct",
)

_NUMERIC_FIELDS: frozenset[str] = frozenset(
    f for f in _TRACKED_FIELDS if f != "name"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sync_positions(
    db: Session,
    user_id: str,
    positions: list[dict[str, Any]],
    source: str = "manual",
    auto_apply_scheduled: bool = False,
) -> dict[str, Any]:
    """Replace the position snapshot for *source* with *positions*.

    Each item in *positions* should contain at minimum ``symbol`` (e.g.
    ``"600519.SH"``).  Optional fields: ``name``, ``current_position``,
    ``available_position``, ``average_cost``, ``market_value``,
    ``current_position_pct``.
    """
    if not isinstance(positions, list):
        raise ValueError("positions 必须为列表")

    source = (source or "manual").strip()
    now = datetime.now(timezone.utc)

    # Normalize & deduplicate
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in positions:
        symbol = _normalize_code(raw.get("symbol"))
        if symbol is None:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        cleaned.append({
            "symbol": symbol,
            "name": (raw.get("name") or "").strip() or None,
            "current_position": _to_float(raw.get("current_position")),
            "available_position": _to_float(raw.get("available_position")),
            "average_cost": _to_float(raw.get("average_cost")),
            "market_value": _to_float(raw.get("market_value")),
            "current_position_pct": _to_float(raw.get("current_position_pct")),
        })

    # Do not infer account-level position_pct from imported rows.
    # A screenshot may contain only one visible holding, and using that subset as
    # the denominator would incorrectly turn it into "100% full position".
    # Only persist current_position_pct when the user/import source explicitly
    # provides an account-level percentage.

    if not cleaned:
        raise ValueError("没有有效的持仓记录，请检查输入格式")

    # Replace snapshot for this source
    db.query(ImportedPortfolioPositionDB).filter(
        ImportedPortfolioPositionDB.user_id == user_id,
        ImportedPortfolioPositionDB.source == source,
    ).delete()

    for p in cleaned:
        db.add(ImportedPortfolioPositionDB(
            id=uuid4().hex,
            user_id=user_id,
            source=source,
            symbol=p["symbol"],
            security_name=p["name"],
            current_position=p["current_position"],
            available_position=p["available_position"],
            average_cost=p["average_cost"],
            market_value=p["market_value"],
            current_position_pct=p["current_position_pct"],
            trade_points_json=[],
            trade_points_count=0,
            latest_trade_at=None,
            latest_trade_action=None,
            last_imported_at=now,
        ))

    scheduled_sync: dict[str, list] = {"created": [], "existing": [], "skipped_limit": []}
    if auto_apply_scheduled:
        ordered = [p["symbol"] for p in cleaned if (p["current_position"] or 0) > 0]
        scheduled_sync = scheduled_service.ensure_scheduled_for_symbols(
            db=db,
            user_id=user_id,
            symbols=ordered,
        )

    db.commit()
    return get_import_state(db, user_id, scheduled_sync=scheduled_sync)


def get_import_state(
    db: Session,
    user_id: str,
    scheduled_sync: dict[str, Any] | None = None,
) -> dict[str, Any]:
    positions = list_imported_positions(db, user_id)
    return {
        "auto_apply_scheduled": False,
        "last_synced_at": _latest_imported_at(positions),
        "last_error": None,
        "summary": {"positions": len(positions)},
        "scheduled_sync": scheduled_sync or {"created": [], "existing": [], "skipped_limit": []},
        "positions": positions,
    }


def list_imported_positions(db: Session, user_id: str) -> list[dict[str, Any]]:
    """List all imported positions for a user, regardless of source."""
    rows = (
        db.query(ImportedPortfolioPositionDB)
        .filter(ImportedPortfolioPositionDB.user_id == user_id)
        .order_by(
            ImportedPortfolioPositionDB.market_value.desc(),
            ImportedPortfolioPositionDB.current_position.desc(),
            ImportedPortfolioPositionDB.symbol,
        )
        .all()
    )
    return [
        {
            "symbol": row.symbol,
            "name": row.security_name or row.symbol,
            "source": row.source,
            "current_position": row.current_position,
            "available_position": row.available_position,
            "average_cost": row.average_cost,
            "market_value": row.market_value,
            "current_position_pct": row.current_position_pct,
            "trade_points_count": row.trade_points_count or 0,
            "last_imported_at": row.last_imported_at.isoformat() if row.last_imported_at else None,
        }
        for row in rows
    ]


def build_scheduled_user_context(db: Session, user_id: str, symbol: str) -> dict[str, Any]:
    """Build user context for a scheduled analysis from any imported source."""
    row = (
        db.query(ImportedPortfolioPositionDB)
        .filter(
            ImportedPortfolioPositionDB.user_id == user_id,
            ImportedPortfolioPositionDB.symbol == (symbol or "").strip().upper(),
        )
        .first()
    )
    if not row:
        return {}

    payload: dict[str, Any] = {
        "objective": "持有处理" if (row.current_position or 0) > 0 else "观察",
        "current_position": row.current_position,
        "current_position_pct": row.current_position_pct,
        "average_cost": row.average_cost,
        "user_notes": f"来源：持仓导入（{row.source}）",
    }
    if row.current_position_pct is None:
        payload["user_notes"] += "；未提供现金、总资产或账户仓位占比，仅代表已导入的可见持仓，禁止据此推断满仓。"
    return normalize_user_context(payload)


def clear_imported_portfolio(db: Session, user_id: str) -> None:
    """Clear all imported positions for a user, regardless of source."""
    db.query(ImportedPortfolioPositionDB).filter(
        ImportedPortfolioPositionDB.user_id == user_id,
    ).delete()
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_code(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if _CODE_RE.match(text):
        return text
    if re.match(r"^\d{6}$", text):
        if text.startswith("6"):
            return f"{text}.SH"
        if text.startswith(("0", "3")):
            return f"{text}.SZ"
        if text.startswith(("4", "8")):
            return f"{text}.BJ"
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _latest_imported_at(positions: list[dict[str, Any]]) -> str | None:
    dates = [p["last_imported_at"] for p in positions if p.get("last_imported_at")]
    return max(dates) if dates else None


# ===========================================================================
# [TRACK-009] holdings_import_contract
# Holdings import dry-run / validate / contract helpers.
#
# Design notes (per AGENTS.md adversarial-review mindset):
#   * dry_run_import MUST NOT touch the DB. It only queries current rows
#     for the (user_id, source) pair and computes a diff.
#   * import_positions_from_text MUST route the actual write through the
#     existing sync_positions() — no side-channel DB writes.
#   * If every parsed row is invalid, raise instead of committing an empty
#     snapshot that would silently erase the user's holdings.
#   * ImportedPortfolioPositionDB has no `notes` column, so watchlist /
#     observation warehouse notes are structurally isolated from this path.
# ===========================================================================


# Header alias map for CSV/TSV parsing. Keys are normalised lower-case.
_HEADER_ALIASES: dict[str, str] = {
    # symbol
    "symbol": "symbol",
    "code": "symbol",
    "代码": "symbol",
    "股票代码": "symbol",
    "证券代码": "symbol",
    # name
    "name": "name",
    "名称": "name",
    "股票名称": "name",
    "证券名称": "name",
    # current_position
    "current_position": "current_position",
    "shares": "current_position",
    "position": "current_position",
    "持仓": "current_position",
    "持仓数": "current_position",
    "持仓数量": "current_position",
    "数量": "current_position",
    # available_position
    "available_position": "available_position",
    "可用": "available_position",
    "可用数量": "available_position",
    # average_cost
    "average_cost": "average_cost",
    "avg_cost": "average_cost",
    "cost": "average_cost",
    "成本价": "average_cost",
    "成本": "average_cost",
    "买入均价": "average_cost",
    # market_value
    "market_value": "market_value",
    "value": "market_value",
    "市值": "market_value",
    "持仓市值": "market_value",
    # current_position_pct
    "current_position_pct": "current_position_pct",
    "position_pct": "current_position_pct",
    "仓位": "current_position_pct",
    "仓位占比": "current_position_pct",
    "占比": "current_position_pct",
}

# Aliases accepted in JSON objects in addition to canonical field names.
_JSON_FIELD_ALIASES: dict[str, str] = {
    "shares": "current_position",
    "position": "current_position",
    "qty": "current_position",
    "quantity": "current_position",
    "cost": "average_cost",
    "avg_cost": "average_cost",
    "price": "average_cost",
    "value": "market_value",
    "pct": "current_position_pct",
}


def parse_positions_text(text: str) -> list[dict[str, Any]]:
    """Parse JSON / CSV / TSV / whitespace holdings text into raw row dicts.

    The returned rows have NOT been validated or normalised yet — they may
    contain alias keys (e.g. ``shares``) and unparseable numbers. Use
    :func:`validate_positions` next.

    Accepted formats (auto-detected):
      * JSON array of objects, single object, or ``{"positions": [...]}``.
      * CSV / TSV with a header row (Chinese or English column names).
      * Whitespace-separated lines: ``代码 [名称] [持仓数] [成本价] [市值]``.

    Raises ``ValueError`` with a clear message on unparseable JSON.
    """
    if not isinstance(text, str):
        raise ValueError("导入文本必须为字符串")
    raw = text.lstrip("\ufeff").strip()
    if not raw:
        return []

    # 1. JSON?
    if raw[0] in "{[":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败：{exc.msg}") from exc
        return _normalise_json_payload(data)

    # 2. CSV / TSV / whitespace (line-based)
    return _parse_tabular_text(raw)


def _normalise_json_payload(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        items: list[dict[str, Any]] = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            items.append(_map_json_aliases(raw))
        return items
    if isinstance(data, dict):
        # envelope {"positions": [...]} / {"holdings": [...]}
        for key in ("positions", "holdings", "data"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [
                    _map_json_aliases(r) for r in inner if isinstance(r, dict)
                ]
        # single position object
        if "symbol" in data or "code" in data or "代码" in data:
            return [_map_json_aliases(data)]
    return []


def _map_json_aliases(row: dict[str, Any]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for k, v in row.items():
        key = str(k).strip().lower()
        canonical = _JSON_FIELD_ALIASES.get(key, key)
        # Always store under canonical field name; keep both for back-compat
        # so callers that look up by alias still find the value.
        mapped[canonical] = v
        if canonical != key and key not in mapped:
            mapped[key] = v
    return mapped


def _parse_tabular_text(raw: str) -> list[dict[str, Any]]:
    # Detect delimiter by looking at the first non-comment line.
    first_line = ""
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        first_line = s
        break
    if not first_line:
        return []

    if "\t" in first_line:
        delimiter = "\t"
    elif "," in first_line:
        delimiter = ","
    elif ";" in first_line:
        delimiter = ";"
    else:
        delimiter = None  # whitespace

    lines = [ln for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return []

    if delimiter is None:
        return _parse_whitespace_lines(lines)

    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return []

    # Detect header: if any header cell maps to a known alias AND no header
    # cell looks like a 6-digit code, treat as header.
    header_cells = [c.strip() for c in rows[0]]
    header_mapped = [
        _HEADER_ALIASES.get(c.lower(), _HEADER_ALIASES.get(c, "")) for c in header_cells
    ]
    looks_like_header = any(h for h in header_mapped) and not _looks_like_code(header_cells[0])

    if looks_like_header:
        cols = header_mapped
        data_rows = rows[1:]
    else:
        # Positional fallback for headerless CSV/TSV:
        # col0=symbol col1=name col2=current_position col3=average_cost col4=market_value col5=current_position_pct
        cols = [
            "symbol",
            "name",
            "current_position",
            "average_cost",
            "market_value",
            "current_position_pct",
        ]
        data_rows = rows

    out: list[dict[str, Any]] = []
    for r in data_rows:
        if not any(cell.strip() for cell in r):
            continue
        item: dict[str, Any] = {}
        for idx, cell in enumerate(r):
            if idx >= len(cols):
                break
            col = cols[idx]
            if not col:
                continue
            val = cell.strip()
            if val == "":
                continue
            item[col] = val
        if item:
            out.append(item)
    return out


def _parse_whitespace_lines(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        item: dict[str, Any] = {"symbol": parts[0]}
        rest = parts[1:]
        # If second token is non-numeric → name, otherwise everything is numeric.
        if rest and not _is_numeric_token(rest[0]):
            item["name"] = rest[0]
            rest = rest[1:]
        numeric = [p for p in rest if _is_numeric_token(p)]
        positional = ("current_position", "average_cost", "market_value", "current_position_pct")
        for idx, val in enumerate(numeric):
            if idx >= len(positional):
                break
            item[positional[idx]] = val
        out.append(item)
    return out


def _is_numeric_token(tok: str) -> bool:
    if not tok:
        return False
    try:
        float(tok)
        return True
    except ValueError:
        return False


def _looks_like_code(token: str) -> bool:
    t = (token or "").strip().upper()
    return bool(re.match(r"^\d{6}(\.(SH|SZ|BJ))?$", t))


def _scrub_strong_actions(value: str | None) -> str | None:
    if not value:
        return value
    text = str(value)
    for verb in _STRONG_ACTION_PATTERNS:
        text = text.replace(verb, "")
    cleaned = text.strip()
    return cleaned or None


def validate_positions(positions: Any) -> dict[str, Any]:
    """Validate and normalise a list of raw position dicts.

    Returns a dict with:
      * ``valid``      — list of cleaned/normalised position dicts.
      * ``invalid``    — list of ``{symbol?, reason, fields}`` dicts.
      * ``warnings``   — list of ``{symbol, reason, field?}`` dicts.
      * ``valid_count``/``invalid_count`` — integer counters.

    Raises ``ValueError`` if *positions* is not a list.
    """
    if not isinstance(positions, list):
        raise ValueError("positions 必须为列表")

    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()

    for raw in positions:
        if not isinstance(raw, dict):
            invalid.append({"reason": "not_a_dict", "fields": [], "raw": raw})
            continue

        symbol = _normalize_code(raw.get("symbol") or raw.get("code"))
        if symbol is None:
            invalid.append({
                "reason": "invalid_symbol",
                "fields": ["symbol"],
                "raw_symbol": str(raw.get("symbol", "")),
            })
            continue

        if symbol in seen_symbols:
            invalid.append({
                "reason": "duplicate_symbol",
                "fields": ["symbol"],
                "symbol": symbol,
            })
            continue

        item: dict[str, Any] = {"symbol": symbol}

        # Name (optional but warned)
        name_raw = raw.get("name")
        name_val: str | None = None
        if name_raw is not None:
            name_val = _scrub_strong_actions(str(name_raw).strip()) or None
        item["name"] = name_val
        if name_val is None:
            warnings.append({"symbol": symbol, "reason": "missing_name", "field": "name"})

        # Numeric fields
        negative_fields: list[str] = []
        for field in _NUMERIC_FIELDS:
            value = raw.get(field)
            if value is None or value == "":
                item[field] = None
                continue
            if isinstance(value, str) and not _is_numeric_token(value.strip()):
                warnings.append({
                    "symbol": symbol,
                    "reason": f"unparseable_{field}",
                    "field": field,
                })
                item[field] = None
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                warnings.append({
                    "symbol": symbol,
                    "reason": f"unparseable_{field}",
                    "field": field,
                })
                item[field] = None
                continue
            if num < 0:
                negative_fields.append(field)
                item[field] = num
            else:
                item[field] = num

        if negative_fields:
            invalid.append({
                "reason": "negative_field",
                "fields": negative_fields,
                "symbol": symbol,
            })
            continue

        seen_symbols.add(symbol)
        valid.append(item)

    return {
        "valid": valid,
        "invalid": invalid,
        "warnings": warnings,
        "valid_count": len(valid),
        "invalid_count": len(invalid),
    }


def _row_to_snapshot(row: ImportedPortfolioPositionDB) -> dict[str, Any]:
    return {
        "symbol": row.symbol,
        "name": row.security_name,
        "current_position": _to_float(row.current_position),
        "available_position": _to_float(row.available_position),
        "average_cost": _to_float(row.average_cost),
        "market_value": _to_float(row.market_value),
        "current_position_pct": _to_float(row.current_position_pct),
    }


def _diff_row(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    delta: dict[str, dict[str, Any]] = {}
    for field in _TRACKED_FIELDS:
        b = before.get(field)
        a = after.get(field)
        # Normalise None vs missing so a wiped field shows up as a change.
        b_norm = b if isinstance(b, (int, float)) or b is None else b
        a_norm = a if isinstance(a, (int, float)) or a is None else a
        if _values_differ(b_norm, a_norm):
            delta[field] = {"before": b, "after": a}
    return delta


def _values_differ(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) > 1e-9
    return str(a) != str(b)


def dry_run_import(
    db: Session,
    user_id: str,
    positions: list[dict[str, Any]],
    source: str = "manual",
) -> dict[str, Any]:
    """Compute the diff of importing *positions* WITHOUT touching the DB.

    The diff is computed against the current snapshot for the
    ``(user_id, source)`` pair, mirroring the snapshot-replace semantics
    of :func:`sync_positions`.
    """
    source = (source or "manual").strip()
    validation = validate_positions(positions)
    valid_rows = validation["valid"]

    existing_rows = (
        db.query(ImportedPortfolioPositionDB)
        .filter(
            ImportedPortfolioPositionDB.user_id == user_id,
            ImportedPortfolioPositionDB.source == source,
        )
        .all()
    )
    existing_by_symbol: dict[str, dict[str, Any]] = {
        r.symbol: _row_to_snapshot(r) for r in existing_rows
    }

    incoming_by_symbol: dict[str, dict[str, Any]] = {r["symbol"]: r for r in valid_rows}

    added: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for symbol, row in incoming_by_symbol.items():
        if symbol not in existing_by_symbol:
            added.append({"symbol": symbol, "incoming": row})
        else:
            before = existing_by_symbol[symbol]
            delta = _diff_row(before, row)
            if delta:
                updated.append({"symbol": symbol, "delta": delta, "before": before, "after": row})
            else:
                unchanged.append({"symbol": symbol, "incoming": row})

    for symbol, before in existing_by_symbol.items():
        if symbol not in incoming_by_symbol:
            removed.append({"symbol": symbol, "before": before})

    return {
        "dry_run": True,
        "source": source,
        "write_semantics": "replace_snapshot",
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "errors": validation["invalid"],
        "warnings": validation["warnings"],
        "added_count": len(added),
        "updated_count": len(updated),
        "unchanged_count": len(unchanged),
        "removed_count": len(removed),
        "error_count": len(validation["invalid"]),
        "valid_count": validation["valid_count"],
        "invalid_count": validation["invalid_count"],
    }


def import_positions_from_text(
    db: Session,
    user_id: str,
    text: str,
    source: str = "manual",
    auto_apply_scheduled: bool = False,
) -> dict[str, Any]:
    """Parse → validate → dry-run → commit through :func:`sync_positions`.

    Returns ``{"dry_run": <diff>, "state": <import state>}``.

    Raises ``ValueError`` when:
      * *text* is empty / whitespace.
      * JSON parsing fails.
      * After validation there are zero valid rows (so we never commit an
        empty snapshot that would erase the user's holdings).
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("导入文本为空")
    source = (source or "manual").strip()

    raw_rows = parse_positions_text(text)
    validation = validate_positions(raw_rows)
    if validation["valid_count"] == 0:
        raise ValueError("全部无效：未识别到任何合法持仓，已拒绝提交以保护现有数据")

    # Build the dry-run preview from valid rows only.
    diff = dry_run_import(db, user_id, validation["valid"], source=source)

    # Commit through the canonical write path. sync_positions re-validates
    # and normalises; passing the already-validated dicts keeps behaviour
    # consistent with the dry-run preview.
    state = sync_positions(
        db=db,
        user_id=user_id,
        positions=validation["valid"],
        source=source,
        auto_apply_scheduled=auto_apply_scheduled,
    )
    return {"dry_run": diff, "state": state}


OPENCLAW_HOLDINGS_CONTRACT: dict[str, Any] = {
    "schema_version": "1.0",
    "contract_for": "openclaw_to_ta_holdings",
    "description": (
        "OpenClaw / 任意外部持仓来源与 TA 跟踪看板之间的持仓快照契约。"
        "通过 dry-run 预览差异，再走 /v1/portfolio/imports 提交。"
    ),
    "write_endpoints": [
        {
            "path": "/v1/portfolio/imports/dry-run",
            "method": "POST",
            "tier": "FAST_RADAR",
            "purpose": "dry-run 预览新增/更新/移除/异常，不写库",
            "accepts": "text(JSON/CSV/TSV/whitespace) 或 positions(list)",
        },
        {
            "path": "/v1/portfolio/imports/import-text",
            "method": "POST",
            "tier": "FAST_RADAR",
            "purpose": "从文本解析并提交，返回 dry-run 预览 + 提交后状态",
            "accepts": "text(JSON/CSV/TSV/whitespace)",
        },
        {
            "path": "/v1/portfolio/imports",
            "method": "POST",
            "tier": "FAST_RADAR",
            "purpose": "结构化 positions 列表直接提交（兼容旧入口）",
            "accepts": "positions(list) + source + auto_apply_scheduled",
        },
        {
            "path": "/v1/portfolio/imports",
            "method": "DELETE",
            "tier": "FAST_RADAR",
            "purpose": "清空当前用户的所有导入持仓",
        },
    ],
    "read_endpoints": [
        {
            "path": "/v1/portfolio/imports",
            "method": "GET",
            "purpose": "读取当前导入状态与持仓列表",
        },
        {
            "path": "/v1/dashboard/tracking-board/v2",
            "method": "GET",
            "purpose": "跟踪看板 v2 聚合视图（持仓/观察仓/今日指引/盘后复盘）",
        },
    ],
    "field_semantics": {
        "symbol": (
            "A 股代码，6 位数字自动加 .SH/.SZ/.BJ 后缀；"
            "已带后缀的 600519.SH / 000001.SZ / 688981.SH 等保持原样"
        ),
        "name": "可选；缺失时仅产生 warning 不阻断导入；不会从代码反查名称",
        "current_position": "持仓数量（整数或浮点），别名 shares / position / qty 也接受",
        "available_position": "可用数量，别名 available 也接受",
        "average_cost": "成本价，别名 cost / avg_cost 也接受",
        "market_value": "市值，别名 value 也接受",
        "current_position_pct": (
            "账户级仓位占比 %。禁止从单一可见行推断；"
            "未显式提供时不会回填为 100%"
        ),
        "source": (
            "持仓来源标签，例如 openclaw / manual / tracking_board_v2 / "
            "broker_screenshot。同一 (user, source) 下的快照整体替换。"
        ),
    },
    "write_semantics": (
        "replace_snapshot —— 提交后该 (user_id, source) 下的旧记录会被本次"
        "快照整体替换；不在快照中的 symbol 会被移除。重复提交相同内容幂等，"
        "不会清空数据。"
    ),
    "invariants": [
        "dry-run 接口绝不写库。",
        "全部行无效时拒绝提交，已有数据保持不变。",
        "ImportedPortfolioPositionDB 不存 notes 字段；watchlist 与 observation "
        "warehouse 的 notes 不受此导入接口影响。",
        "导入路径不调用 LLM，不读取 API key。",
        "free-text 字段（name）会被剥除强动作词。",
    ],
    "text_formats": {
        "json": "数组 / 单对象 / {\"positions\": [...]}",
        "csv":  "首行表头，支持中文（代码,名称,持仓数,成本价,市值）或英文（symbol,name,shares,cost,value）",
        "tsv":  "Tab 分隔，列含义同 CSV",
        "whitespace": "代码 [名称] [持仓数] [成本价] [市值] —— 第二段非数字视为名称",
    },
}
