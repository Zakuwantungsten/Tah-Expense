"""Accountant service — async Motor queries for the accountant dashboard."""

import asyncio
import calendar
import re
from datetime import datetime, date
from typing import Dict, List, Optional

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


async def get_imported_feed_totals(feed_type: str) -> dict:
    db = get_db()
    pipeline = [
        {"$match": {"feed_type": feed_type}},
        {"$group": {
            "_id": None,
            "tender_amount": {"$sum": {"$toDouble": {"$ifNull": ["$tender_amount", 0]}}},
            "amount":        {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}},
            "debit":         {"$sum": {"$toDouble": {"$ifNull": ["$debit", 0]}}},
            "credit":        {"$sum": {"$toDouble": {"$ifNull": ["$credit", 0]}}},
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
            {"receipt_no": {"$in": keys}},
            {"serial":     {"$in": keys}},
            {"ticket_no":  {"$in": keys}},
            {"lpo_no":     {"$in": keys}},
        ]},
        {"receipt_no": 1, "serial": 1, "ticket_no": 1, "lpo_no": 1},
    ).to_list(length=None)
    found: set = set()
    for doc in docs:
        for field in ("receipt_no", "serial", "ticket_no", "lpo_no"):
            v = doc.get(field)
            if v:
                found.add(v)
    return found


async def save_imported_feed(records: list) -> int:
    """Upsert a batch of imported feed records; returns the count inserted."""
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
            "ltrs":         {"$sum": {"$toDouble": {"$ifNull": ["$ltrs", 0]}}},
            "total_amount": {"$sum": {"$toDouble": {"$ifNull": ["$total_amount", 0]}}},
            "lake_usd":     {"$sum": {"$toDouble": {"$ifNull": ["$lake_usd", 0]}}},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"ltrs": 0.0, "total_amount": 0.0, "lake_usd": 0.0}


# ── Separate expenses (congo_expenses / harrison / ahmed_kimvi) ───────────────

def _build_sep_query(expense_type: str, search: str = "", truck: str = "") -> dict:
    query: dict = {"expense_type": expense_type}
    if search.strip():
        query["$or"] = [
            {"description": {"$regex": re.escape(search.strip()), "$options": "i"}},
            {"truck_no":    {"$regex": re.escape(search.strip()), "$options": "i"}},
            {"lpo_no":      {"$regex": re.escape(search.strip()), "$options": "i"}},
        ]
    if truck.strip():
        query["truck_no"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    return query


async def get_separate_expenses_list(
    expense_type: str,
    search: str = "",
    truck: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    db = get_db()
    query = _build_sep_query(expense_type, search, truck)
    cursor = db.separate_expenses.find(query).sort("created_at", -1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def count_separate_expenses(
    expense_type: str,
    search: str = "",
    truck: str = "",
) -> int:
    db = get_db()
    query = _build_sep_query(expense_type, search, truck)
    return await db.separate_expenses.count_documents(query)


async def get_separate_expense_totals(expense_type: str) -> dict:
    db = get_db()
    pipeline = [
        {"$match": {"expense_type": expense_type}},
        {"$group": {
            "_id": None,
            "amount_usd":    {"$sum": {"$ifNull": ["$amount_usd", 0]}},
            "amount_kwacha": {"$sum": {"$ifNull": ["$amount_kwacha", 0]}},
        }},
    ]
    result = await db.separate_expenses.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"amount_usd": 0.0, "amount_kwacha": 0.0}


async def save_separate_expense(expense_type: str, data: dict) -> bool:
    db = get_db()
    doc = dict(data)
    doc["expense_type"] = expense_type
    doc["created_at"]   = datetime.utcnow()
    result = await db.separate_expenses.insert_one(doc)
    return result.inserted_id is not None


# ── Ahmed Kimvi visit-sheet helpers ──────────────────────────────────────────

async def get_kimvi_sheets() -> List[str]:
    """Return distinct sheet_labels sorted chronologically."""
    db = get_db()
    labels = await db.separate_expenses.distinct(
        "sheet_label", {"expense_type": "ahmed_kimvi"}
    )
    return sorted(l for l in labels if l)


async def get_kimvi_sheet_entries(sheet_label: str) -> list:
    db = get_db()
    cursor = (
        db.separate_expenses
        .find({"expense_type": "ahmed_kimvi", "sheet_label": sheet_label})
        .sort([("is_advance", -1), ("created_at", 1)])
    )
    return await cursor.to_list(length=None)


async def create_kimvi_sheet(label: str, date_str: str, advance: float) -> None:
    """Insert the advance row that starts a new visit sheet."""
    db = get_db()
    await db.separate_expenses.insert_one({
        "expense_type": "ahmed_kimvi",
        "sheet_label":  label,
        "date_str":     date_str,
        "truck_no":     "",
        "description":  "Cash Advance Payment",
        "amount_usd":   advance,
        "is_advance":   True,
        "created_at":   datetime.utcnow(),
    })
