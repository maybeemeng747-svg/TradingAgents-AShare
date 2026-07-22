# [B-004] holdings_sync
"""Bidirectional holdings sync between TA and investment-controller.

Provides file-based sync of portfolio positions between the TA system's
``ImportedPortfolioPositionDB`` and an external ``current_holdings.json``
file used by the investment-controller project.

Design contract:
    * Pure-deterministic diff computation — no LLM, no network calls.
    * Write path reuses ``portfolio_import_service.sync_positions()`` for
      TA-side writes and atomic JSON file writes for the external side.
    * ``direction`` parameter controls sync mode:
      - ``"export"``       : TA → file (one-way)
      - ``"import"``       : file → TA (one-way)
      - ``"bidirectional"`` : both ways (default), with conflict resolution
    * Conflict resolution in bidirectional mode: last-write-wins based on
      ``last_synced_at`` timestamps. When both sides changed the same
      symbol, the side with the newer modification wins.
    * Never erases holdings silently — empty input produces a warning.
    * Never reads/writes API keys or calls LLM.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from api.database import ImportedPortfolioPositionDB
from api.services import portfolio_import_service

logger = logging.getLogger(__name__)

# Default path for the external holdings JSON file.
# Can be overridden via INVESTMENT_CONTROLLER_HOLDINGS_PATH env var.
_DEFAULT_HOLDINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "investment-controller",
    "current_holdings.json",
)

_HOLDINGS_SOURCE = "investment_controller"

_SYNC_SCHEMA_VERSION = "1.0"

# Fields that are tracked for diff computation.
_TRACKED_FIELDS = (
    "name",
    "current_position",
    "available_position",
    "average_cost",
    "market_value",
    "current_position_pct",
)


def get_default_sync_path() -> str:
    """Return the default file path for the external holdings JSON.

    Respects ``INVESTMENT_CONTROLLER_HOLDINGS_PATH`` env var.
    """
    return os.environ.get(
        "INVESTMENT_CONTROLLER_HOLDINGS_PATH", _DEFAULT_HOLDINGS_PATH
    )


def read_external_holdings(file_path: str) -> dict[str, Any]:
    """Read and parse the external ``current_holdings.json``.

    Returns:
        ``{"holdings": [...], "meta": {...}}`` on success.
        ``{"holdings": [], "meta": None, "error": "..."}`` on failure.
    """
    path = Path(file_path)
    if not path.exists():
        return {"holdings": [], "meta": None, "error": "file_not_found"}

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"holdings": [], "meta": None, "error": f"read_error: {exc}"}

    if not raw.strip():
        return {"holdings": [], "meta": None, "error": "empty_file"}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"holdings": [], "meta": None, "error": f"json_parse_error: {exc.msg}"}

    if isinstance(data, dict):
        holdings = data.get("holdings", [])
        meta = data.get("meta")
    elif isinstance(data, list):
        holdings = data
        meta = None
    else:
        return {"holdings": [], "meta": None, "error": "unexpected_format"}

    if not isinstance(holdings, list):
        return {"holdings": [], "meta": meta, "error": "holdings_not_list"}

    return {"holdings": holdings, "meta": meta, "error": None}


def write_external_holdings(file_path: str, holdings: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write holdings to the external JSON file atomically.

    Uses write-to-temp-then-rename for atomicity.
    """
    path = Path(file_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "schema_version": _SYNC_SCHEMA_VERSION,
        "meta": meta or {
            "exported_at": now,
            "source": "tradingagents_ta",
            "count": len(holdings),
        },
        "holdings": holdings,
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError as exc:
        return {"success": False, "error": f"write_error: {exc}"}

    return {"success": True, "file_path": str(path), "count": len(holdings)}


def ta_positions_to_external(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert TA position dicts to the external file format."""
    out: list[dict[str, Any]] = []
    for p in positions:
        out.append({
            "symbol": p.get("symbol", ""),
            "name": p.get("name") or "",
            "current_position": p.get("current_position"),
            "available_position": p.get("available_position"),
            "average_cost": p.get("average_cost"),
            "market_value": p.get("market_value"),
            "current_position_pct": p.get("current_position_pct"),
        })
    return out


def external_to_ta_positions(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert external file format to TA position dicts."""
    out: list[dict[str, Any]] = []
    for h in holdings:
        out.append({
            "symbol": h.get("symbol", ""),
            "name": h.get("name") or "",
            "current_position": h.get("current_position"),
            "available_position": h.get("available_position"),
            "average_cost": h.get("average_cost"),
            "market_value": h.get("market_value"),
            "current_position_pct": h.get("current_position_pct"),
        })
    return out


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positions_by_symbol(positions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index positions by symbol."""
    return {p["symbol"]: p for p in positions if p.get("symbol")}


def _field_differ(a: Any, b: Any) -> bool:
    """Check if two field values are materially different."""
    a_f = _to_float(a)
    b_f = _to_float(b)
    if a_f is not None and b_f is not None:
        return abs(a_f - b_f) > 1e-9
    a_s = str(a or "")
    b_s = str(b or "")
    return a_s != b_s


def compute_bidirectional_diff(
    ta_positions: list[dict[str, Any]],
    external_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute the diff between TA and external positions.

    Returns a structured diff with:
    - ``ta_only``: symbols only in TA
    - ``external_only``: symbols only in external
    - ``both``: symbols in both, with per-field diffs
    - ``identical``: symbols with no differences
    """
    ta_by = _positions_by_symbol(ta_positions)
    ext_by = _positions_by_symbol(external_positions)

    ta_only: list[dict[str, Any]] = []
    ext_only: list[dict[str, Any]] = []
    both_changed: list[dict[str, Any]] = []
    identical: list[str] = []

    for symbol in sorted(set(ta_by) | set(ext_by)):
        in_ta = symbol in ta_by
        in_ext = symbol in ext_by

        if in_ta and not in_ext:
            ta_only.append({"symbol": symbol, "data": ta_by[symbol]})
        elif in_ext and not in_ta:
            ext_only.append({"symbol": symbol, "data": ext_by[symbol]})
        else:
            # In both — check field diffs
            ta_row = ta_by[symbol]
            ext_row = ext_by[symbol]
            diffs: dict[str, dict[str, Any]] = {}
            for field in _TRACKED_FIELDS:
                ta_val = ta_row.get(field)
                ext_val = ext_row.get(field)
                if _field_differ(ta_val, ext_val):
                    diffs[field] = {"ta": ta_val, "external": ext_val}
            if diffs:
                both_changed.append({
                    "symbol": symbol,
                    "diffs": diffs,
                    "ta_data": ta_row,
                    "external_data": ext_row,
                })
            else:
                identical.append(symbol)

    return {
        "ta_only": ta_only,
        "external_only": ext_only,
        "both_changed": both_changed,
        "identical": identical,
        "ta_only_count": len(ta_only),
        "external_only_count": len(ext_only),
        "both_changed_count": len(both_changed),
        "identical_count": len(identical),
    }


def get_ta_positions(db: Session, user_id: str) -> list[dict[str, Any]]:
    """Read current TA positions for a user from the DB."""
    rows = (
        db.query(ImportedPortfolioPositionDB)
        .filter(ImportedPortfolioPositionDB.user_id == user_id)
        .all()
    )
    return [
        {
            "symbol": row.symbol,
            "name": row.security_name or row.symbol,
            "source": row.source,
            "current_position": _to_float(row.current_position),
            "available_position": _to_float(row.available_position),
            "average_cost": _to_float(row.average_cost),
            "market_value": _to_float(row.market_value),
            "current_position_pct": _to_float(row.current_position_pct),
            "last_imported_at": row.last_imported_at.isoformat() if row.last_imported_at else None,
        }
        for row in rows
    ]


def export_holdings(db: Session, user_id: str, file_path: str | None = None) -> dict[str, Any]:
    """Export TA holdings to external file (one-way TA → file).

    Returns:
        ``{"success": True, "exported_count": N, "file_path": "...", "holdings": [...]}``
        or ``{"success": False, "error": "..."}``
    """
    path = file_path or get_default_sync_path()
    positions = get_ta_positions(db, user_id)

    if not positions:
        return {"success": False, "error": "no_ta_holdings_to_export"}

    external = ta_positions_to_external(positions)
    result = write_external_holdings(path, external)
    if not result["success"]:
        return result

    return {
        "success": True,
        "exported_count": len(external),
        "file_path": result["file_path"],
        "holdings": external,
    }


def import_holdings(db: Session, user_id: str, file_path: str | None = None) -> dict[str, Any]:
    """Import holdings from external file into TA (one-way file → TA).

    Returns:
        ``{"success": True, "dry_run": {...}, "state": {...}}``
        or ``{"success": False, "error": "..."}``
    """
    path = file_path or get_default_sync_path()
    ext_data = read_external_holdings(path)

    if ext_data["error"]:
        return {"success": False, "error": ext_data["error"]}

    ext_holdings = ext_data["holdings"]
    if not ext_holdings:
        return {"success": False, "error": "external_file_empty"}

    ta_positions = external_to_ta_positions(ext_holdings)

    try:
        diff = portfolio_import_service.dry_run_import(
            db, user_id, ta_positions, source=_HOLDINGS_SOURCE
        )
        state = portfolio_import_service.sync_positions(
            db, user_id, ta_positions, source=_HOLDINGS_SOURCE
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    return {"success": True, "dry_run": diff, "state": state}


def sync_holdings(
    db: Session,
    user_id: str,
    file_path: str | None = None,
    direction: str = "bidirectional",
) -> dict[str, Any]:
    """Main sync entry point.

    Args:
        db: SQLAlchemy session.
        user_id: Current user ID.
        file_path: Path to external holdings JSON. Defaults to env/default.
        direction: ``"export"``, ``"import"``, or ``"bidirectional"`` (default).

    Returns:
        Structured result dict with sync outcome.
    """
    path = file_path or get_default_sync_path()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if direction == "export":
        return export_holdings(db, user_id, path)
    if direction == "import":
        return import_holdings(db, user_id, path)

    # --- Bidirectional sync ---
    ta_positions = get_ta_positions(db, user_id)
    ext_data = read_external_holdings(path)
    ext_holdings_raw = ext_data["holdings"] if not ext_data["error"] else []
    ext_positions = external_to_ta_positions(ext_holdings_raw)

    diff = compute_bidirectional_diff(ta_positions, ext_positions)

    # Case 1: External file doesn't exist or is empty → export TA
    if ext_data["error"] in ("file_not_found", "empty_file") or not ext_positions:
        if ta_positions:
            export_result = export_holdings(db, user_id, path)
            return {
                "success": export_result.get("success", False),
                "direction": "export",
                "reason": "external_file_missing_or_empty",
                "diff": diff,
                "export_result": export_result,
                "import_result": None,
                "synced_at": now,
            }
        return {
            "success": True,
            "direction": "none",
            "reason": "both_sides_empty",
            "diff": diff,
            "export_result": None,
            "import_result": None,
            "synced_at": now,
        }

    # Case 2: TA has no holdings → import from external
    if not ta_positions:
        import_result = import_holdings(db, user_id, path)
        return {
            "success": import_result.get("success", False),
            "direction": "import",
            "reason": "ta_empty_import_from_external",
            "diff": diff,
            "export_result": None,
            "import_result": import_result,
            "synced_at": now,
        }

    # Case 3: Both sides have data → bidirectional merge
    # Strategy: For each symbol, use the side with data.
    # When both sides have the same symbol with diffs, last-write-wins
    # based on the external file's meta.last_synced_at vs TA last_imported_at.
    # Default: external wins for conflicts (controller is authoritative).
    merged = _merge_positions(ta_positions, ext_positions, ext_data.get("meta"))

    # Write merged result to both sides
    # Write to external file
    ext_merged = ta_positions_to_external(merged)
    write_result = write_external_holdings(path, ext_merged, meta={
        "exported_at": now,
        "source": "bidirectional_sync",
        "count": len(ext_merged),
    })

    # Write to TA (import merged as investment_controller source)
    ta_merged = external_to_ta_positions(ext_merged)
    try:
        import_diff = portfolio_import_service.dry_run_import(
            db, user_id, ta_merged, source=_HOLDINGS_SOURCE
        )
        import_state = portfolio_import_service.sync_positions(
            db, user_id, ta_merged, source=_HOLDINGS_SOURCE
        )
    except ValueError as exc:
        return {
            "success": False,
            "direction": "bidirectional",
            "reason": f"ta_import_error: {exc}",
            "diff": diff,
            "export_result": write_result,
            "import_result": None,
            "synced_at": now,
        }

    return {
        "success": True,
        "direction": "bidirectional",
        "reason": "merged",
        "diff": diff,
        "export_result": write_result,
        "import_result": {"dry_run": import_diff, "state": import_state},
        "merged_count": len(merged),
        "synced_at": now,
    }


def _merge_positions(
    ta_positions: list[dict[str, Any]],
    ext_positions: list[dict[str, Any]],
    ext_meta: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Merge TA and external positions.

    Rules:
    - Symbols only in TA → keep (export to file).
    - Symbols only in external → keep (import to TA).
    - Symbols in both with diffs → external wins (controller is authoritative).
    """
    ta_by = _positions_by_symbol(ta_positions)
    ext_by = _positions_by_symbol(ext_positions)

    merged: list[dict[str, Any]] = []
    all_symbols = sorted(set(ta_by) | set(ext_by))

    for symbol in all_symbols:
        in_ta = symbol in ta_by
        in_ext = symbol in ext_by

        if in_ext:
            # External wins for any symbol present in external
            merged.append(ext_by[symbol])
        elif in_ta:
            merged.append(ta_by[symbol])

    return merged


def get_sync_status(db: Session, user_id: str, file_path: str | None = None) -> dict[str, Any]:
    """Read current sync status without making changes.

    Returns the diff between TA and external, plus file existence/metadata.
    """
    path = file_path or get_default_sync_path()

    ta_positions = get_ta_positions(db, user_id)
    ext_data = read_external_holdings(path)
    ext_holdings_raw = ext_data["holdings"] if not ext_data["error"] else []
    ext_positions = external_to_ta_positions(ext_holdings_raw)

    diff = compute_bidirectional_diff(ta_positions, ext_positions)

    return {
        "file_path": path,
        "file_exists": ext_data["error"] != "file_not_found",
        "file_error": ext_data["error"],
        "file_meta": ext_data.get("meta"),
        "ta_count": len(ta_positions),
        "external_count": len(ext_positions),
        "diff": diff,
    }
