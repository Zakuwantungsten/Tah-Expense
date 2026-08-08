"""Idempotent import helpers — stable row keys + duplicate-tolerant inserts."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Optional, Sequence, Tuple

from pymongo.errors import BulkWriteError

from tahmeed.db.connection import get_db

_indexes_ensured = False


def reset_import_index_cache() -> None:
    global _indexes_ensured
    _indexes_ensured = False


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip().upper()


def daily_import_row_key(payload: dict) -> str:
    """Stable hash for one daily→master row within an upload batch."""
    parts = [
        _norm(payload.get("serial")),
        _norm(payload.get("date")),
        _norm(payload.get("description")),
        _norm(payload.get("truck_number")),
        _norm(payload.get("amount")),
        _norm(payload.get("item") or payload.get("category_name")),
        _norm(payload.get("lpo_do")),
        _norm(payload.get("do_number")),
        _norm(payload.get("currency") or "TZS"),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:40]


async def ensure_import_indexes() -> None:
    """Create unique indexes used for idempotent imports (safe to call often)."""
    global _indexes_ensured
    if _indexes_ensured:
        return
    db = get_db()
    try:
        await db.transactions.create_index(
            [("daily_import_id", 1), ("import_row_key", 1)],
            name="uniq_daily_import_row",
            unique=True,
            partialFilterExpression={
                "daily_import_id": {"$type": "string"},
                "import_row_key": {"$type": "string"},
            },
        )
        await db.transactions.create_index(
            [("master_import_source", 1), ("master_serial", 1)],
            name="uniq_master_import_serial",
            unique=True,
            partialFilterExpression={
                "master_import_source": {"$type": "string"},
                "master_serial": {"$exists": True},
            },
        )
        await db.imported_feeds.create_index(
            [("skipped_row_id", 1)],
            name="uniq_feed_skipped_row_id",
            unique=True,
            sparse=True,
        )
        await db.separate_expenses.create_index(
            [("skipped_row_id", 1)],
            name="uniq_sep_skipped_row_id",
            unique=True,
            sparse=True,
        )
    except Exception:
        # Index build may fail on pre-existing duplicates — inserts still use
        # soft dedupe; migrate.py is the authoritative conflict reporter.
        pass
    _indexes_ensured = True


async def insert_many_idempotent(
    collection: Any,
    docs: Sequence[dict],
    *,
    session: Any = None,
) -> Tuple[int, int]:
    """Insert docs ignoring duplicate-key conflicts.

    Returns ``(inserted_count, duplicate_skipped)``.
    """
    if not docs:
        return 0, 0
    kwargs: dict = {"ordered": False}
    if session is not None:
        kwargs["session"] = session
    try:
        result = await collection.insert_many(list(docs), **kwargs)
        return len(result.inserted_ids), 0
    except BulkWriteError as exc:
        details = exc.details or {}
        inserted = int(details.get("nInserted") or 0)
        errors = details.get("writeErrors") or []
        dupes = sum(1 for err in errors if err.get("code") == 11000)
        other = [err for err in errors if err.get("code") != 11000]
        if other:
            raise
        return inserted, dupes
