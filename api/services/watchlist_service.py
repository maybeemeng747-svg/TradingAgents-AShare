"""Watchlist service for database operations."""

from typing import List

from sqlalchemy.orm import Session

from api.database import WatchlistItemDB, ScheduledAnalysisDB

MAX_WATCHLIST_ITEMS = 300


def _item_to_dict(item: WatchlistItemDB, extra: dict | None = None) -> dict:
    # [DATA-009] watchlist_notes_persistence
    d = {
        "id": item.id,
        "symbol": item.symbol,
        "sort_order": item.sort_order,
        "notes": item.notes,
        "topic": getattr(item, "topic", None),
        "benefit_score": getattr(item, "benefit_score", None),
        "consensus_score": getattr(item, "consensus_score", None),
        "expected_window": getattr(item, "expected_window", None),
        "evidence_gap": getattr(item, "evidence_gap", None),
        "watchlist_note_suggested": getattr(item, "watchlist_note_suggested", None),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
    if extra:
        d.update(extra)
    return d


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
        _item_to_dict(item, {"has_scheduled": item.symbol in scheduled_symbols})
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

    item = WatchlistItemDB(id=__import__("uuid").uuid4().hex, user_id=user_id, symbol=symbol, notes=notes)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _item_to_dict(item)


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


# [VLM-001] watchlist_notes_protection
def _merge_notes(existing_notes: str | None, new_notes: str | None) -> str | None:
    if not new_notes:
        return existing_notes
    if not existing_notes:
        return new_notes
    return f"{existing_notes}｜{new_notes}"


# [VLM-001] watchlist_notes_protection
# [DATA-009] watchlist_notes_persistence
def add_watchlist_items_with_notes(
    db: Session,
    user_id: str,
    entries: List[dict],
) -> List[dict]:
    """Add multiple stocks with optional notes and structured fields. Each entry:
    {symbol, notes?, topic?, benefit_score?, consensus_score?, expected_window?,
     evidence_gap?, watchlist_note_suggested?}.

    When a stock already exists (duplicate):
    - If new notes provided and existing notes empty → write notes directly
    - If new notes provided and existing notes non-empty → append with ｜ separator
    - If no new notes → leave unchanged
    - Structured fields always preserved/merged (new values overwrite old)
    """
    results: List[dict] = []
    for entry in entries:
        symbol = entry.get("symbol", "")
        notes = entry.get("notes")
        # [DATA-009] structured fields from VLM/H-008
        topic = entry.get("topic")
        benefit_score = entry.get("benefit_score")
        consensus_score = entry.get("consensus_score")
        expected_window = entry.get("expected_window")
        evidence_gap = entry.get("evidence_gap")
        watchlist_note_suggested = entry.get("watchlist_note_suggested")
        try:
            item = add_watchlist_item(db, user_id, symbol, notes=notes)
            # [DATA-009] persist structured fields on new item
            _update_structured_fields(db, user_id, symbol, topic=topic,
                                      benefit_score=benefit_score,
                                      consensus_score=consensus_score,
                                      expected_window=expected_window,
                                      evidence_gap=evidence_gap,
                                      watchlist_note_suggested=watchlist_note_suggested)
            results.append({
                "symbol": symbol,
                "status": "added",
                "item": _get_item_dict(db, user_id, symbol),
                "message": "已添加到自选列表",
            })
        except ValueError as exc:
            message = str(exc)
            status = "duplicate" if "已在自选列表" in message else "failed"
            merged_notes = None
            updated_item = None
            if status == "duplicate" and notes:
                existing = (
                    db.query(WatchlistItemDB)
                    .filter(WatchlistItemDB.user_id == user_id, WatchlistItemDB.symbol == symbol)
                    .first()
                )
                if existing:
                    merged_notes = _merge_notes(existing.notes, notes)
                    if merged_notes != existing.notes:
                        existing.notes = merged_notes
                        db.commit()
                        db.refresh(existing)
                    updated_item = _item_to_dict(existing)
            elif status == "duplicate":
                # [DATA-009] still update structured fields even without notes
                existing = (
                    db.query(WatchlistItemDB)
                    .filter(WatchlistItemDB.user_id == user_id, WatchlistItemDB.symbol == symbol)
                    .first()
                )
                if existing and any(v is not None for v in [topic, benefit_score, consensus_score, expected_window, evidence_gap, watchlist_note_suggested]):
                    _update_structured_fields(db, user_id, symbol, topic=topic,
                                              benefit_score=benefit_score,
                                              consensus_score=consensus_score,
                                              expected_window=expected_window,
                                              evidence_gap=evidence_gap,
                                              watchlist_note_suggested=watchlist_note_suggested)
                    updated_item = _get_item_dict(db, user_id, symbol)
                elif existing:
                    updated_item = _item_to_dict(existing)
            results.append({
                "symbol": symbol,
                "status": status,
                "message": message if not merged_notes else f"{message}（备注已追加）",
                **({"item": updated_item} if updated_item else {}),
            })
    return results


# [DATA-009] watchlist_notes_persistence
def _get_item_dict(db: Session, user_id: str, symbol: str) -> dict | None:
    existing = (
        db.query(WatchlistItemDB)
        .filter(WatchlistItemDB.user_id == user_id, WatchlistItemDB.symbol == symbol)
        .first()
    )
    return _item_to_dict(existing) if existing else None


def _update_structured_fields(
    db: Session,
    user_id: str,
    symbol: str,
    *,
    topic: str | None = None,
    benefit_score: float | None = None,
    consensus_score: int | None = None,
    expected_window: str | None = None,
    evidence_gap: str | None = None,
    watchlist_note_suggested: str | None = None,
) -> None:
    existing = (
        db.query(WatchlistItemDB)
        .filter(WatchlistItemDB.user_id == user_id, WatchlistItemDB.symbol == symbol)
        .first()
    )
    if not existing:
        return
    if topic is not None:
        existing.topic = topic
    if benefit_score is not None:
        existing.benefit_score = benefit_score
    if consensus_score is not None:
        existing.consensus_score = consensus_score
    if expected_window is not None:
        existing.expected_window = expected_window
    if evidence_gap is not None:
        existing.evidence_gap = evidence_gap
    if watchlist_note_suggested is not None:
        existing.watchlist_note_suggested = watchlist_note_suggested
    db.commit()
    db.refresh(existing)


def update_watchlist_notes(db: Session, user_id: str, item_id: str, notes: str, clear: bool = False) -> dict | None:
    """Update notes for a watchlist item. Returns updated item or None if not found.

    Protection: empty notes won't overwrite existing notes unless clear=True.
    Structured fields (topic, benefit_score, etc.) are never cleared by this function.
    """
    item = (
        db.query(WatchlistItemDB)
        .filter(WatchlistItemDB.id == item_id, WatchlistItemDB.user_id == user_id)
        .first()
    )
    if not item:
        return None
    if not notes and not clear and item.notes:
        return _item_to_dict(item)
    item.notes = notes if notes else None
    db.commit()
    db.refresh(item)
    return _item_to_dict(item)


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
