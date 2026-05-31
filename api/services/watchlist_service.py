"""Watchlist service for database operations."""

from typing import List
from uuid import uuid4

from sqlalchemy.orm import Session

from api.database import WatchlistItemDB, ScheduledAnalysisDB

MAX_WATCHLIST_ITEMS = 300


def list_watchlist(db: Session, user_id: str) -> List[dict]:
    """List user's watchlist items with scheduled status."""
    items = (
        db.query(WatchlistItemDB)
        .filter(WatchlistItemDB.user_id == user_id)
        .order_by(WatchlistItemDB.sort_order, WatchlistItemDB.created_at)
        .all()
    )
    scheduled_symbols = set(
        row.symbol for row in
        db.query(ScheduledAnalysisDB.symbol)
        .filter(ScheduledAnalysisDB.user_id == user_id)
        .all()
    )
    return [
        {
            "id": item.id,
            "symbol": item.symbol,
            "sort_order": item.sort_order,
            "notes": item.notes,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "has_scheduled": item.symbol in scheduled_symbols,
        }
        for item in items
    ]


def add_watchlist_item(db: Session, user_id: str, symbol: str, notes: str | None = None) -> dict:
    """Add a stock to user's watchlist."""
    count = db.query(WatchlistItemDB).filter(WatchlistItemDB.user_id == user_id).count()
    if count >= MAX_WATCHLIST_ITEMS:
        raise ValueError(f"自选股数量已达上限 ({MAX_WATCHLIST_ITEMS})")

    existing = (
        db.query(WatchlistItemDB)
        .filter(WatchlistItemDB.user_id == user_id, WatchlistItemDB.symbol == symbol)
        .first()
    )
    if existing:
        raise ValueError(f"{symbol} 已在自选列表中")

    item = WatchlistItemDB(id=uuid4().hex, user_id=user_id, symbol=symbol, notes=notes)
    db.add(item)
    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "symbol": item.symbol,
        "sort_order": item.sort_order,
        "notes": item.notes,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def add_watchlist_items(db: Session, user_id: str, symbols: List[str]) -> List[dict]:
    """Add multiple stocks to user's watchlist and return per-item results."""
    results: List[dict] = []
    for symbol in symbols:
        try:
            item = add_watchlist_item(db, user_id, symbol)
            results.append({
                "symbol": symbol,
                "status": "added",
                "item": item,
                "message": "已添加到自选列表",
            })
        except ValueError as exc:
            message = str(exc)
            status = "duplicate" if "已在自选列表" in message else "failed"
            results.append({
                "symbol": symbol,
                "status": status,
                "message": message,
            })
    return results


# [VLM-001] watchlist_table_parser
def add_watchlist_items_with_notes(
    db: Session,
    user_id: str,
    entries: List[dict],
) -> List[dict]:
    """Add multiple stocks with optional notes. Each entry: {symbol, notes?}."""
    results: List[dict] = []
    for entry in entries:
        symbol = entry.get("symbol", "")
        notes = entry.get("notes")
        try:
            item = add_watchlist_item(db, user_id, symbol, notes=notes)
            results.append({
                "symbol": symbol,
                "status": "added",
                "item": item,
                "message": "已添加到自选列表",
            })
        except ValueError as exc:
            message = str(exc)
            status = "duplicate" if "已在自选列表" in message else "failed"
            results.append({
                "symbol": symbol,
                "status": status,
                "message": message,
            })
    return results


def update_watchlist_notes(db: Session, user_id: str, item_id: str, notes: str, clear: bool = False) -> dict | None:
    """Update notes for a watchlist item. Returns updated item or None if not found.

    Protection: empty notes won't overwrite existing notes unless clear=True.
    """
    item = (
        db.query(WatchlistItemDB)
        .filter(WatchlistItemDB.id == item_id, WatchlistItemDB.user_id == user_id)
        .first()
    )
    if not item:
        return None
    # 防误清空：空备注不覆盖已有备注，除非明确要求清空
    if not notes and not clear and item.notes:
        return {
            "id": item.id,
            "symbol": item.symbol,
            "sort_order": item.sort_order,
            "notes": item.notes,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
    item.notes = notes if notes else None
    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "symbol": item.symbol,
        "sort_order": item.sort_order,
        "notes": item.notes,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def delete_watchlist_item(db: Session, user_id: str, item_id: str) -> bool:
    """Delete a watchlist item. Returns True if found and deleted."""
    item = (
        db.query(WatchlistItemDB)
        .filter(WatchlistItemDB.id == item_id, WatchlistItemDB.user_id == user_id)
        .first()
    )
    if not item:
        return False
    db.delete(item)
    db.commit()
    return True


def reorder_watchlist(db: Session, user_id: str, items: List[dict]) -> None:
    """Batch-update sort_order for watchlist items.

    ``items`` is a list of ``{"id": str, "sort_order": int}``.
    Validates that all ids belong to the user and sort_orders are unique,
    then persists in a single transaction.
    """
    if not items:
        return

    id_list = [entry["id"] for entry in items]
    order_list = [entry["sort_order"] for entry in items]

    if len(set(id_list)) != len(id_list):
        raise ValueError("存在重复的 id")
    if len(set(order_list)) != len(order_list):
        raise ValueError("存在重复的 sort_order")

    rows = (
        db.query(WatchlistItemDB)
        .filter(WatchlistItemDB.user_id == user_id, WatchlistItemDB.id.in_(id_list))
        .all()
    )
    if len(rows) != len(id_list):
        raise ValueError("包含不属于当前用户的自选股")

    row_map = {row.id: row for row in rows}
    for entry in items:
        row_map[entry["id"]].sort_order = entry["sort_order"]
    db.commit()
