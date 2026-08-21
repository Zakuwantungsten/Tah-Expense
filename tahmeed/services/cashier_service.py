import asyncio
import re
from datetime import datetime, date, timedelta
from typing import List, Optional
from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.transaction import Transaction

# Effective register day for Simple day-transaction grouping.
_REGISTER_DAY_EXPR = {"$ifNull": ["$import_primary_date", "$date"]}


def _day_bounds(target_date: date) -> tuple:
    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59)
    return start, end


def _register_day_clause(target_date: date) -> dict:
    """Match rows that belong on this register calendar day.

    Daily-import rows are owned by ``import_primary_date`` even when their
    Excel ``date`` is earlier. Manual/legacy rows without a primary date use
    Excel ``date``. Prior-day Excel rows filed under another register day are
    excluded from this day.
    """
    start, end = _day_bounds(target_date)
    return {
        "$or": [
            {"import_primary_date": {"$gte": start, "$lte": end}},
            {
                "$and": [
                    {"date": {"$gte": start, "$lte": end}},
                    {"import_primary_date": None},
                ]
            },
        ]
    }


def _register_day_range_clause(
    date_from: date = None,
    date_to: date = None,
) -> dict:
    """Match rows whose effective register day falls in the browse window."""
    primary: dict = {}
    excel: dict = {}
    if date_from:
        start = datetime(date_from.year, date_from.month, date_from.day, 0, 0, 0)
        primary["$gte"] = start
        excel["$gte"] = start
    if date_to:
        end = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        primary["$lte"] = end
        excel["$lte"] = end
    if not primary:
        return {}
    return {
        "$or": [
            {"import_primary_date": primary},
            {
                "$and": [
                    {"date": excel},
                    {"import_primary_date": None},
                ]
            },
        ]
    }


async def get_transactions_by_date(
    target_date: date,
    cashier_id=None,
    *,
    merged: bool = False,
) -> List[Transaction]:
    """Load a calendar day's register rows.

    Includes daily-import rows filed under that day via ``import_primary_date``
    (Excel dates may differ; the whole upload still belongs on the register day)
    and manual/legacy rows whose Excel ``date`` is that day.

    ``merged=True`` returns every cashier's rows for that day (Shared/Merged mode).
    Otherwise ``cashier_id`` scopes to one user when provided.
    Sorted by ``day_order`` then ``created_at``.
    """
    db = get_db()
    query: dict = {
        **_register_day_clause(target_date),
        "rejected": {"$ne": True},
        "deletion_requested": {"$ne": True},
        "trashed": {"$ne": True},
    }
    if not merged and cashier_id is not None:
        query["cashier_id"] = cashier_id
    cursor = db.transactions.find(query).sort(
        [("day_order", 1), ("created_at", 1)]
    )
    docs = await cursor.to_list(length=None)
    # Prefer open pending-edit clones over their master originals so the
    # register does not show both after a verified row was edited.
    pending_original_ids = {
        d.get("original_transaction_id")
        for d in docs
        if d.get("original_transaction_id") and not d.get("verified")
    }
    if pending_original_ids:
        docs = [d for d in docs if d.get("_id") not in pending_original_ids]
    return [Transaction.from_doc(d) for d in docs]


async def submit_day_for_verify(target_date: date) -> int:
    """Mark all draft (and legacy) unverified rows for *target_date* as submitted.

    Includes daily-import rows filed under this register day via
    ``import_primary_date``. Returns the number of documents updated.
    """
    db = get_db()
    result = await db.transactions.update_many(
        {
            "$and": [
                _register_day_clause(target_date),
                {"verified": {"$ne": True}},
                {"rejected": {"$ne": True}},
                {"discarded": {"$ne": True}},
                {"$or": [
                    {"register_status": "draft"},
                    {"register_status": {"$exists": False}},
                ]},
            ],
        },
        {"$set": {"register_status": "submitted"}},
    )
    return int(result.modified_count)


async def recount_day_order(target_date: date, ordered_ids: List[ObjectId]) -> None:
    """Persist ``day_order`` = index for each id in *ordered_ids*."""
    if not ordered_ids:
        return
    db = get_db()
    ops = []
    from pymongo import UpdateOne
    for i, oid in enumerate(ordered_ids):
        ops.append(UpdateOne({"_id": oid}, {"$set": {"day_order": i}}))
    if ops:
        await db.transactions.bulk_write(ops, ordered=False)


