"""Accountant service — async Motor queries for the accountant dashboard."""

import asyncio
import calendar
import re
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.transaction import Transaction


# ── transactions ──────────────────────────────────────────────────────────────

async def get_unverified_transactions(limit: int = 50, skip: int = 0) -> List[Transaction]:
    db = get_db()
    cursor = (
        db.transactions
        .find({"verified": False})
        .sort([("date", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def get_verified_transactions(
    year: Optional[int] = None,
    month: Optional[int] = None,
    category: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
) -> List[Transaction]:
    db = get_db()
    query: dict = {"verified": True}

    if year or month:
        date_filter: dict = {}
        _year = year or date.today().year
        if month:
            last_day = calendar.monthrange(_year, month)[1]
            date_filter["$gte"] = datetime(_year, month, 1)
            date_filter["$lte"] = datetime(_year, month, last_day, 23, 59, 59)
        else:
            date_filter["$gte"] = datetime(_year, 1, 1)
            date_filter["$lte"] = datetime(_year, 12, 31, 23, 59, 59)
        query["date"] = date_filter

    if category:
        query["category_name"] = category

    cursor = (
        db.transactions
        .find(query)
        .sort([("date", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def get_transactions_by_ids(tx_ids: List[ObjectId]) -> List[Transaction]:
    if not tx_ids:
        return []
    db = get_db()
    docs = await db.transactions.find({"_id": {"$in": tx_ids}}).to_list(length=len(tx_ids))
    return [Transaction.from_doc(d) for d in docs]


async def approve_transaction(tx_id: ObjectId, accountant_id: ObjectId) -> bool:
    db = get_db()
    result = await db.transactions.update_one(
        {"_id": tx_id, "verified": False},
        {"$set": {
            "verified": True,
            "verified_by": accountant_id,
            "verified_at": datetime.utcnow(),
            "rejection_reason": None,
        }},
    )
    return result.modified_count == 1


async def reject_transaction(tx_id: ObjectId, reason: str) -> bool:
    db = get_db()
    result = await db.transactions.update_one(
        {"_id": tx_id},
        {"$set": {
            "verified": False,
            "rejection_reason": reason,
            "rejected": True,
        }},
    )
    return result.modified_count == 1


async def get_pending_count() -> int:
    db = get_db()
    return await db.transactions.count_documents({"verified": False, "rejected": {"$ne": True}})


async def get_overview_kpis() -> dict:
    """Aggregate real counts for the 4 Overview KPI cards."""
    today = date.today()
    month_start = datetime(today.year, today.month, 1)
    year_start = datetime(today.year, 1, 1)
    year_end = datetime(today.year, 12, 31, 23, 59, 59)

    db = get_db()
    pending_count, ytd_res, month_res = await asyncio.gather(
        db.transactions.count_documents({"verified": False, "rejected": {"$ne": True}}),
        db.transactions.aggregate([
            {"$match": {"verified": True, "date": {"$gte": year_start, "$lte": year_end}}},
            {"$group": {
                "_id": None,
                "count": {"$sum": 1},
                "tzs_total": {
                    "$sum": {"$cond": [{"$eq": ["$currency", "TZS"]}, "$amount", 0]}
                },
                "usd_total": {
                    "$sum": {"$cond": [{"$eq": ["$currency", "USD"]}, "$amount", 0]}
                },
            }},
        ]).to_list(1),
        db.transactions.aggregate([
            {"$match": {"date": {"$gte": month_start}}},
            {"$group": {
                "_id": None,
                "verified": {"$sum": {"$cond": ["$verified", 1, 0]}},
                "total": {"$sum": 1},
            }},
        ]).to_list(1),
    )

    ytd = ytd_res[0] if ytd_res else {"count": 0, "tzs_total": 0.0, "usd_total": 0.0}
    month = month_res[0] if month_res else {"verified": 0, "total": 0}

    return {
        "pending_count": pending_count,
        "master_count": ytd.get("count", 0),
        "verified_this_month": month.get("verified", 0),
        "submitted_this_month": month.get("total", 0),
        "total_tzs_ytd": ytd.get("tzs_total", 0.0),
        "total_usd_ytd": ytd.get("usd_total", 0.0),
    }


# ── Inbox filtered queries ────────────────────────────────────────────────────

def _build_inbox_query(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    edited: Optional[bool] = None,
) -> dict:
    query: dict = {"verified": False, "rejected": {"$ne": True}}
    # edited=False  → "New" tab: fresh entries never edited-after-verification
    #                 (matches both False and missing field on legacy docs)
    # edited=True   → "Edited" tab: rows the cashier changed after approval
    # edited=None   → no edited filter (all unverified)
    if edited is True:
        query["edited_after_verification"] = True
    elif edited is False:
        query["edited_after_verification"] = {"$ne": True}
    if search.strip():
        query["description"] = {"$regex": re.escape(search.strip()), "$options": "i"}
    if truck.strip():
        query["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    if cashier_id:
        query["cashier_id"] = cashier_id
    if date_from or date_to:
        df: dict = {}
        if date_from:
            df["$gte"] = date_from
        if date_to:
            df["$lte"] = date_to
        query["date"] = df
    return query


async def get_unverified_filtered(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 25,
    skip: int = 0,
    edited: Optional[bool] = None,
) -> List[Transaction]:
    db = get_db()
    query = _build_inbox_query(search, truck, cashier_id, date_from, date_to, edited)
    cursor = (
        db.transactions.find(query)
        .sort([("date", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def count_unverified_filtered(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    edited: Optional[bool] = None,
) -> int:
    db = get_db()
    query = _build_inbox_query(search, truck, cashier_id, date_from, date_to, edited)
    return await db.transactions.count_documents(query)


# ── Edited-after-verification queries (accountant "Edited" sub-tab) ───────────

async def get_edited_transactions(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 25,
    skip: int = 0,
) -> List[Transaction]:
    """Rows the cashier edited after they had been approved (verified=False AND
    edited_after_verification=True). Sorted by most-recently-edited first."""
    db = get_db()
    query = _build_inbox_query(search, truck, cashier_id, date_from, date_to, edited=True)
    cursor = (
        db.transactions.find(query)
        .sort([("last_edited_at", -1), ("date", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def count_edited_transactions(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    db = get_db()
    query = _build_inbox_query(search, truck, cashier_id, date_from, date_to, edited=True)
    return await db.transactions.count_documents(query)


async def get_edited_count() -> int:
    """Total edited-after-verification rows awaiting re-approval (for the badge)."""
    db = get_db()
    return await db.transactions.count_documents(
        {"verified": False, "edited_after_verification": True, "rejected": {"$ne": True}}
    )


# ── Rejected queries ──────────────────────────────────────────────────────────

def _build_rejected_query(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    query: dict = {"rejected": True}
    if search.strip():
        query["description"] = {"$regex": re.escape(search.strip()), "$options": "i"}
    if truck.strip():
        query["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    if cashier_id:
        query["cashier_id"] = cashier_id
    if date_from or date_to:
        df: dict = {}
        if date_from:
            df["$gte"] = date_from
        if date_to:
            df["$lte"] = date_to
        query["date"] = df
    return query


async def get_rejected_transactions(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 25,
    skip: int = 0,
) -> List[Transaction]:
    db = get_db()
    query = _build_rejected_query(search, truck, cashier_id, date_from, date_to)
    cursor = (
        db.transactions.find(query)
        .sort([("date", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def count_rejected_transactions(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    db = get_db()
    query = _build_rejected_query(search, truck, cashier_id, date_from, date_to)
    return await db.transactions.count_documents(query)


async def get_rejected_count() -> int:
    """Total rejected entries across all cashiers (for the badge)."""
    db = get_db()
    return await db.transactions.count_documents({"rejected": True})


async def return_to_inbox(tx_id: ObjectId) -> bool:
    """Undo a rejection — clears rejected flag so the entry reappears in its original inbox tab."""
    db = get_db()
    result = await db.transactions.update_one(
        {"_id": tx_id, "rejected": True},
        {"$set": {"rejected": False, "rejection_reason": None}},
    )
    return result.modified_count == 1


# ── Re-approve (edited entries) ───────────────────────────────────────────────

_CASCADE_FIELDS = {
    "date", "description", "truck_number", "amount", "currency",
    "lpo_do", "do_number", "memo", "receipt_status", "notes_flag",
    "ownership", "approver", "category_name", "category_id", "item",
    "category_confidence", "month", "year",
}


async def re_approve_transaction(tx_id: ObjectId, accountant_id: ObjectId) -> bool:
    """Approve an edited row.

    If the pending-edit document carries an original_transaction_id, the edited
    values cascade to the original approved record and the pending doc is deleted.
    Otherwise (legacy in-place edits) the document is flipped verified=True directly.
    """
    db = get_db()
    pending_doc = await db.transactions.find_one({"_id": tx_id, "verified": False})
    if not pending_doc:
        return False

    original_id = pending_doc.get("original_transaction_id")

    if original_id:
        updates = {k: pending_doc[k] for k in _CASCADE_FIELDS if k in pending_doc}
        updates.update({
            "verified": True,
            "verified_by": accountant_id,
            "verified_at": datetime.utcnow(),
            "rejection_reason": None,
            "rejected": False,
            "edited_after_verification": False,
            "last_edited_at": pending_doc.get("last_edited_at"),
            "last_edited_by": pending_doc.get("last_edited_by"),
        })
        result = await db.transactions.update_one({"_id": original_id}, {"$set": updates})
        if result.modified_count == 1:
            await db.transactions.delete_one({"_id": tx_id})
            return True
        return False

    result = await db.transactions.update_one(
        {"_id": tx_id, "verified": False},
        {"$set": {
            "verified": True,
            "verified_by": accountant_id,
            "verified_at": datetime.utcnow(),
            "rejection_reason": None,
            "rejected": False,
            "edited_after_verification": False,
        }},
    )
    return result.modified_count == 1


async def bulk_re_approve_transactions(
    tx_ids: List[ObjectId], accountant_id: ObjectId
) -> int:
    if not tx_ids:
        return 0
    db = get_db()
    docs = await db.transactions.find(
        {"_id": {"$in": tx_ids}, "verified": False},
        {"_id": 1, "original_transaction_id": 1},
    ).to_list(length=None)

    cascade_ids = [d["_id"] for d in docs if d.get("original_transaction_id")]
    simple_ids = [d["_id"] for d in docs if not d.get("original_transaction_id")]

    count = 0
    for tx_id in cascade_ids:
        if await re_approve_transaction(tx_id, accountant_id):
            count += 1

    if simple_ids:
        result = await db.transactions.update_many(
            {"_id": {"$in": simple_ids}, "verified": False},
            {"$set": {
                "verified": True,
                "verified_by": accountant_id,
                "verified_at": datetime.utcnow(),
                "rejection_reason": None,
                "rejected": False,
                "edited_after_verification": False,
            }},
        )
        count += result.modified_count

    return count


async def get_unverified_trucks() -> List[str]:
    db = get_db()
    vals = await db.transactions.distinct("truck_number", {"verified": False, "rejected": {"$ne": True}})
    return sorted(v for v in vals if v)


async def get_unverified_cashier_ids() -> List[ObjectId]:
    db = get_db()
    vals = await db.transactions.distinct("cashier_id", {"verified": False, "rejected": {"$ne": True}})
    return [v for v in vals if v is not None]


async def get_rejected_trucks() -> List[str]:
    db = get_db()
    vals = await db.transactions.distinct("truck_number", {"rejected": True})
    return sorted(v for v in vals if v)


async def get_rejected_cashier_ids() -> List[ObjectId]:
    db = get_db()
    vals = await db.transactions.distinct("cashier_id", {"rejected": True})
    return [v for v in vals if v is not None]


async def get_cashier_names(cashier_ids: List[ObjectId]) -> Dict[ObjectId, str]:
    if not cashier_ids:
        return {}
    db = get_db()
    docs = await db.users.find(
        {"_id": {"$in": cashier_ids}},
        {"_id": 1, "full_name": 1},
    ).to_list(length=None)
    return {doc["_id"]: doc.get("full_name", str(doc["_id"])) for doc in docs}


async def bulk_approve_transactions(
    tx_ids: List[ObjectId], accountant_id: ObjectId
) -> int:
    if not tx_ids:
        return 0
    db = get_db()
    result = await db.transactions.update_many(
        {"_id": {"$in": tx_ids}, "verified": False},
        {"$set": {
            "verified": True,
            "verified_by": accountant_id,
            "verified_at": datetime.utcnow(),
            "rejection_reason": None,
        }},
    )
    return result.modified_count


async def update_transaction_category(
    tx_id: ObjectId,
    category_name: str,
    category_id: Optional[ObjectId] = None,
) -> bool:
    db = get_db()
    # Keep item and category_name in sync — they represent the same thing.
    update: dict = {"category_name": category_name, "item": category_name}
    if category_id:
        update["category_id"] = category_id
    result = await db.transactions.update_one({"_id": tx_id}, {"$set": update})
    return result.modified_count == 1


# ── Master Expenses Table queries ────────────────────────────────────────────


def _build_master_query(
    year: Optional[int],
    month: int,          # 0 = all, 1‑12 = month index
    search: str,
    truck: str,
    category: str,
    receipt: str,
    description: str = "",   # exact-ish sub-route filter (description contains)
) -> dict:
    _year = year or date.today().year
    if month and 1 <= month <= 12:
        last_day = calendar.monthrange(_year, month)[1]
        date_filter: dict = {
            "$gte": datetime(_year, month, 1),
            "$lte": datetime(_year, month, last_day, 23, 59, 59),
        }
    else:
        date_filter = {
            "$gte": datetime(_year, 1, 1),
            "$lte": datetime(_year, 12, 31, 23, 59, 59),
        }

    query: dict = {"verified": True, "date": date_filter}

    # Both `search` and `description` constrain the description field; combine
    # them with $and so a sub-route filter and a free-text search can coexist.
    desc_conditions = []
    if search.strip():
        desc_conditions.append({"$regex": re.escape(search.strip()), "$options": "i"})
    if description.strip():
        desc_conditions.append({"$regex": re.escape(description.strip()), "$options": "i"})
    if len(desc_conditions) == 1:
        query["description"] = desc_conditions[0]
    elif len(desc_conditions) == 2:
        query["$and"] = [{"description": c} for c in desc_conditions]

    if truck.strip():
        query["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    if category.strip():
        query["category_name"] = category.strip()
    if receipt.strip() and receipt != "all":
        query["receipt_status"] = receipt.strip()
    return query


async def get_master_transactions(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    sort_field: str = "date",
    sort_asc: bool = False,
    limit: int = 50,
    skip: int = 0,
) -> List[Transaction]:
    db = get_db()
    query = _build_master_query(year, month, search, truck, category, receipt, description)
    direction = 1 if sort_asc else -1
    cursor = (
        db.transactions.find(query)
        .sort([(sort_field, direction), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def count_master_transactions(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
) -> int:
    db = get_db()
    query = _build_master_query(year, month, search, truck, category, receipt, description)
    return await db.transactions.count_documents(query)


async def get_master_totals(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
) -> dict:
    """Aggregate TZS + USD totals for the current filter (all pages, not just current)."""
    db = get_db()
    query = _build_master_query(year, month, search, truck, category, receipt, description)
    result = await db.transactions.aggregate([
        {"$match": query},
        {"$group": {
            "_id": None,
            "tzs_total": {"$sum": {"$cond": [{"$eq": ["$currency", "TZS"]}, "$amount", 0]}},
            "usd_total": {"$sum": {"$cond": [{"$eq": ["$currency", "USD"]}, "$amount", 0]}},
        }},
    ]).to_list(1)
    if result:
        return {"tzs": result[0]["tzs_total"], "usd": result[0]["usd_total"]}
    return {"tzs": 0.0, "usd": 0.0}


async def get_master_month_totals(year: int) -> Dict:
    """Per-month TZS totals for the year, used by the month tab bar."""
    db = get_db()
    _year = year
    pipeline = [
        {"$match": {
            "verified": True,
            "date": {
                "$gte": datetime(_year, 1, 1),
                "$lte": datetime(_year, 12, 31, 23, 59, 59),
            },
        }},
        {"$group": {
            "_id": {"$month": "$date"},
            "tzs": {"$sum": {"$cond": [{"$eq": ["$currency", "TZS"]}, "$amount", 0]}},
            "usd": {"$sum": {"$cond": [{"$eq": ["$currency", "USD"]}, "$amount", 0]}},
            "count": {"$sum": 1},
        }},
    ]
    docs = await db.transactions.aggregate(pipeline).to_list(12)
    return {doc["_id"]: {"tzs": doc["tzs"], "usd": doc["usd"], "count": doc["count"]}
            for doc in docs}


async def get_master_trucks(year: Optional[int] = None) -> List[str]:
    db = get_db()
    _year = year or date.today().year
    query = {
        "verified": True,
        "date": {"$gte": datetime(_year, 1, 1), "$lte": datetime(_year, 12, 31, 23, 59, 59)},
    }
    vals = await db.transactions.distinct("truck_number", query)
    return sorted(v for v in vals if v)


async def get_master_categories(year: Optional[int] = None) -> List[str]:
    db = get_db()
    _year = year or date.today().year
    query = {
        "verified": True,
        "date": {"$gte": datetime(_year, 1, 1), "$lte": datetime(_year, 12, 31, 23, 59, 59)},
    }
    vals = await db.transactions.distinct("category_name", query)
    return sorted(v for v in vals if v)


# ── Diesel Cash (cashier-fed, verified transactions) ─────────────────────────

DIESEL_CASH_CATEGORY = "Diesel Cash"

# Canonical cashier item name for diesel cash. Matched case-insensitively so
# minor casing variations still land in the Diesel Cash tab.
DIESEL_CASH_CATEGORIES = ("Diesel Cash",)

_MONTH_NAMES = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _diesel_cash_name_filter() -> dict:
    """Case-insensitive exact match against any known diesel-cash item name."""
    alternation = "|".join(re.escape(n) for n in DIESEL_CASH_CATEGORIES)
    return {"$regex": f"^(?:{alternation})$", "$options": "i"}


def _build_diesel_cash_query(
    year: Optional[int],
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
) -> dict:
    _year = year or date.today().year
    if month and 1 <= month <= 12:
        last_day = calendar.monthrange(_year, month)[1]
        date_filter: dict = {
            "$gte": datetime(_year, month, 1),
            "$lte": datetime(_year, month, last_day, 23, 59, 59),
        }
    else:
        date_filter = {
            "$gte": datetime(_year, 1, 1),
            "$lte": datetime(_year, 12, 31, 23, 59, 59),
        }

    query: dict = {
        "verified": True,
        "category_name": _diesel_cash_name_filter(),
        "date": date_filter,
    }
    if search.strip():
        query["description"] = {"$regex": re.escape(search.strip()), "$options": "i"}
    if truck.strip():
        query["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    if receipt.strip() and receipt != "all":
        query["receipt_status"] = receipt.strip()
    return query


async def get_diesel_cash_month_summaries(year: int) -> list:
    """One summary row per calendar month for verified Diesel Cash transactions."""
    db = get_db()
    pipeline = [
        {"$match": {
            "verified": True,
            "category_name": _diesel_cash_name_filter(),
            "date": {
                "$gte": datetime(year, 1, 1),
                "$lte": datetime(year, 12, 31, 23, 59, 59),
            },
        }},
        {"$group": {
            "_id": {"$month": "$date"},
            "record_count": {"$sum": 1},
            "tzs_total": {
                "$sum": {"$cond": [{"$eq": ["$currency", "TZS"]}, "$amount", 0]},
            },
            "usd_total": {
                "$sum": {"$cond": [{"$eq": ["$currency", "USD"]}, "$amount", 0]},
            },
            "min_date": {"$min": "$date"},
            "max_date": {"$max": "$date"},
        }},
        {"$sort": {"_id": 1}},
    ]
    docs = await db.transactions.aggregate(pipeline).to_list(length=12)
    summaries = []
    for doc in docs:
        month_idx = int(doc["_id"])
        min_d = doc.get("min_date")
        max_d = doc.get("max_date")
        summaries.append({
            "month": month_idx,
            "month_name": _MONTH_NAMES[month_idx] if 1 <= month_idx <= 12 else str(month_idx),
            "record_count": int(doc.get("record_count", 0)),
            "tzs_total": float(doc.get("tzs_total", 0)),
            "usd_total": float(doc.get("usd_total", 0)),
            "min_date": min_d,
            "max_date": max_d,
        })
    return summaries


async def get_diesel_cash_transactions(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
    sort_field: str = "date",
    sort_asc: bool = False,
    limit: int = 50,
    skip: int = 0,
) -> List[Transaction]:
    db = get_db()
    query = _build_diesel_cash_query(year, month, search, truck, receipt)
    direction = 1 if sort_asc else -1
    cursor = (
        db.transactions.find(query)
        .sort([(sort_field, direction), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def count_diesel_cash_transactions(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
) -> int:
    db = get_db()
    query = _build_diesel_cash_query(year, month, search, truck, receipt)
    return await db.transactions.count_documents(query)


async def get_diesel_cash_totals(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
) -> dict:
    db = get_db()
    query = _build_diesel_cash_query(year, month, search, truck, receipt)
    result = await db.transactions.aggregate([
        {"$match": query},
        {"$group": {
            "_id": None,
            "tzs_total": {"$sum": {"$cond": [{"$eq": ["$currency", "TZS"]}, "$amount", 0]}},
            "usd_total": {"$sum": {"$cond": [{"$eq": ["$currency", "USD"]}, "$amount", 0]}},
        }},
    ]).to_list(1)
    if result:
        return {"tzs": result[0]["tzs_total"], "usd": result[0]["usd_total"]}
    return {"tzs": 0.0, "usd": 0.0}


# ── stubs (diesel / reconciliation) ──────────────────────────────────────────

async def get_diesel_entries(
    station: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    return []


async def get_reconciliation_entries(
    entity: Optional[str] = None,
    station: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    return []


# ── Imported feeds (toll_plaza / parking_congo / zambia_parking) ──────────────

def _build_feed_query(feed_type: str, search: str = "", extra: str = "") -> dict:
    query: dict = {"feed_type": feed_type}
    if search.strip():
        query["$or"] = [
            {"vehicle_reg":  {"$regex": re.escape(search.strip()), "$options": "i"}},
            {"toll_plaza":   {"$regex": re.escape(search.strip()), "$options": "i"}},
            {"receipt_no":   {"$regex": re.escape(search.strip()), "$options": "i"}},
            {"vehicle_no":   {"$regex": re.escape(search.strip()), "$options": "i"}},
            {"serial":       {"$regex": re.escape(search.strip()), "$options": "i"}},
            {"plate_num":    {"$regex": re.escape(search.strip()), "$options": "i"}},
            {"ticket_no":    {"$regex": re.escape(search.strip()), "$options": "i"}},
            {"heading_to":   {"$regex": re.escape(search.strip()), "$options": "i"}},
        ]
    if extra.strip():
        query["toll_plaza"] = extra.strip()
    return query


async def get_imported_feed(
    feed_type: str,
    search: str = "",
    extra: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    db = get_db()
    query = _build_feed_query(feed_type, search, extra)
    cursor = db.imported_feeds.find(query).sort("import_date", -1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def count_imported_feed(feed_type: str, search: str = "", extra: str = "") -> int:
    db = get_db()
    query = _build_feed_query(feed_type, search, extra)
    return await db.imported_feeds.count_documents(query)


def _safe_double(field: str) -> dict:
    """$convert expression that safely coerces a string field to double.

    Handles null, missing, and empty-string values — all yield 0.0 rather
    than raising ConversionFailure (which $toDouble does on empty strings).
    """
    return {"$convert": {"input": f"${field}", "to": "double", "onError": 0.0, "onNull": 0.0}}


async def get_imported_feed_totals(feed_type: str) -> dict:
    db = get_db()
    pipeline = [
        {"$match": {"feed_type": feed_type}},
        {"$group": {
            "_id": None,
            "tender_amount": {"$sum": _safe_double("tender_amount")},
            "amount":        {"$sum": _safe_double("amount")},
            "debit":         {"$sum": _safe_double("debit")},
            "credit":        {"$sum": _safe_double("credit")},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"tender_amount": 0.0, "amount": 0.0, "debit": 0.0, "credit": 0.0}


async def get_existing_feed_keys(keys: List[str]) -> set:
    """Return the set of dedup key values already in the imported_feeds collection."""
    if not keys:
        return set()
    db = get_db()
    docs = await db.imported_feeds.find(
        {"$or": [
            {"receipt_no":  {"$in": keys}},
            {"serial":      {"$in": keys}},
            {"ticket_no":   {"$in": keys}},
            {"lpo_no":      {"$in": keys}},
            {"ledger_id":   {"$in": keys}},
            {"trip_number": {"$in": keys}},
        ]},
        {"receipt_no": 1, "serial": 1, "ticket_no": 1, "lpo_no": 1,
         "ledger_id": 1, "trip_number": 1},
    ).to_list(length=None)
    found: set = set()
    for doc in docs:
        for field in ("receipt_no", "serial", "ticket_no", "lpo_no", "ledger_id", "trip_number"):
            v = doc.get(field)
            if v:
                found.add(v)
    return found


async def save_imported_feed(records: list) -> int:
    """Insert a batch of imported feed records; returns the count inserted.

    Records are expected to carry upload_id and source_filename already (set by
    ImportDialog before calling this function).
    """
    if not records:
        return 0
    db = get_db()
    now = datetime.utcnow()
    docs = []
    for rec in records:
        doc = dict(rec)
        doc.pop("_raw", None)
        doc["import_date"] = now
        docs.append(doc)
    result = await db.imported_feeds.insert_many(docs, ordered=False)
    return len(result.inserted_ids)


async def get_toll_plaza_uploads() -> list:
    """Return one summary doc per upload batch for the toll_plaza feed."""
    db = get_db()
    pipeline = [
        {"$match": {"feed_type": "toll_plaza"}},
        {"$group": {
            "_id":             "$upload_id",
            "source_filename": {"$first": "$source_filename"},
            "import_date":     {"$first": "$import_date"},
            "record_count":    {"$sum": 1},
            "total_zmw":       {"$sum": _safe_double("tender_amount")},
            "min_toll_date":   {"$min": "$toll_date"},
            "max_toll_date":   {"$max": "$toll_date"},
        }},
        {"$sort": {"import_date": -1}},
    ]
    return await db.imported_feeds.aggregate(pipeline).to_list(length=None)


async def get_toll_plaza_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated records for a single toll plaza upload batch."""
    db = get_db()
    query: dict = {"feed_type": "toll_plaza", "upload_id": upload_id}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"vehicle_reg":  {"$regex": s, "$options": "i"}},
            {"toll_plaza":   {"$regex": s, "$options": "i"}},
            {"receipt_no":   {"$regex": s, "$options": "i"}},
            {"cashier_name": {"$regex": s, "$options": "i"}},
            {"client_name":  {"$regex": s, "$options": "i"}},
        ]
    cursor = db.imported_feeds.find(query).sort("toll_date", 1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def count_toll_plaza_upload_records(upload_id: str, search: str = "") -> int:
    """Count records for a single toll plaza upload batch."""
    db = get_db()
    query: dict = {"feed_type": "toll_plaza", "upload_id": upload_id}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"vehicle_reg":  {"$regex": s, "$options": "i"}},
            {"toll_plaza":   {"$regex": s, "$options": "i"}},
            {"receipt_no":   {"$regex": s, "$options": "i"}},
            {"cashier_name": {"$regex": s, "$options": "i"}},
            {"client_name":  {"$regex": s, "$options": "i"}},
        ]
    return await db.imported_feeds.count_documents(query)


async def get_parking_congo_uploads() -> list:
    """Return one summary doc per upload batch for the parking_congo feed."""
    db = get_db()
    pipeline = [
        {"$match": {"feed_type": "parking_congo"}},
        {"$group": {
            "_id":             "$upload_id",
            "source_filename": {"$first": "$source_filename"},
            "import_date":     {"$first": "$import_date"},
            "record_count":    {"$sum": 1},
            "min_date":        {"$min": "$payment_date"},
            "max_date":        {"$max": "$payment_date"},
        }},
        {"$sort": {"import_date": -1}},
    ]
    return await db.imported_feeds.aggregate(pipeline).to_list(length=None)


async def get_parking_congo_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated records for a single parking_congo upload batch."""
    db = get_db()
    query: dict = {"feed_type": "parking_congo", "upload_id": upload_id}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"vehicle_no":          {"$regex": s, "$options": "i"}},
            {"ledger_id":           {"$regex": s, "$options": "i"}},
            {"transaction_type":    {"$regex": s, "$options": "i"}},
            {"cashier":             {"$regex": s, "$options": "i"}},
            {"transaction_details": {"$regex": s, "$options": "i"}},
        ]
    cursor = db.imported_feeds.find(query).sort("payment_date", -1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def count_parking_congo_upload_records(upload_id: str, search: str = "") -> int:
    """Count records for a single parking_congo upload batch."""
    db = get_db()
    query: dict = {"feed_type": "parking_congo", "upload_id": upload_id}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"vehicle_no":          {"$regex": s, "$options": "i"}},
            {"ledger_id":           {"$regex": s, "$options": "i"}},
            {"transaction_type":    {"$regex": s, "$options": "i"}},
            {"cashier":             {"$regex": s, "$options": "i"}},
            {"transaction_details": {"$regex": s, "$options": "i"}},
        ]
    return await db.imported_feeds.count_documents(query)


# ── RahnTech — transacted devices import ─────────────────────────────────────

async def get_rahntech_uploads() -> list:
    """Return one summary doc per upload batch for the rahntech feed."""
    db = get_db()
    pipeline = [
        {"$match": {
            "feed_type": "rahntech",
            "upload_id": {"$exists": True, "$ne": ""},
        }},
        {"$group": {
            "_id":             "$upload_id",
            "source_filename": {"$first": "$source_filename"},
            "import_date":     {"$first": "$import_date"},
            "record_count":    {"$sum": 1},
            "min_sales_date":  {"$min": "$sales_date"},
            "max_sales_date":  {"$max": "$sales_date"},
        }},
        {"$sort": {"import_date": -1}},
    ]
    return await db.imported_feeds.aggregate(pipeline).to_list(length=None)


async def get_rahntech_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated records for a single RahnTech upload batch."""
    db = get_db()
    query: dict = {"feed_type": "rahntech", "upload_id": upload_id}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"truck_number":  {"$regex": s, "$options": "i"}},
            {"driver_name":   {"$regex": s, "$options": "i"}},
            {"trip_number":   {"$regex": s, "$options": "i"}},
            {"device_number": {"$regex": s, "$options": "i"}},
            {"do_number":     {"$regex": s, "$options": "i"}},
        ]
    cursor = db.imported_feeds.find(query).sort("sales_date", -1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def count_rahntech_upload_records(upload_id: str, search: str = "") -> int:
    """Count records for a single RahnTech upload batch."""
    db = get_db()
    query: dict = {"feed_type": "rahntech", "upload_id": upload_id}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"truck_number":  {"$regex": s, "$options": "i"}},
            {"driver_name":   {"$regex": s, "$options": "i"}},
            {"trip_number":   {"$regex": s, "$options": "i"}},
            {"device_number": {"$regex": s, "$options": "i"}},
            {"do_number":     {"$regex": s, "$options": "i"}},
        ]
    return await db.imported_feeds.count_documents(query)


# ── Zambia Parking — weekly statement import (sheet tab = week label) ─────────

async def zambia_sheet_exists(sheet_label: str) -> bool:
    """True when a weekly sheet tab was already imported."""
    if not sheet_label.strip():
        return False
    db = get_db()
    count = await db.imported_feeds.count_documents({
        "feed_type":   "zambia_parking",
        "sheet_label": sheet_label.strip(),
    })
    return count > 0


async def zambia_existing_sheet_labels(labels: List[str]) -> set:
    """Return sheet tab names from labels that were already imported."""
    clean = [l.strip() for l in labels if l and l.strip()]
    if not clean:
        return set()
    db = get_db()
    found = await db.imported_feeds.distinct(
        "sheet_label",
        {"feed_type": "zambia_parking", "sheet_label": {"$in": clean}},
    )
    return set(found)


async def get_zambia_parking_uploads() -> list:
    """Return one summary doc per Zambia Parking import batch."""
    db = get_db()
    pipeline = [
        {"$match": {
            "feed_type": "zambia_parking",
            "upload_id": {"$exists": True, "$ne": ""},
        }},
        {"$sort": {"row_index": 1}},
        {"$group": {
            "_id":             "$upload_id",
            "sheet_label":     {"$first": "$sheet_label"},
            "source_filename": {"$first": "$source_filename"},
            "import_date":     {"$first": "$import_date"},
            "record_count":    {"$sum": 1},
            "total_debit":     {"$sum": _safe_double("debit")},
            "total_credit":    {"$sum": _safe_double("credit")},
            "closing_balance": {"$last": _safe_double("balance")},
            "min_transaction_date": {"$min": "$transaction_date"},
            "max_transaction_date": {"$max": "$transaction_date"},
        }},
        {"$sort": {"import_date": -1}},
    ]
    return await db.imported_feeds.aggregate(pipeline).to_list(length=None)


async def get_zambia_parking_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated records for a single Zambia Parking upload batch."""
    db = get_db()
    query: dict = {"feed_type": "zambia_parking", "upload_id": upload_id}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"plate_num":  {"$regex": s, "$options": "i"}},
            {"ticket_no":  {"$regex": s, "$options": "i"}},
            {"heading_to": {"$regex": s, "$options": "i"}},
            {"type":       {"$regex": s, "$options": "i"}},
            {"date":       {"$regex": s, "$options": "i"}},
        ]
    cursor = (
        db.imported_feeds
        .find(query)
        .sort([("row_index", 1), ("import_date", 1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_zambia_parking_upload_records(upload_id: str, search: str = "") -> int:
    """Count records for a single Zambia Parking upload batch."""
    db = get_db()
    query: dict = {"feed_type": "zambia_parking", "upload_id": upload_id}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"plate_num":  {"$regex": s, "$options": "i"}},
            {"ticket_no":  {"$regex": s, "$options": "i"}},
            {"heading_to": {"$regex": s, "$options": "i"}},
            {"type":       {"$regex": s, "$options": "i"}},
            {"date":       {"$regex": s, "$options": "i"}},
        ]
    return await db.imported_feeds.count_documents(query)


# ── Insurance feeds (comesa / third_party) ────────────────────────────────────

def _build_insurance_query(
    feed_type: str, search: str = "", month: str = "", status: str = ""
) -> dict:
    query: dict = {"feed_type": feed_type}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"name":      {"$regex": s, "$options": "i"}},
            {"card_no":   {"$regex": s, "$options": "i"}},
            {"truck_reg": {"$regex": s, "$options": "i"}},
            {"reg_no":    {"$regex": s, "$options": "i"}},
        ]
    if month.strip() and month.upper() != "ALL":
        query["month"] = {"$regex": re.escape(month.strip()), "$options": "i"}
    if status.strip() and status.upper() != "ALL":
        query["status"] = status.strip()
    return query


async def get_insurance_feed(
    feed_type: str,
    search: str = "",
    month: str = "",
    status: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    db = get_db()
    query = _build_insurance_query(feed_type, search, month, status)
    cursor = db.imported_feeds.find(query).sort(
        [("month", 1), ("name", 1)]
    ).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def count_insurance_feed(
    feed_type: str, search: str = "", month: str = "", status: str = ""
) -> int:
    db = get_db()
    query = _build_insurance_query(feed_type, search, month, status)
    return await db.imported_feeds.count_documents(query)


async def get_insurance_totals(
    feed_type: str, month: str = "", status: str = ""
) -> dict:
    db = get_db()
    match: dict = {"feed_type": feed_type}
    if month.strip() and month.upper() != "ALL":
        match["month"] = {"$regex": re.escape(month.strip()), "$options": "i"}
    if status.strip() and status.upper() != "ALL":
        match["status"] = status.strip()
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": None,
            "premium":       {"$sum": {"$toDouble": {"$ifNull": ["$premium", 0]}}},
            "vat":           {"$sum": {"$toDouble": {"$ifNull": ["$vat", 0]}}},
            "total_premium": {"$sum": {"$toDouble": {"$ifNull": ["$total_premium", 0]}}},
            "count":         {"$sum": 1},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"premium": 0.0, "vat": 0.0, "total_premium": 0.0, "count": 0}


async def get_existing_insurance_keys(feed_type: str, keys: List[str]) -> set:
    """Return dedup_id values already stored for this insurance feed type."""
    if not keys:
        return set()
    db = get_db()
    docs = await db.imported_feeds.find(
        {"feed_type": feed_type, "dedup_id": {"$in": keys}},
        {"dedup_id": 1},
    ).to_list(length=None)
    return {d["dedup_id"] for d in docs if "dedup_id" in d}


# ── Diesel fuel feeds (infinity / lake_zambia / lake_tunduma / gbp) ──────────

async def get_diesel_feed(
    feed_type: str,
    search: str = "",
    truck: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    db = get_db()
    query: dict = {"feed_type": feed_type}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"truck_no":     {"$regex": s, "$options": "i"}},
            {"lpo_no":       {"$regex": s, "$options": "i"}},
            {"clients_name": {"$regex": s, "$options": "i"}},
            {"destinations": {"$regex": s, "$options": "i"}},
            {"do_sdo_no":    {"$regex": s, "$options": "i"}},
        ]
    if truck.strip():
        query["truck_no"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    cursor = db.imported_feeds.find(query).sort("import_date", -1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def count_diesel_feed(feed_type: str, search: str = "", truck: str = "") -> int:
    db = get_db()
    query: dict = {"feed_type": feed_type}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"truck_no":     {"$regex": s, "$options": "i"}},
            {"lpo_no":       {"$regex": s, "$options": "i"}},
            {"clients_name": {"$regex": s, "$options": "i"}},
            {"destinations": {"$regex": s, "$options": "i"}},
        ]
    if truck.strip():
        query["truck_no"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    return await db.imported_feeds.count_documents(query)


async def get_diesel_totals(feed_type: str) -> dict:
    db = get_db()
    pipeline = [
        {"$match": {"feed_type": feed_type}},
        {"$group": {
            "_id":          None,
            "ltrs":         {"$sum": _safe_double("ltrs")},
            "total_amount": {"$sum": _safe_double("total_amount")},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"ltrs": 0.0, "total_amount": 0.0}


# ── Diesel feed — per-upload batch browse/detail ─────────────────────────────
#  Each Import tags its rows with a shared upload_id + source_filename +
#  sheet_label so the fuel pages can browse imports as batches (like Toll
#  Plaza / Parking Congo) and drill into a single upload's records.

async def get_diesel_uploads(feed_type: str) -> list:
    """Return one summary doc per import batch for a diesel feed_type."""
    db = get_db()
    pipeline = [
        {"$match": {
            "feed_type": feed_type,
            "upload_id": {"$exists": True, "$ne": ""},
        }},
        {"$group": {
            "_id":             "$upload_id",
            "source_filename": {"$first": "$source_filename"},
            "sheet_label":     {"$first": "$sheet_label"},
            "import_date":     {"$first": "$import_date"},
            "record_count":    {"$sum": 1},
            "ltrs":            {"$sum": _safe_double("ltrs")},
            "total_amount":    {"$sum": _safe_double("total_amount")},
            "min_date":        {"$min": "$date"},
            "max_date":        {"$max": "$date"},
        }},
        {"$sort": {"import_date": -1}},
    ]
    return await db.imported_feeds.aggregate(pipeline).to_list(length=None)


def _diesel_records_query(feed_type: str, upload_id: str, search: str) -> dict:
    query: dict = {"feed_type": feed_type, "upload_id": upload_id}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"truck_no":     {"$regex": s, "$options": "i"}},
            {"lpo_no":       {"$regex": s, "$options": "i"}},
            {"do_sdo_no":    {"$regex": s, "$options": "i"}},
            {"clients_name": {"$regex": s, "$options": "i"}},
            {"destinations": {"$regex": s, "$options": "i"}},
            {"diesel_at":    {"$regex": s, "$options": "i"}},
        ]
    return query


async def get_diesel_upload_records(
    feed_type: str,
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated records for a single diesel upload batch."""
    db = get_db()
    query = _diesel_records_query(feed_type, upload_id, search)
    cursor = db.imported_feeds.find(query).sort("date", 1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def count_diesel_upload_records(
    feed_type: str, upload_id: str, search: str = "",
) -> int:
    """Count records for a single diesel upload batch."""
    db = get_db()
    return await db.imported_feeds.count_documents(
        _diesel_records_query(feed_type, upload_id, search)
    )


async def get_diesel_upload_totals(
    feed_type: str, upload_id: str, search: str = "",
) -> dict:
    """Ltrs + amount totals for the (optionally searched) upload batch."""
    db = get_db()
    pipeline = [
        {"$match": _diesel_records_query(feed_type, upload_id, search)},
        {"$group": {
            "_id":          None,
            "ltrs":         {"$sum": _safe_double("ltrs")},
            "total_amount": {"$sum": _safe_double("total_amount")},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"ltrs": 0.0, "total_amount": 0.0}


async def delete_diesel_upload(feed_type: str, upload_id: str) -> int:
    """Delete every record belonging to one diesel upload batch."""
    db = get_db()
    result = await db.imported_feeds.delete_many(
        {"feed_type": feed_type, "upload_id": upload_id}
    )
    return result.deleted_count


# ── Ahmed Kimvi — Excel import (last sheet per workbook) ─────────────────────

async def kimvi_sheet_exists(sheet_label: str) -> bool:
    """True when a sheet with this exact tab name was already imported."""
    if not sheet_label.strip():
        return False
    db = get_db()
    count = await db.separate_expenses.count_documents({
        "expense_type": "ahmed_kimvi",
        "sheet_label":  sheet_label.strip(),
    })
    return count > 0


async def kimvi_existing_sheet_labels(labels: List[str]) -> set:
    """Return sheet tab names from labels that were already imported."""
    clean = [l.strip() for l in labels if l and l.strip()]
    if not clean:
        return set()
    db = get_db()
    found = await db.separate_expenses.distinct(
        "sheet_label",
        {"expense_type": "ahmed_kimvi", "sheet_label": {"$in": clean}},
    )
    return set(found)


async def save_kimvi_import(records: list) -> int:
    """Insert a batch of Ahmed Kimvi rows from an Excel import."""
    if not records:
        return 0
    db = get_db()
    now = datetime.utcnow()
    docs = []
    for rec in records:
        doc = dict(rec)
        doc.pop("_raw", None)
        doc["expense_type"] = "ahmed_kimvi"
        doc["import_date"]  = now
        doc["created_at"]   = now
        docs.append(doc)
    result = await db.separate_expenses.insert_many(docs, ordered=False)
    return len(result.inserted_ids)


async def get_kimvi_uploads() -> list:
    """Return one summary doc per import batch."""
    db = get_db()
    pipeline = [
        {"$match": {
            "expense_type": "ahmed_kimvi",
            "upload_id":    {"$exists": True, "$ne": ""},
        }},
        {"$group": {
            "_id":             "$upload_id",
            "sheet_label":     {"$first": "$sheet_label"},
            "source_filename": {"$first": "$source_filename"},
            "import_date":     {"$first": "$import_date"},
            "record_count":    {"$sum": 1},
            "balance_usd":     {"$sum": {"$ifNull": ["$amount_usd", 0]}},
            "money_in":        {"$sum": {
                "$cond": [
                    {"$lt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                    {"$ifNull": ["$amount_usd", 0]},
                    0,
                ],
            }},
            "money_out":       {"$sum": {
                "$cond": [
                    {"$gt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                    {"$ifNull": ["$amount_usd", 0]},
                    0,
                ],
            }},
            "min_expense_date": {"$min": "$expense_date"},
            "max_expense_date": {"$max": "$expense_date"},
        }},
        {"$sort": {"import_date": -1}},
    ]
    return await db.separate_expenses.aggregate(pipeline).to_list(length=None)


async def get_kimvi_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated rows for a single Ahmed Kimvi import batch."""
    db = get_db()
    query: dict = {
        "expense_type": "ahmed_kimvi",
        "upload_id":    upload_id,
    }
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"description": {"$regex": s, "$options": "i"}},
            {"truck_no":    {"$regex": s, "$options": "i"}},
            {"date_str":    {"$regex": s, "$options": "i"}},
        ]
    cursor = (
        db.separate_expenses
        .find(query)
        .sort([("row_index", 1), ("created_at", 1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_kimvi_upload_records(upload_id: str, search: str = "") -> int:
    """Count rows for a single Ahmed Kimvi import batch."""
    db = get_db()
    query: dict = {
        "expense_type": "ahmed_kimvi",
        "upload_id":    upload_id,
    }
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"description": {"$regex": s, "$options": "i"}},
            {"truck_no":    {"$regex": s, "$options": "i"}},
            {"date_str":    {"$regex": s, "$options": "i"}},
        ]
    return await db.separate_expenses.count_documents(query)


# ── Congo Expenses — Excel import (last sheet per workbook) ──────────────────

async def congo_sheet_exists(sheet_label: str) -> bool:
    """True when a sheet with this exact tab name was already imported."""
    if not sheet_label.strip():
        return False
    db = get_db()
    count = await db.separate_expenses.count_documents({
        "expense_type": "congo_expenses",
        "sheet_label":  sheet_label.strip(),
    })
    return count > 0


async def congo_existing_sheet_labels(labels: List[str]) -> set:
    """Return sheet tab names from labels that were already imported."""
    clean = [l.strip() for l in labels if l and l.strip()]
    if not clean:
        return set()
    db = get_db()
    found = await db.separate_expenses.distinct(
        "sheet_label",
        {"expense_type": "congo_expenses", "sheet_label": {"$in": clean}},
    )
    return set(found)


async def save_congo_import(records: list) -> int:
    """Insert a batch of Congo Expenses rows from an Excel import."""
    if not records:
        return 0
    db = get_db()
    now = datetime.utcnow()
    docs = []
    for rec in records:
        doc = dict(rec)
        doc.pop("_raw", None)
        doc["expense_type"] = "congo_expenses"
        doc["import_date"]  = now
        doc["created_at"]   = now
        docs.append(doc)
    result = await db.separate_expenses.insert_many(docs, ordered=False)
    return len(result.inserted_ids)


async def get_congo_uploads() -> list:
    """Return one summary doc per Congo Expenses import batch."""
    db = get_db()
    pipeline = [
        {"$match": {
            "expense_type": "congo_expenses",
            "upload_id":    {"$exists": True, "$ne": ""},
        }},
        {"$group": {
            "_id":             "$upload_id",
            "sheet_label":     {"$first": "$sheet_label"},
            "source_filename": {"$first": "$source_filename"},
            "import_date":     {"$first": "$import_date"},
            "record_count":    {"$sum": 1},
            "balance_usd":     {"$sum": {"$ifNull": ["$amount_usd", 0]}},
            "money_in":        {"$sum": {
                "$cond": [
                    {"$lt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                    {"$ifNull": ["$amount_usd", 0]},
                    0,
                ],
            }},
            "money_out":       {"$sum": {
                "$cond": [
                    {"$gt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                    {"$ifNull": ["$amount_usd", 0]},
                    0,
                ],
            }},
            "min_expense_date": {"$min": "$expense_date"},
            "max_expense_date": {"$max": "$expense_date"},
        }},
        {"$sort": {"import_date": -1}},
    ]
    return await db.separate_expenses.aggregate(pipeline).to_list(length=None)


async def get_congo_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated rows for a single Congo Expenses import batch."""
    db = get_db()
    query: dict = {
        "expense_type": "congo_expenses",
        "upload_id":    upload_id,
    }
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"description": {"$regex": s, "$options": "i"}},
            {"truck_no":    {"$regex": s, "$options": "i"}},
            {"lpo_no":      {"$regex": s, "$options": "i"}},
            {"date_str":    {"$regex": s, "$options": "i"}},
        ]
    cursor = (
        db.separate_expenses
        .find(query)
        .sort([("row_index", 1), ("created_at", 1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_congo_upload_records(upload_id: str, search: str = "") -> int:
    """Count rows for a single Congo Expenses import batch."""
    db = get_db()
    query: dict = {
        "expense_type": "congo_expenses",
        "upload_id":    upload_id,
    }
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"description": {"$regex": s, "$options": "i"}},
            {"truck_no":    {"$regex": s, "$options": "i"}},
            {"lpo_no":      {"$regex": s, "$options": "i"}},
            {"date_str":    {"$regex": s, "$options": "i"}},
        ]
    return await db.separate_expenses.count_documents(query)


# ── Truck Overview — cross-source normalized rollup ───────────────────────────

_TRUCK_OVERVIEW_DIESEL_FEEDS = (
    ("diesel_infinity", "Infinity Diesel"),
    ("diesel_lake_zambia", "Lake Zambia Diesel"),
    ("diesel_lake_tunduma", "Lake Tunduma Diesel"),
    ("diesel_gbp", "GBP Diesel"),
)

_TRUCK_OVERVIEW_IMPORTED_FEEDS = (
    ("toll_plaza", "Toll Plaza"),
    ("parking_congo", "Parking Congo"),
    ("zambia_parking", "Zambia Parking"),
)

_TRUCK_OVERVIEW_SOURCES = {
    "all",
    "master",
    "diesel_cash",
    "diesel_imports",
    "afritrack",
    "toll_plaza",
    "parking_congo",
    "zambia_parking",
    "congo_expenses",
    "ahmed_kimvi",
    "rahntech",
    "comesa",
    "third_party",
    "sm_burhani",
}


def _truck_exact(value: str) -> dict:
    return {"$regex": f"^{re.escape(value.strip())}$", "$options": "i"}


def _safe_float_value(value: Any) -> float:
    try:
        if value in (None, "", "None"):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_date_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value in (None, "", "None"):
        return datetime.min
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d %b %Y",
        "%d %b %y",
    ):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return datetime.min


def _truck_row_matches_search(row: dict, search: str) -> bool:
    if not search.strip():
        return True
    needle = search.strip().lower()
    haystack = " ".join(
        str(row.get(key, "") or "")
        for key in (
            "source",
            "description",
            "reference",
            "truck_value",
            "station",
            "currency",
        )
    ).lower()
    return needle in haystack


def _truck_row_in_date_range(
    row: dict,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> bool:
    row_date = row.get("date")
    if not isinstance(row_date, datetime) or row_date == datetime.min:
        return date_from is None and date_to is None
    if date_from is not None and row_date < date_from:
        return False
    if date_to is not None and row_date > date_to:
        return False
    return True


def _normalized_row(
    *,
    source_group: str,
    source: str,
    date_value: Any,
    truck_value: str,
    description: str,
    reference: str = "",
    currency: str = "",
    amount: Any = None,
    liters: Any = None,
    rate: Any = None,
    station: str = "",
    receipt_status: str = "",
) -> dict:
    amt = _safe_float_value(amount) if amount not in (None, "") else None
    ltrs = _safe_float_value(liters) if liters not in (None, "") else None
    unit_rate = _safe_float_value(rate) if rate not in (None, "") else None
    dt = _as_date_value(date_value)
    return {
        "source_group": source_group,
        "source": source,
        "date": dt,
        "truck_value": truck_value or "—",
        "description": description or "—",
        "reference": reference or "—",
        "currency": currency or "",
        "amount": amt,
        "liters": ltrs,
        "rate": unit_rate,
        "station": station or "—",
        "receipt_status": receipt_status or "—",
    }


async def _truck_overview_master_rows(truck: str) -> list:
    db = get_db()
    docs = await db.transactions.find(
        {"verified": True, "truck_number": _truck_exact(truck)}
    ).sort([("date", -1), ("created_at", -1)]).to_list(length=None)

    rows = []
    for doc in docs:
        tx = Transaction.from_doc(doc)
        is_diesel_cash = bool(
            tx.category_name
            and re.fullmatch(
                _diesel_cash_name_filter()["$regex"],
                str(tx.category_name),
                re.IGNORECASE,
            )
        )
        rows.append(_normalized_row(
            source_group="diesel_cash" if is_diesel_cash else "master",
            source="Diesel Cash" if is_diesel_cash else "Master Expenses",
            date_value=tx.date,
            truck_value=tx.truck_number,
            description=tx.description or tx.item or tx.category_name or "Verified transaction",
            reference=tx.memo or tx.do_number or tx.lpo_do or tx.category_name or "",
            currency=tx.currency or "",
            amount=tx.amount,
            station=tx.ownership or "",
            receipt_status=tx.receipt_status or "",
        ))
    return rows


async def _truck_overview_diesel_rows(truck: str) -> list:
    db = get_db()
    rows: list = []
    for feed_type, label in _TRUCK_OVERVIEW_DIESEL_FEEDS:
        docs = await db.imported_feeds.find(
            {"feed_type": feed_type, "truck_no": _truck_exact(truck)}
        ).sort([("date", -1), ("import_date", -1)]).to_list(length=None)
        for doc in docs:
            rows.append(_normalized_row(
                source_group="diesel_imports",
                source=label,
                date_value=doc.get("date"),
                truck_value=str(doc.get("truck_no", "") or ""),
                description=doc.get("destinations") or doc.get("clients_name") or "Diesel import",
                reference=doc.get("lpo_no") or doc.get("do_sdo_no") or doc.get("sheet_label") or "",
                currency="TZS",
                amount=doc.get("total_amount"),
                liters=doc.get("ltrs"),
                rate=doc.get("price_per_ltr"),
                station=doc.get("diesel_at") or "",
            ))
    return rows


async def _truck_overview_imported_feed_rows(truck: str) -> list:
    db = get_db()
    rows: list = []

    toll_docs = await db.imported_feeds.find(
        {"feed_type": "toll_plaza", "vehicle_reg": _truck_exact(truck)}
    ).sort([("toll_date", -1), ("import_date", -1)]).to_list(length=None)
    for doc in toll_docs:
        rows.append(_normalized_row(
            source_group="toll_plaza",
            source="Toll Plaza",
            date_value=doc.get("toll_date"),
            truck_value=str(doc.get("vehicle_reg", "") or ""),
            description=doc.get("toll_plaza") or "Toll transaction",
            reference=doc.get("receipt_no") or doc.get("card_no") or doc.get("device") or "",
            currency="ZMW",
            amount=doc.get("tender_amount"),
            station=doc.get("cashier_name") or "",
        ))

    parking_docs = await db.imported_feeds.find(
        {"feed_type": "parking_congo", "vehicle_no": _truck_exact(truck)}
    ).sort([("payment_date", -1), ("import_date", -1)]).to_list(length=None)
    for doc in parking_docs:
        rows.append(_normalized_row(
            source_group="parking_congo",
            source="Parking Congo",
            date_value=doc.get("payment_date"),
            truck_value=str(doc.get("vehicle_no", "") or ""),
            description=doc.get("transaction_details") or doc.get("transaction_type") or "Parking transaction",
            reference=doc.get("ledger_id") or doc.get("direction") or "",
            amount=doc.get("amount"),
            station=doc.get("cashier") or "",
        ))

    zambia_docs = await db.imported_feeds.find(
        {"feed_type": "zambia_parking", "plate_num": _truck_exact(truck)}
    ).sort([("transaction_date", -1), ("import_date", -1)]).to_list(length=None)
    for doc in zambia_docs:
        debit = _safe_float_value(doc.get("debit"))
        credit = _safe_float_value(doc.get("credit"))
        amount = debit if debit else (-credit if credit else None)
        rows.append(_normalized_row(
            source_group="zambia_parking",
            source="Zambia Parking",
            date_value=doc.get("transaction_date") or doc.get("date"),
            truck_value=str(doc.get("plate_num", "") or ""),
            description=doc.get("heading_to") or doc.get("type") or "Zambia parking transaction",
            reference=doc.get("ticket_no") or doc.get("sheet_label") or "",
            currency="ZMW",
            amount=amount,
        ))

    return rows


async def _truck_overview_separate_rows(truck: str) -> list:
    db = get_db()
    rows: list = []

    congo_docs = await db.separate_expenses.find(
        {"expense_type": "congo_expenses", "truck_no": _truck_exact(truck)}
    ).sort([("expense_date", -1), ("created_at", -1)]).to_list(length=None)
    for doc in congo_docs:
        rows.append(_normalized_row(
            source_group="congo_expenses",
            source="Congo Expenses",
            date_value=doc.get("expense_date") or doc.get("date_str"),
            truck_value=str(doc.get("truck_no", "") or ""),
            description=doc.get("description") or "Congo expense",
            reference=doc.get("lpo_no") or doc.get("sheet_label") or "",
            currency="USD",
            amount=doc.get("amount_usd"),
        ))

    kimvi_docs = await db.separate_expenses.find(
        {"expense_type": "ahmed_kimvi", "truck_no": _truck_exact(truck)}
    ).sort([("expense_date", -1), ("created_at", -1)]).to_list(length=None)
    for doc in kimvi_docs:
        rows.append(_normalized_row(
            source_group="ahmed_kimvi",
            source="Ahmed Kimvi",
            date_value=doc.get("expense_date") or doc.get("date_str"),
            truck_value=str(doc.get("truck_no", "") or ""),
            description=doc.get("description") or "Ahmed Kimvi expense",
            reference=doc.get("sheet_label") or "",
            currency="USD",
            amount=doc.get("amount_usd"),
        ))

    return rows


async def _truck_overview_extra_sidebar_rows(truck: str) -> list:
    db = get_db()
    rows: list = []

    afritrack_docs = await db.imported_feeds.find(
        {"feed_type": "afritrack", "truck": _truck_exact(truck)}
    ).sort([("import_date", -1), ("row_index", 1)]).to_list(length=None)
    for doc in afritrack_docs:
        rows.append(_normalized_row(
            source_group="afritrack",
            source="Afritrack",
            date_value=doc.get("import_date"),
            truck_value=str(doc.get("truck", "") or ""),
            description=doc.get("remarks") or "Afritrack schedule row",
            reference=doc.get("period") or doc.get("source_filename") or "",
            currency="USD",
            amount=doc.get("total_invoice"),
            rate=doc.get("rate_per_day"),
        ))

    rahn_docs = await db.imported_feeds.find(
        {"feed_type": "rahntech", "truck_number": _truck_exact(truck)}
    ).sort([("sales_date", -1), ("import_date", -1)]).to_list(length=None)
    for doc in rahn_docs:
        rows.append(_normalized_row(
            source_group="rahntech",
            source="RahnTech",
            date_value=doc.get("sales_date"),
            truck_value=str(doc.get("truck_number", "") or ""),
            description=doc.get("driver_name") or doc.get("device_number") or "RahnTech transaction",
            reference=doc.get("trip_number") or doc.get("do_number") or "",
        ))

    comesa_docs = await db.imported_feeds.find(
        {"feed_type": "comesa", "truck_reg": _truck_exact(truck)}
    ).sort([("month", -1), ("import_date", -1)]).to_list(length=None)
    for doc in comesa_docs:
        rows.append(_normalized_row(
            source_group="comesa",
            source="COMESA",
            date_value=doc.get("import_date"),
            truck_value=str(doc.get("truck_reg", "") or ""),
            description=doc.get("name") or "COMESA cover",
            reference=doc.get("card_no") or doc.get("month") or "",
            currency="TZS",
            amount=doc.get("premium"),
        ))

    third_party_docs = await db.imported_feeds.find(
        {"feed_type": "third_party", "reg_no": _truck_exact(truck)}
    ).sort([("month", -1), ("import_date", -1)]).to_list(length=None)
    for doc in third_party_docs:
        rows.append(_normalized_row(
            source_group="third_party",
            source="Third Party Covers",
            date_value=doc.get("import_date"),
            truck_value=str(doc.get("reg_no", "") or ""),
            description=(doc.get("name") or "Third Party cover") + (
                f" [{doc.get('status')}]" if doc.get("status") else ""
            ),
            reference=doc.get("month") or "",
            currency="TZS",
            amount=doc.get("total_premium"),
        ))

    recon_docs = await db.reconciliation_entries.find(
        {"entity": "sm_burhani", "truck_and_trailer": {"$regex": re.escape(truck.strip()), "$options": "i"}}
    ).sort([("t1_date", -1), ("import_date", -1)]).to_list(length=None)
    for doc in recon_docs:
        table = doc.get("table", "bonds")
        source_label = "SM Burhani Bonds" if table == "bonds" else "SM Burhani RPA"
        rows.append(_normalized_row(
            source_group="sm_burhani",
            source=source_label,
            date_value=doc.get("t1_date") or doc.get("import_date"),
            truck_value=str(doc.get("truck_and_trailer", "") or ""),
            description=doc.get("consignment") or doc.get("importer") or "Reconciliation entry",
            reference=doc.get("prn_number") or doc.get("entry_reg_no") or doc.get("station") or "",
            currency="TZS",
            amount=doc.get("charge"),
            station=doc.get("station") or "",
        ))

    return rows


async def get_afritrack_uploads() -> list:
    """Return one summary doc per upload batch for the afritrack feed."""
    db = get_db()
    pipeline = [
        {"$match": {
            "feed_type": "afritrack",
            "upload_id": {"$exists": True, "$ne": ""},
        }},
        {"$group": {
            "_id":                  "$upload_id",
            "source_filename":      {"$first": "$source_filename"},
            "import_date":          {"$first": "$import_date"},
            "period":               {"$first": "$period"},
            "record_count":         {"$sum": 1},
            "total_tahmeed":        {"$sum": _safe_double("total_tahmeed")},
            "total_invoice":        {"$sum": _safe_double("total_invoice")},
            "total_variance":       {"$sum": _safe_double("variance")},
            "installation_tahmeed": {"$first": _safe_double("installation_tahmeed")},
            "installation_invoice": {"$first": _safe_double("installation_invoice")},
            "balance_mar":          {"$first": _safe_double("balance_mar")},
        }},
        {"$sort": {"import_date": -1}},
    ]
    return await db.imported_feeds.aggregate(pipeline).to_list(length=None)


def _afritrack_record_query(upload_id: str, search: str = "") -> dict:
    query: dict = {"feed_type": "afritrack", "upload_id": upload_id}
    if search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"truck": {"$regex": s, "$options": "i"}},
            {"remarks": {"$regex": s, "$options": "i"}},
            {"period": {"$regex": s, "$options": "i"}},
        ]
    return query


async def get_afritrack_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    db = get_db()
    cursor = (
        db.imported_feeds.find(_afritrack_record_query(upload_id, search))
        .sort([("row_index", 1), ("import_date", 1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_afritrack_upload_records(upload_id: str, search: str = "") -> int:
    db = get_db()
    return await db.imported_feeds.count_documents(_afritrack_record_query(upload_id, search))


async def get_afritrack_upload_totals(upload_id: str, search: str = "") -> dict:
    db = get_db()
    pipeline = [
        {"$match": _afritrack_record_query(upload_id, search)},
        {"$group": {
            "_id": None,
            "count": {"$sum": 1},
            "total_tahmeed": {"$sum": _safe_double("total_tahmeed")},
            "total_invoice": {"$sum": _safe_double("total_invoice")},
            "total_variance": {"$sum": _safe_double("variance")},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"count": 0, "total_tahmeed": 0.0, "total_invoice": 0.0, "total_variance": 0.0}


async def delete_afritrack_upload(upload_id: str) -> int:
    db = get_db()
    result = await db.imported_feeds.delete_many({"feed_type": "afritrack", "upload_id": upload_id})
    return result.deleted_count


async def _load_truck_overview_rows(truck: str) -> list:
    master_rows, diesel_rows, imported_rows, separate_rows, extra_rows = await asyncio.gather(
        _truck_overview_master_rows(truck),
        _truck_overview_diesel_rows(truck),
        _truck_overview_imported_feed_rows(truck),
        _truck_overview_separate_rows(truck),
        _truck_overview_extra_sidebar_rows(truck),
    )
    return master_rows + diesel_rows + imported_rows + separate_rows + extra_rows


def _filter_truck_overview_rows(
    rows: list,
    search: str = "",
    source: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list:
    wanted = source if source in _TRUCK_OVERVIEW_SOURCES else "all"
    filtered = [
        row for row in rows
        if (wanted == "all" or row["source_group"] == wanted)
        and _truck_row_matches_search(row, search)
        and _truck_row_in_date_range(row, date_from, date_to)
    ]
    filtered.sort(key=lambda row: (row.get("date") or datetime.min), reverse=True)
    return filtered


async def get_truck_overview_records(
    truck: str,
    search: str = "",
    source: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    if not truck.strip():
        return []
    rows = _filter_truck_overview_rows(
        await _load_truck_overview_rows(truck.strip()),
        search=search,
        source=source,
        date_from=date_from,
        date_to=date_to,
    )
    return rows[skip: skip + limit]


async def count_truck_overview_records(
    truck: str,
    search: str = "",
    source: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    if not truck.strip():
        return 0
    rows = _filter_truck_overview_rows(
        await _load_truck_overview_rows(truck.strip()),
        search=search,
        source=source,
        date_from=date_from,
        date_to=date_to,
    )
    return len(rows)


async def get_truck_overview_summary(
    truck: str,
    search: str = "",
    source: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    if not truck.strip():
        return {
            "record_count": 0,
            "source_count": 0,
            "tzs_total": 0.0,
            "usd_total": 0.0,
            "zmw_total": 0.0,
            "liters_total": 0.0,
        }
    rows = _filter_truck_overview_rows(
        await _load_truck_overview_rows(truck.strip()),
        search=search,
        source=source,
        date_from=date_from,
        date_to=date_to,
    )
    tzs_total = 0.0
    usd_total = 0.0
    zmw_total = 0.0
    liters_total = 0.0
    seen_sources: set[str] = set()
    for row in rows:
        seen_sources.add(row.get("source", ""))
        amount = row.get("amount")
        currency = (row.get("currency") or "").upper()
        if amount is not None:
            if currency == "TZS":
                tzs_total += amount
            elif currency == "USD":
                usd_total += amount
            elif currency == "ZMW":
                zmw_total += amount
        liters_total += row.get("liters") or 0.0
    return {
        "record_count": len(rows),
        "source_count": len(seen_sources),
        "tzs_total": tzs_total,
        "usd_total": usd_total,
        "zmw_total": zmw_total,
        "liters_total": liters_total,
    }
