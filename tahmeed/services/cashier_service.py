import asyncio
import re
from datetime import datetime, date
from typing import List
from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.transaction import Transaction


async def get_transactions_by_date(target_date: date) -> List[Transaction]:
    db = get_db()
    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59)
    cursor = (
        db.transactions.find({"date": {"$gte": start, "$lte": end}})
        .sort("created_at", 1)
    )
    docs = await cursor.to_list(length=None)
    return [Transaction.from_doc(d) for d in docs]


async def save_transaction(tx: Transaction) -> Transaction:
    db = get_db()
    result = await db.transactions.insert_one(tx.to_doc())
    tx._id = result.inserted_id
    return tx


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
                "tzs_total":        {"$sum": {"$cond": [{"$eq": ["$currency", "TZS"]}, "$amount", 0]}},
                "usd_total":        {"$sum": {"$cond": [{"$eq": ["$currency", "USD"]}, "$amount", 0]}},
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

    _empty = {"count": 0, "tzs_total": 0.0, "usd_total": 0.0,
              "receipt_received": 0, "receipt_pending": 0, "receipt_missing": 0, "unverified": 0}
    return {
        "today":  today_res[0]  if today_res  else _empty,
        "month":  month_res[0]  if month_res  else _empty,
        "recent": [Transaction.from_doc(d) for d in recent_docs],
    }


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