async def next_day_order(target_date: date) -> int:
    """Next ``day_order`` for *target_date* (append after every cashier's rows)."""
    db = get_db()
    docs = await db.transactions.aggregate([
        {"$match": _register_day_clause(target_date)},
        {"$group": {"_id": None, "mx": {"$max": "$day_order"}}},
    ]).to_list(1)
    mx = docs[0].get("mx") if docs else None
    if mx is None:
        return 0
    try:
        return int(mx) + 1
    except (TypeError, ValueError):
        return 0


async def save_transaction(tx: Transaction) -> Transaction:
    db = get_db()
    result = await db.transactions.insert_one(tx.to_doc())
    tx._id = result.inserted_id
    try:
        from tahmeed.services.audit_service import record_event

        await record_event(
            "txn.save",
            actor_id=tx.cashier_id,
            entity_type="transaction",
            entity_ids=[tx._id],
            details={
                "amount": tx.amount,
                "description": (tx.description or "")[:120],
                "register_status": tx.register_status,
            },
        )
    except Exception:
        pass
    return tx


async def update_transaction(tx_id: ObjectId, updates: dict) -> bool:
    """Apply a $set update to an existing transaction. Used by the explicit
    Save action when committing edits to already-saved rows. The caller is
    responsible for building `updates` from the edited cell values (excluding
    immutable / accountant-managed fields)."""
    db = get_db()
    result = await db.transactions.update_one(
        {"_id": tx_id},
        {"$set": updates},
    )
    if result.modified_count == 1:
        try:
            from tahmeed.services.audit_service import record_event

            await record_event(
                "txn.update",
                actor_id=updates.get("last_edited_by"),
                entity_type="transaction",
                entity_ids=[tx_id],
                details={"fields": sorted(str(k) for k in updates.keys())},
            )
        except Exception:
            pass
    return result.modified_count == 1


async def delete_transaction(tx_id: ObjectId) -> None:
    """Hard-delete a transaction by id (no status guards). Prefer
    ``request_or_delete_transaction`` from the cashier register UI."""
    db = get_db()
    await db.transactions.delete_one({"_id": tx_id})


async def request_or_delete_transaction(
    tx_id: ObjectId,
    cashier_id: Optional[ObjectId] = None,
) -> str:
    """Delete or request deletion of a register row.

    Returns one of:
      - ``"deleted"`` — hard-deleted (unverified, or pending-edit clone)
      - ``"deletion_requested"`` — approved row flagged for accountant confirm
      - ``"not_found"`` — no matching document
    """
    db = get_db()
    doc = await db.transactions.find_one({"_id": tx_id})
    if not doc:
        return "not_found"

    is_pending_edit = bool(doc.get("original_transaction_id")) and not doc.get("verified")
    if is_pending_edit or not doc.get("verified"):
        await db.transactions.delete_one({"_id": tx_id})
        return "deleted"

    await db.transactions.update_one(
        {"_id": tx_id},
        {"$set": {
            "deletion_requested": True,
            "deletion_requested_at": datetime.utcnow(),
            "deletion_requested_by": cashier_id or doc.get("cashier_id"),
        }},
    )
    return "deletion_requested"


