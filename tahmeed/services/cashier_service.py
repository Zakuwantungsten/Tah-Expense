import asyncio
import re
from datetime import datetime, date
from typing import List
from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.transaction import Transaction


async def get_transactions_by_date(target_date: date, cashier_id=None) -> List[Transaction]:
    db = get_db()
    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59)
    query: dict = {"date": {"$gte": start, "$lte": end}}
    if cashier_id is not None:
        query["cashier_id"] = cashier_id
    cursor = db.transactions.find(query).sort("created_at", 1)
    docs = await cursor.to_list(length=None)
    return [Transaction.from_doc(d) for d in docs]


async def save_transaction(tx: Transaction) -> Transaction:
    db = get_db()
    result = await db.transactions.insert_one(tx.to_doc())
    tx._id = result.inserted_id
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
    return result.modified_count == 1


async def delete_transaction(tx_id: ObjectId) -> None:
    db = get_db()
    await db.transactions.delete_one({"_id": tx_id})


async def search_transactions(
    date_from: date = None,
    date_to: date = None,
    keyword: str = "",
    truck: str = "",
    limit: int = 500,
) -> List[Transaction]:
    db = get_db()
    query: dict = {}
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
    category_name: str = "",
    sub_item_match: str = "",
    limit: int = 365,
) -> list:
    """Aggregate transactions by calendar day, returning one summary dict per day.

    Each dict has: date (date), entries_count (int), total_tzs (float), total_refund (float).
    Filters narrow which *entries* are counted before the group-by, so a truck
    filter returns days that contain that truck with counts/totals for that truck only.
    sub_item_match filters on description and takes priority over keyword when both are set.
    """
    db = get_db()
    match: dict = {}
    if date_from or date_to:
        date_filter: dict = {}
        if date_from:
            date_filter["$gte"] = datetime(date_from.year, date_from.month, date_from.day)
        if date_to:
            date_filter["$lte"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        match["date"] = date_filter
    if category_name.strip():
        match["category_name"] = category_name.strip()
    if truck.strip():
        match["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    if sub_item_match.strip():
        # advanced exact-match on description only
        match["description"] = {"$regex": re.escape(sub_item_match.strip()), "$options": "i"}
    elif keyword.strip():
        # simple keyword: OR across description, truck_number, memo
        kw_re = {"$regex": re.escape(keyword.strip()), "$options": "i"}
        match["$or"] = [
            {"description":  kw_re},
            {"truck_number": kw_re},
            {"memo":         kw_re},
        ]

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {
                "year":  {"$year":  "$date"},
                "month": {"$month": "$date"},
                "day":   {"$dayOfMonth": "$date"},
            },
            "entries_count": {"$sum": 1},
            "total_tzs":     {"$sum": "$amount"},
            "total_refund":  {"$sum": {
                "$cond": [{"$eq": ["$notes_flag", True]}, "$amount", 0]
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
        result.append({
            "date":          date(g["year"], g["month"], g["day"]),
            "entries_count": d["entries_count"],
            "total_tzs":     d["total_tzs"],
            "total_refund":  d["total_refund"],
        })
    return result


async def get_transactions_flat(
    date_from: date = None,
    date_to: date = None,
    keyword: str = "",
    category_name: str = "",
    sub_item_match: str = "",
    limit: int = 1000,
) -> List[Transaction]:
    """Return individual transactions matching all supplied filters."""
    db = get_db()
    match: dict = {}
    if date_from or date_to:
        date_filter: dict = {}
        if date_from:
            date_filter["$gte"] = datetime(date_from.year, date_from.month, date_from.day)
        if date_to:
            date_filter["$lte"] = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        match["date"] = date_filter
    if category_name.strip():
        match["category_name"] = category_name.strip()
    if sub_item_match.strip():
        match["description"] = {"$regex": re.escape(sub_item_match.strip()), "$options": "i"}
    elif keyword.strip():
        kw_re = {"$regex": re.escape(keyword.strip()), "$options": "i"}
        match["$or"] = [
            {"description":  kw_re},
            {"truck_number": kw_re},
            {"memo":         kw_re},
        ]
    cursor = db.transactions.find(match).sort([("date", -1), ("created_at", -1)]).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


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


async def search_descriptions(prefix: str, limit: int = 12) -> List[str]:
    """
    Return distinct descriptions whose prefix matches (case-insensitive),
    sorted by frequency so the most-used descriptions appear first.
    """
    if not prefix.strip():
        return []
    db = get_db()
    pattern = f"^{re.escape(prefix.strip())}"
    pipeline = [
        {"$match": {"description": {"$regex": pattern, "$options": "i"}}},
        {"$group": {"_id": "$description", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": limit},
        {"$project": {"_id": 1}},
    ]
    cursor = db.transactions.aggregate(pipeline)
    results = await cursor.to_list(length=limit)
    return [r["_id"] for r in results]