async def search_transactions(
    date_from: date = None,
    date_to: date = None,
    keyword: str = "",
    truck: str = "",
    limit: int = 500,
) -> List[Transaction]:
    db = get_db()
    query: dict = {
        "trashed": {"$ne": True},
        "deletion_requested": {"$ne": True},
    }
    if date_from or date_to:
        date_filter: dict = {}
        if date_from:
            date_filter["$gte"] = datetime(date_from.year, date_from.month, date_from.day)
        if date_to:
            date_filter["$lte"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        query["date"] = date_filter
    if keyword.strip():
        query["description"] = {"$regex": re.escape(keyword.strip()), "$options": "i"}
    if truck.strip():
        query["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    cursor = db.transactions.find(query).sort([("date", -1), ("created_at", -1)]).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def get_overview_stats() -> dict:
    """Aggregate today stats, this-month stats, and recent 8 transactions."""
    db = get_db()
    today = date.today()

    today_start = datetime(today.year, today.month, today.day)
    today_end   = datetime(today.year, today.month, today.day, 23, 59, 59)
    month_start = datetime(today.year, today.month, 1)

    def _money_group(match: dict) -> list:
        return [
            {"$match": match},
            {"$group": {
                "_id": None,
                "count":            {"$sum": 1},
                "tzs_total":        {"$sum": "$amount"},
                "receipt_received": {"$sum": {"$cond": [{"$eq": ["$receipt_status", "received"]}, 1, 0]}},
                "receipt_pending":  {"$sum": {"$cond": [{"$eq": ["$receipt_status", "pending"]},  1, 0]}},
                "receipt_missing":  {"$sum": {"$cond": [{"$eq": ["$receipt_status", "missing"]},  1, 0]}},
                "unverified":       {"$sum": {"$cond": [{"$eq": ["$verified", False]}, 1, 0]}},
            }},
        ]

    today_res, month_res, recent_docs = await asyncio.gather(
        db.transactions.aggregate(_money_group({"date": {"$gte": today_start, "$lte": today_end}})).to_list(1),
        db.transactions.aggregate(_money_group({"date": {"$gte": month_start}})).to_list(1),
        db.transactions.find({}).sort([("date", -1), ("created_at", -1)]).limit(8).to_list(8),
    )

    _empty = {"count": 0, "tzs_total": 0.0,
              "receipt_received": 0, "receipt_pending": 0, "receipt_missing": 0, "unverified": 0}
    return {
        "today":  today_res[0]  if today_res  else _empty,
        "month":  month_res[0]  if month_res  else _empty,
        "recent": [Transaction.from_doc(d) for d in recent_docs],
    }


async def get_transactions_by_category(
    cashier_id, category_name: str, description: str = "", limit: int = 500
) -> List[Transaction]:
    db = get_db()
    query: dict = {"category_name": category_name}
    if cashier_id is not None:
        query["cashier_id"] = cashier_id
    if description.strip():
        query["description"] = {"$regex": re.escape(description.strip()), "$options": "i"}
    cursor = db.transactions.find(query).sort([("date", -1), ("created_at", -1)]).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def get_daily_summaries(
    date_from: date = None,
    date_to: date = None,
    keyword: str = "",
    truck: str = "",
    category_name="",
    sub_item_match="",
    descriptions="",
    daily_import_id: str = "",
    limit: int = 365,
) -> list:
    """Aggregate transactions by register day, returning one summary dict per day.

    Grouping uses ``import_primary_date`` when set (daily-import register day),
    otherwise Excel ``date``. Prior-day Excel rows inside an upload therefore
    stay on that upload's day transaction instead of spawning a separate TXN-*.

    Each dict has: date (date), entries_count (int), total_tzs (float), total_refund (float).
    Filters narrow which *entries* are counted before the group-by, so a truck
    filter returns days that contain that truck with counts/totals for that truck only.

    ``category_name`` / ``descriptions`` accept a string or list (multi-select).
    ``sub_item_match`` is kept for backward compatibility (maps into descriptions).
    """
    db = get_db()
    desc_filter = descriptions if descriptions not in ("", None, []) else sub_item_match
    uid = (daily_import_id or "").strip()
    # Date window is applied via register-day clause below (not Excel date alone).
    match = _browse_match(
        date_from=date_from if uid else None,
        date_to=date_to if uid else None,
        keyword=keyword,
        truck=truck,
        category_name=category_name,
        descriptions=desc_filter,
        daily_import_id=daily_import_id,
    )
    if not uid and (date_from or date_to):
        range_clause = _register_day_range_clause(date_from, date_to)
        if range_clause:
            if "$and" in match:
                match["$and"] = [range_clause, *match["$and"]]
            elif match:
                match = {"$and": [range_clause, match]}
            else:
                match = range_clause

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {
                "year":  {"$year":  _REGISTER_DAY_EXPR},
                "month": {"$month": _REGISTER_DAY_EXPR},
                "day":   {"$dayOfMonth": _REGISTER_DAY_EXPR},
            },
            "entries_count": {"$sum": 1},
            "total_tzs":     {"$sum": "$amount"},
            "total_refund":  {"$sum": {
                "$cond": [{"$eq": ["$notes_flag", True]}, "$amount", 0]
            }},
            "cashier_ids": {"$addToSet": "$cashier_id"},
            "draft_count": {"$sum": {
                "$cond": [{"$eq": ["$register_status", "draft"]}, 1, 0]
            }},
            "submitted_count": {"$sum": {
                "$cond": [
                    {"$or": [
                        {"$eq": ["$register_status", "submitted"]},
                        {"$eq": [{"$type": "$register_status"}, "missing"]},
                    ]},
                    1,
                    0,
                ]
            }},
        }},
        {"$sort": {"_id.year": -1, "_id.month": -1, "_id.day": -1}},
        {"$limit": limit},
    ]
    cursor = db.transactions.aggregate(pipeline)
    docs = await cursor.to_list(length=limit)
    result = []
    for d in docs:
        g = d["_id"]
        cashier_ids = [cid for cid in (d.get("cashier_ids") or []) if cid is not None]
        result.append({
            "date":             date(g["year"], g["month"], g["day"]),
            "entries_count":    d["entries_count"],
            "total_tzs":        d["total_tzs"],
            "total_refund":     d["total_refund"],
            "cashier_ids":      cashier_ids,
            "draft_count":      int(d.get("draft_count") or 0),
            "submitted_count":  int(d.get("submitted_count") or 0),
        })
    return result


def _normalize_multi_filter(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    out: List[str] = []
    for v in value:
        s = str(v).strip()
        if s:
            out.append(s)
    return out


def _browse_match(
    *,
    date_from: date = None,
    date_to: date = None,
    keyword: str = "",
    truck: str = "",
    category_name="",
    descriptions="",
    daily_import_id: str = "",
) -> dict:
    """Shared match clause for browse list / distinct-option queries."""
    match: dict = {}
    and_clauses: list = []

    uid = (daily_import_id or "").strip()
    if uid:
        # Whole upload — do not also clip by Excel date range.
        match["daily_import_id"] = uid
    elif date_from or date_to:
        date_filter: dict = {}
        if date_from:
            date_filter["$gte"] = datetime(date_from.year, date_from.month, date_from.day)
        if date_to:
            date_filter["$lte"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        match["date"] = date_filter

    cats = _normalize_multi_filter(category_name)
    if cats:
        cat_ors: list = []
        for c in cats:
            esc = re.escape(c)
            cat_ors.append({"category_name": {"$regex": f"^{esc}$", "$options": "i"}})
            cat_ors.append({"item": {"$regex": f"^{esc}$", "$options": "i"}})
        and_clauses.append({"$or": cat_ors})

    descs = _normalize_multi_filter(descriptions)
    if descs:
        if isinstance(descriptions, str):
            and_clauses.append({
                "description": {
                    "$regex": re.escape(descs[0]),
                    "$options": "i",
                },
            })
        else:
            and_clauses.append({"$or": [
                {"description": {"$regex": f"^{re.escape(d)}$", "$options": "i"}}
                for d in descs
            ]})
    elif keyword.strip():
        kw_re = {"$regex": re.escape(keyword.strip()), "$options": "i"}
        and_clauses.append({"$or": [
            {"description":  kw_re},
            {"truck_number": kw_re},
            {"memo":         kw_re},
        ]})

    if truck.strip():
        match["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}

    if and_clauses:
        match["$and"] = and_clauses
    return match


async def get_transactions_flat(
    date_from: date = None,
    date_to: date = None,
    keyword: str = "",
    category_name="",
    sub_item_match="",
    descriptions="",
    daily_import_id: str = "",
    limit: int = 1000,
) -> List[Transaction]:
    """Return individual transactions matching all supplied filters."""
    db = get_db()
    desc_filter = descriptions if descriptions not in ("", None, []) else sub_item_match
    match = _browse_match(
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
        category_name=category_name,
        descriptions=desc_filter,
        daily_import_id=daily_import_id,
    )
    cursor = db.transactions.find(match).sort([("date", -1), ("created_at", -1)]).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def get_browse_items(
    date_from: date = None,
    date_to: date = None,
    descriptions=None,
) -> List[str]:
    """Distinct item / category names in the browse date window (cascading)."""
    db = get_db()
    match = _browse_match(
        date_from=date_from,
        date_to=date_to,
        descriptions=descriptions or [],
    )
    items = await db.transactions.distinct("item", match)
    cats = await db.transactions.distinct("category_name", match)
    names = {*(v for v in items if v), *(v for v in cats if v)}
    return sorted(names, key=str.lower)


async def get_browse_descriptions(
    date_from: date = None,
    date_to: date = None,
    category_name=None,
) -> List[str]:
    """Distinct descriptions in the browse date window (cascading by items)."""
    db = get_db()
    match = _browse_match(
        date_from=date_from,
        date_to=date_to,
        category_name=category_name or [],
    )
    vals = await db.transactions.distinct("description", match)
    return sorted((v for v in vals if v), key=str.lower)


async def get_available_months() -> list:
    """Return (year, month) tuples for every month that has transactions, newest first."""
    db = get_db()
    pipeline = [
        {"$group": {
            "_id": {
                "year":  {"$year":  "$date"},
                "month": {"$month": "$date"},
            }
        }},
        {"$sort": {"_id.year": -1, "_id.month": -1}},
    ]
    docs = await db.transactions.aggregate(pipeline).to_list(length=None)
    return [(d["_id"]["year"], d["_id"]["month"]) for d in docs]


async def get_available_years() -> list:
    """Calendar years that have transactions, newest first (always includes recent years)."""
    db = get_db()
    pipeline = [
        {"$group": {"_id": {"$year": "$date"}}},
        {"$sort": {"_id": -1}},
    ]
    docs = await db.transactions.aggregate(pipeline).to_list(length=40)
    years = [int(d["_id"]) for d in docs if d.get("_id") is not None]
    today_y = date.today().year
    for y in (today_y + 1, today_y, today_y - 1):
        if y not in years:
            years.append(y)
    return sorted(set(years), reverse=True)


# In-memory description history for Excel-style autocomplete across all days.
_desc_ranked: Optional[List[str]] = None
_desc_counts: Optional[dict] = None
_desc_lock = asyncio.Lock()
_DESC_CACHE_LIMIT = 5000


def invalidate_description_cache() -> None:
    global _desc_ranked, _desc_counts
    _desc_ranked = None
    _desc_counts = None


def _rebuild_desc_ranked() -> None:
    global _desc_ranked
    counts = _desc_counts or {}
    _desc_ranked = sorted(counts.keys(), key=lambda d: (-counts[d], d))


async def ensure_description_cache() -> List[str]:
    """Load distinct descriptions (all days) ranked by frequency."""
    global _desc_ranked, _desc_counts
    if _desc_ranked is not None:
        return _desc_ranked
    async with _desc_lock:
        if _desc_ranked is not None:
            return _desc_ranked
        db = get_db()
        pipeline = [
            {"$match": {"description": {"$type": "string", "$ne": ""}}},
            {"$group": {
                "_id": {"$toUpper": "$description"},
                "n": {"$sum": 1},
            }},
            {"$sort": {"n": -1, "_id": 1}},
            {"$limit": _DESC_CACHE_LIMIT},
        ]
        docs = await db.transactions.aggregate(pipeline).to_list(
            length=_DESC_CACHE_LIMIT
        )
        counts: dict = {}
        ranked: list = []
        for doc in docs:
            name = str(doc.get("_id") or "").strip()
            if not name:
                continue
            counts[name] = int(doc.get("n") or 0)
            ranked.append(name)
        _desc_counts = counts
        _desc_ranked = ranked
        return _desc_ranked


def search_descriptions_sync(prefix: str, limit: int = 12) -> Optional[List[str]]:
    """Prefix filter against the warm description cache (no await).

    Returns ``None`` when the cache has not been loaded yet — callers should
    fall through to the async path. Safe during modal dialogs / nested tasks.
    """
    if _desc_ranked is None:
        return None
    value = (prefix or "").strip().lower()
    if not value:
        return []
    out: List[str] = []
    for description in _desc_ranked:
        if description.lower().startswith(value):
            out.append(description)
            if len(out) >= limit:
                break
    return out


def remember_description(text: str) -> None:
    """Boost a just-entered description so same-session typing can Tab-complete it."""
    global _desc_ranked, _desc_counts
    if _desc_ranked is None:
        return
    name = (text or "").strip().upper()
    if not name:
        return
    counts = _desc_counts if _desc_counts is not None else {}
    counts[name] = counts.get(name, 0) + 1
    _desc_counts = counts
    _rebuild_desc_ranked()


async def search_descriptions(prefix: str, limit: int = 12) -> List[str]:
    """
    Return distinct descriptions whose prefix matches (case-insensitive),
    sorted by frequency so the most-used descriptions appear first.
    Uses the warm in-memory cache covering all days in the system.
    """
    if not prefix.strip():
        return []
    await ensure_description_cache()
    return search_descriptions_sync(prefix, limit) or []


async def resolve_item_name_for_description(description: str) -> Optional[str]:
    """Item/category for a description: saved map first, then prior entries.

    Prefers verified history when no mapping exists. Returns None when neither
    source has an item.
    """
    from tahmeed.services.description_mapping_service import (
        normalize_description,
        resolve_category_for_description,
    )

    key = normalize_description(description)
    if not key:
        return None
    try:
        mapped = await resolve_category_for_description(description)
        if mapped and (mapped[1] or "").strip():
            return mapped[1].strip()
    except Exception:
        pass

    db = get_db()
    desc_match = {"description": {"$regex": f"^{re.escape(key)}$", "$options": "i"}}
    has_item = {
        "$or": [
            {"item": {"$type": "string", "$ne": ""}},
            {"category_name": {"$type": "string", "$ne": ""}},
        ]
    }
    projection = {"item": 1, "category_name": 1}

    async def _first(extra: dict) -> Optional[dict]:
        cursor = (
            db.transactions.find({**desc_match, **has_item, **extra}, projection)
            .sort([("date", -1), ("created_at", -1)])
            .limit(1)
        )
        docs = await cursor.to_list(length=1)
        return docs[0] if docs else None

    doc = await _first({"verified": True})
    if not doc:
        doc = await _first({})
    if not doc:
        return None
    return (doc.get("item") or doc.get("category_name") or "").strip() or None


async def get_rejected_transactions_for_cashier(
    cashier_id: ObjectId,
    limit: int = 200,
) -> List[Transaction]:
    """Active rejected entries for this cashier (excludes discarded), newest first."""
    db = get_db()
    cursor = (
        db.transactions
        .find({
            "cashier_id": cashier_id,
            "rejected": True,
            "discarded": {"$ne": True},
        })
        .sort([("date", -1), ("created_at", -1)])
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def get_discarded_transactions_for_cashier(
    cashier_id: ObjectId,
    limit: int = 200,
) -> List[Transaction]:
    """Soft-discarded rejected entries for this cashier, newest first."""
    db = get_db()
    cursor = (
        db.transactions
        .find({
            "cashier_id": cashier_id,
            "discarded": True,
        })
        .sort([("date", -1), ("created_at", -1)])
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def discard_transactions(
    tx_ids: List[ObjectId],
    cashier_id: ObjectId,
) -> int:
    """Soft-discard rejected entries owned by this cashier. Returns modified count."""
    if not tx_ids:
        return 0
    db = get_db()
    result = await db.transactions.update_many(
        {
            "_id": {"$in": list(tx_ids)},
            "cashier_id": cashier_id,
            "rejected": True,
            "discarded": {"$ne": True},
        },
        {"$set": {"discarded": True}},
    )
    return result.modified_count


async def restore_discarded_transactions(
    tx_ids: List[ObjectId],
    cashier_id: ObjectId,
) -> int:
    """Move discarded entries back to the active Rejected list. Returns modified count."""
    if not tx_ids:
        return 0
    db = get_db()
    result = await db.transactions.update_many(
        {
            "_id": {"$in": list(tx_ids)},
            "cashier_id": cashier_id,
            "discarded": True,
        },
        {"$set": {"discarded": False}},
    )
    return result.modified_count


async def delete_discarded_transactions(
    tx_ids: List[ObjectId],
    cashier_id: ObjectId,
) -> int:
    """Permanently delete discarded entries owned by this cashier. Returns deleted count."""
    if not tx_ids:
        return 0
    db = get_db()
    result = await db.transactions.delete_many(
        {
            "_id": {"$in": list(tx_ids)},
            "cashier_id": cashier_id,
            "discarded": True,
        },
    )
    return result.deleted_count


async def resubmit_rejected_transactions(
    cashier_id: ObjectId,
    updates_by_id: dict,
) -> int:
    """Apply field updates and clear rejection/discard flags for owned rejected rows.

    ``updates_by_id`` maps ObjectId → field updates dict (may be empty for
    resubmit-without-edit). Returns number of successfully updated documents.
    """
    if not updates_by_id:
        return 0
    db = get_db()
    now = datetime.utcnow()
    updated = 0
    for tx_id, field_updates in updates_by_id.items():
        payload = dict(field_updates or {})
        payload.update({
            "rejected": False,
            "rejection_reason": None,
            "discarded": False,
            "last_edited_at": now,
            "last_edited_by": cashier_id,
        })
        result = await db.transactions.update_one(
            {
                "_id": tx_id,
                "cashier_id": cashier_id,
                "rejected": True,
                "discarded": {"$ne": True},
            },
            {"$set": payload},
        )
        if result.modified_count:
            updated += 1
    return updated


async def check_for_duplicates(
    truck_number: str,
    amount: float,
    item: str,
    description: str,
    days: int,
    exclude_id: Optional[ObjectId] = None,
) -> List[Transaction]:
    """Return existing transactions within the last `days` days that share
    truck_number, amount, item, and description with the candidate entry."""
    db = get_db()
    cutoff = datetime.utcnow() - timedelta(days=days)
    query: dict = {
        "date": {"$gte": cutoff},
        "rejected": {"$ne": True},
        "amount": amount,
    }
    if truck_number:
        query["truck_number"] = {"$regex": f"^{re.escape(truck_number)}$", "$options": "i"}
    if item:
        query["item"] = {"$regex": f"^{re.escape(item)}$", "$options": "i"}
    if description:
        query["description"] = {"$regex": f"^{re.escape(description)}$", "$options": "i"}
    if exclude_id:
        query["_id"] = {"$ne": exclude_id}
    cursor = db.transactions.find(query).sort("date", -1).limit(10)
    docs = await cursor.to_list(length=10)
    return [Transaction.from_doc(d) for d in docs]


async def insert_pending_edit(
    original_tx_id: ObjectId,
    updates: dict,
    cashier_id: ObjectId,
) -> ObjectId:
    """Insert (or refresh) a pending-edit document instead of modifying the original.

    The original approved transaction stays untouched in Master Expenses.
    The pending doc carries original_transaction_id so the accountant's
    re_approve_transaction can cascade the new values back to the original.
    Re-editing the same original updates the existing pending doc in place.
    """
    db = get_db()
    original_doc = await db.transactions.find_one({"_id": original_tx_id})
    if not original_doc:
        raise ValueError(f"Original transaction {original_tx_id} not found")

    now = datetime.utcnow()
    meta = {
        "original_transaction_id": original_tx_id,
        "edited_after_verification": True,
        "verified": False,
        "verified_by": None,
        "verified_at": None,
        "rejection_reason": None,
        "rejected": False,
        "discarded": False,
        "last_edited_at": now,
        "last_edited_by": cashier_id,
    }

    existing = await db.transactions.find_one({
        "original_transaction_id": original_tx_id,
        "verified": False,
        "edited_after_verification": True,
        "rejected": {"$ne": True},
    })
    if existing:
        patch = dict(updates)
        patch.update(meta)
        await db.transactions.update_one({"_id": existing["_id"]}, {"$set": patch})
        return existing["_id"]

    pending = dict(original_doc)
    pending.pop("_id", None)
    pending.update(updates)
    pending.update(meta)
    pending["created_at"] = now

    result = await db.transactions.insert_one(pending)
    return result.inserted_id
