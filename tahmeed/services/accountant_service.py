"""Accountant service — async Motor queries for the accountant dashboard."""

import asyncio
import calendar
import hashlib
import re
from datetime import datetime, date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.transaction import Transaction
from tahmeed.services.diesel_amounts import apply_diesel_computed_fields, diesel_line_total
from tahmeed.services.excel_dates import (
    format_excel_date,
    normalize_date_fields,
    parse_excel_date,
)


def _date_range_clause(
    field: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Optional[dict]:
    """MongoDB clause for an optional inclusive From/To window on *field*."""
    if date_from is None and date_to is None:
        return None
    rng: dict = {}
    if date_from is not None:
        rng["$gte"] = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
    if date_to is not None:
        rng["$lte"] = date_to.replace(hour=23, minute=59, second=59, microsecond=0)
    return {field: rng}


def _with_date_range(
    query: dict,
    field: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Return *query* with an optional inclusive date window on *field*."""
    clause = _date_range_clause(field, date_from, date_to)
    if not clause:
        return query
    merged = dict(query)
    merged.update(clause)
    return merged


# ── transactions ──────────────────────────────────────────────────────────────

# Newest calendar day first; within a day, Daily Register / WhatsApp sequence.
UNVERIFIED_INBOX_SORT = [("date", -1), ("day_order", 1), ("created_at", 1)]


async def get_unverified_transactions(limit: int = 50, skip: int = 0) -> List[Transaction]:
    db = get_db()
    cursor = (
        db.transactions
        .find({"verified": False})
        .sort(list(UNVERIFIED_INBOX_SORT))
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
            "possible_duplicate": False,
            "date_discrepancy": False,
        }},
    )
    ok = result.modified_count == 1
    if ok:
        try:
            from tahmeed.services.audit_service import record_event

            await record_event(
                "txn.approve",
                actor_id=accountant_id,
                entity_type="transaction",
                entity_ids=[tx_id],
            )
        except Exception:
            pass
    return ok


async def reject_transaction(
    tx_id: ObjectId,
    reason: str,
    *,
    actor_id: ObjectId | None = None,
) -> bool:
    db = get_db()
    result = await db.transactions.update_one(
        {"_id": tx_id},
        {"$set": {
            "verified": False,
            "rejection_reason": reason,
            "rejected": True,
            "discarded": False,
        }},
    )
    ok = result.modified_count == 1
    if ok:
        try:
            from tahmeed.services.audit_service import record_event

            await record_event(
                "txn.reject",
                actor_id=actor_id,
                entity_type="transaction",
                entity_ids=[tx_id],
                details={"reason": (reason or "")[:240]},
            )
        except Exception:
            pass
    return ok


def _normalize_user_id(value: ObjectId | str | None) -> ObjectId | None:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return None


async def bulk_reject_transactions(
    tx_ids: List[ObjectId],
    reason: str,
    *,
    actor_id: ObjectId | None = None,
) -> int:
    if not tx_ids:
        return 0
    db = get_db()
    result = await db.transactions.update_many(
        {"_id": {"$in": tx_ids}},
        {"$set": {
            "verified": False,
            "rejection_reason": reason,
            "rejected": True,
            "discarded": False,
        }},
    )
    n = int(result.modified_count)
    if n:
        try:
            from tahmeed.services.audit_service import record_event

            await record_event(
                "txn.reject_bulk",
                actor_id=actor_id,
                entity_type="transaction",
                entity_ids=tx_ids,
                details={"count": n, "reason": (reason or "")[:240]},
            )
        except Exception:
            pass
    return n


async def get_cashier_names(cashier_ids: List[ObjectId]) -> Dict[ObjectId, str]:
    if not cashier_ids:
        return {}

    id_by_original: Dict[ObjectId | str, ObjectId] = {}
    query_ids: List[ObjectId] = []
    for cid in cashier_ids:
        oid = _normalize_user_id(cid)
        if oid is None:
            continue
        id_by_original[cid] = oid
        query_ids.append(oid)

    if not query_ids:
        return {}

    db = get_db()
    docs = await db.users.find(
        {"_id": {"$in": list(set(query_ids))}},
        {"_id": 1, "full_name": 1, "username": 1},
    ).to_list(length=None)

    names_by_oid: Dict[ObjectId, str] = {}
    for doc in docs:
        name = (doc.get("full_name") or "").strip()
        if not name:
            name = (doc.get("username") or "").strip()
        names_by_oid[doc["_id"]] = name or "Unknown cashier"

    return {
        cid: names_by_oid.get(id_by_original[cid], "Unknown cashier")
        for cid in cashier_ids
        if cid in id_by_original
    }


async def get_pending_count() -> int:
    """Sidebar / inbox badge: submitted unverified + deletion requests."""
    db = get_db()
    inbox = await db.transactions.count_documents({
        "verified": False,
        "rejected": {"$ne": True},
        "$or": [
            {"register_status": "submitted"},
            {"register_status": {"$exists": False}},
        ],
    })
    deletions = await db.transactions.count_documents({"deletion_requested": True})
    return int(inbox) + int(deletions)


def _sum_amount_if_currency(*codes: str, absolute: bool = False) -> dict:
    """Mongo $sum that only includes rows whose currency matches one of *codes*."""
    amount_expr: Any = {"$abs": "$amount"} if absolute else "$amount"
    return {
        "$sum": {
            "$cond": [
                {
                    "$in": [
                        {"$toUpper": {"$ifNull": ["$currency", ""]}},
                        list(codes),
                    ]
                },
                amount_expr,
                0,
            ]
        }
    }


async def _overview_zmw_feed_month_totals(year: int) -> Dict[int, float]:
    """Per-month ZMW from Toll Plaza + Zambia Parking (same date filters as feed tabs)."""

    async def _month(m: int) -> tuple[int, float]:
        toll, zambia = await asyncio.gather(
            get_toll_plaza_all_totals(year=year, month=m),
            get_zambia_parking_all_totals(year=year, month=m),
        )
        total = float(toll.get("total_zmw", 0) or 0) + float(
            zambia.get("total_debit", 0) or 0
        )
        return m, total

    pairs = await asyncio.gather(*[_month(m) for m in range(1, 13)])
    return dict(pairs)


async def _overview_usd_feed_month_totals(year: int) -> Dict[int, float]:
    """Per-month USD from separate-expense feeds (all except Toll Plaza / Zambia ZMW).

    Congo / Kimvi contribute money-out; Parking Congo and Afritrack contribute
    their spend totals. Toll Plaza stays in the ZMW feed merge only.
    """

    async def _month(m: int) -> tuple[int, float]:
        congo, kimvi, parking, afritrack = await asyncio.gather(
            get_congo_all_totals(year=year, month=m),
            get_kimvi_all_totals(year=year, month=m),
            get_parking_congo_all_totals(year=year, month=m),
            get_afritrack_all_totals(year=year, month=m),
        )
        total = (
            float(congo.get("money_out", 0) or 0)
            + float(kimvi.get("money_out", 0) or 0)
            + float(parking.get("amount", 0) or 0)
            + float(afritrack.get("total_invoice", 0) or 0)
        )
        return m, total

    pairs = await asyncio.gather(*[_month(m) for m in range(1, 13)])
    return dict(pairs)


async def get_overview_kpis(year: Optional[int] = None) -> dict:
    """Aggregate real counts for the Overview KPI cards (TZS / USD / ZMW).

    USD/ZMW from imported separate-expense feeds are merged by
    ``get_overview_dashboard`` so feed aggregations run once per refresh.
    """
    today = date.today()
    _year = year if year is not None else today.year
    month_start = datetime(today.year, today.month, 1)
    year_start = datetime(_year, 1, 1)
    year_end = datetime(_year, 12, 31, 23, 59, 59)

    db = get_db()
    pending_count, ytd_res, month_res = await asyncio.gather(
        db.transactions.count_documents({"verified": False, "rejected": {"$ne": True}}),
        db.transactions.aggregate([
            {"$match": {"verified": True, "date": {"$gte": year_start, "$lte": year_end}}},
            {"$group": {
                "_id": None,
                "count": {"$sum": 1},
                "tzs_total": _sum_amount_if_currency("TZS", "TSH", "TZ"),
                "usd_total": _sum_amount_if_currency("USD"),
                "zmw_total": _sum_amount_if_currency("ZMW", "ZMB", "ZK"),
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

    ytd = ytd_res[0] if ytd_res else {
        "count": 0, "tzs_total": 0.0, "usd_total": 0.0, "zmw_total": 0.0,
    }
    month = month_res[0] if month_res else {"verified": 0, "total": 0}

    return {
        "pending_count": pending_count,
        "master_count": ytd.get("count", 0),
        "verified_this_month": month.get("verified", 0),
        "submitted_this_month": month.get("total", 0),
        "total_tzs_ytd": float(ytd.get("tzs_total", 0.0) or 0.0),
        "total_usd_ytd": float(ytd.get("usd_total", 0.0) or 0.0),
        "total_zmw_ytd": float(ytd.get("zmw_total", 0.0) or 0.0),
    }


async def get_overview_category_breakdown(year: int, top_n: int = 4) -> List[dict]:
    """Top expense categories with per-currency verified spend for a fiscal year."""
    db = get_db()
    year_start = datetime(year, 1, 1)
    year_end = datetime(year, 12, 31, 23, 59, 59)
    docs = await db.transactions.aggregate([
        {"$match": {"verified": True, "date": {"$gte": year_start, "$lte": year_end}}},
        {"$group": {
            "_id": {"$ifNull": ["$category_name", "Uncategorised"]},
            "tzs": _sum_amount_if_currency("TZS", "TSH", "TZ", absolute=True),
            "usd": _sum_amount_if_currency("USD", absolute=True),
            "zmw": _sum_amount_if_currency("ZMW", "ZMB", "ZK", absolute=True),
        }},
        {"$sort": {"tzs": -1, "usd": -1, "zmw": -1}},
    ]).to_list(None)

    if not docs:
        return []

    # Rank by whichever currency has the largest absolute total across categories
    # so empty TZS years still surface USD/ZMW categories.
    def _rank_key(doc: dict) -> float:
        return max(
            float(doc.get("tzs", 0) or 0),
            float(doc.get("usd", 0) or 0),
            float(doc.get("zmw", 0) or 0),
        )

    ranked = sorted(docs, key=_rank_key, reverse=True)
    if _rank_key(ranked[0]) <= 0:
        return []

    top = ranked[:top_n]
    rest = ranked[top_n:]
    slices: List[dict] = []
    for doc in top:
        name = doc["_id"] or "Uncategorised"
        slices.append({
            "name": name,
            "tzs": float(doc.get("tzs", 0.0) or 0.0),
            "usd": float(doc.get("usd", 0.0) or 0.0),
            "zmw": float(doc.get("zmw", 0.0) or 0.0),
        })
    if rest:
        slices.append({
            "name": "Other",
            "tzs": sum(float(d.get("tzs", 0) or 0) for d in rest),
            "usd": sum(float(d.get("usd", 0) or 0) for d in rest),
            "zmw": sum(float(d.get("zmw", 0) or 0) for d in rest),
        })
    return slices


async def get_overview_receipt_breakdown(year: int) -> dict:
    """Receipt status counts for verified transactions in a fiscal year."""
    db = get_db()
    year_start = datetime(year, 1, 1)
    year_end = datetime(year, 12, 31, 23, 59, 59)
    docs = await db.transactions.aggregate([
        {"$match": {"verified": True, "date": {"$gte": year_start, "$lte": year_end}}},
        {"$group": {"_id": "$receipt_status", "count": {"$sum": 1}}},
    ]).to_list(None)

    received = pending = missing = 0
    for doc in docs:
        status = (doc.get("_id") or "pending").lower()
        count = doc.get("count", 0)
        if status == "received":
            received += count
        elif status in ("missing", "no_receipt"):
            missing += count
        else:
            pending += count

    total = received + pending + missing
    return {
        "received": received,
        "pending": pending,
        "missing": missing,
        "total": total,
    }


async def get_overview_month_totals(year: int) -> Dict:
    """Per-month TZS / USD / ZMW totals for the Overview trend chart.

    Master ledger supplies TZS/USD/(any ZMW on transactions). Separate-expense
    USD feeds (Congo, Kimvi, Parking Congo, Afritrack) are added into USD;
    Toll Plaza and Zambia Parking feeds are added into ZMW.

    Returns ``{"months": {1: {...}, ...}, "feed_usd_ytd": float, "feed_zmw_ytd": float}``.
    """
    master, feed_usd, feed_zmw = await asyncio.gather(
        get_master_month_totals(year),
        _overview_usd_feed_month_totals(year),
        _overview_zmw_feed_month_totals(year),
    )
    months: Dict[int, dict] = {}
    for month in range(1, 13):
        row = master.get(month, {"tzs": 0.0, "usd": 0.0, "zmw": 0.0, "count": 0})
        months[month] = {
            "tzs": float(row.get("tzs", 0.0) or 0.0),
            "usd": float(row.get("usd", 0.0) or 0.0) + float(feed_usd.get(month, 0.0) or 0.0),
            "zmw": float(row.get("zmw", 0.0) or 0.0) + float(feed_zmw.get(month, 0.0) or 0.0),
            "count": int(row.get("count", 0) or 0),
        }
    return {
        "months": months,
        "feed_usd_ytd": float(sum(feed_usd.values())),
        "feed_zmw_ytd": float(sum(feed_zmw.values())),
    }


async def get_overview_dashboard(year: int) -> dict:
    """All data needed by the accountant Overview tab."""
    kpis, month_payload, categories, receipts, recent = await asyncio.gather(
        get_overview_kpis(year),
        get_overview_month_totals(year),
        get_overview_category_breakdown(year),
        get_overview_receipt_breakdown(year),
        get_verified_transactions(year=year, limit=8),
    )
    kpis = dict(kpis)
    kpis["total_usd_ytd"] = (
        float(kpis.get("total_usd_ytd", 0.0) or 0.0)
        + float(month_payload.get("feed_usd_ytd", 0.0) or 0.0)
    )
    kpis["total_zmw_ytd"] = (
        float(kpis.get("total_zmw_ytd", 0.0) or 0.0)
        + float(month_payload.get("feed_zmw_ytd", 0.0) or 0.0)
    )
    return {
        "kpis": kpis,
        "month_totals": month_payload.get("months", {}),
        "categories": categories,
        "receipts": receipts,
        "recent": recent,
    }


# ── Inbox filtered queries ────────────────────────────────────────────────────

def _normalize_multi_filter(value) -> List[str]:
    """Accept a single string or a list of strings; empty → no filter."""
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


def _append_text_filters(
    and_clauses: list,
    *,
    search: str = "",
    item="",
    description="",
) -> None:
    if search.strip():
        s = re.escape(search.strip())
        and_clauses.append({"$or": [
            {"description": {"$regex": s, "$options": "i"}},
            {"item": {"$regex": s, "$options": "i"}},
            {"category_name": {"$regex": s, "$options": "i"}},
            {"truck_number": {"$regex": s, "$options": "i"}},
        ]})
    descs = _normalize_multi_filter(description)
    if descs:
        if isinstance(description, str):
            # Legacy free-text: substring match
            and_clauses.append({
                "description": {
                    "$regex": re.escape(descs[0]),
                    "$options": "i",
                },
            })
        else:
            # Multi-select: exact match on any selected description
            and_clauses.append({"$or": [
                {"description": {"$regex": f"^{re.escape(d)}$", "$options": "i"}}
                for d in descs
            ]})
    items = _normalize_multi_filter(item)
    if items:
        item_ors: list = []
        for it in items:
            esc = re.escape(it)
            item_ors.append({"item": {"$regex": f"^{esc}$", "$options": "i"}})
            item_ors.append({"category_name": {"$regex": f"^{esc}$", "$options": "i"}})
        and_clauses.append({"$or": item_ors})


def _build_inbox_query(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    edited: Optional[bool] = None,
    item="",
    description="",
) -> dict:
    # Only rows submitted from the register (or legacy docs without the field).
    query: dict = {
        "verified": False,
        "rejected": {"$ne": True},
        "$or": [
            {"register_status": "submitted"},
            {"register_status": {"$exists": False}},
        ],
    }
    # edited=False  → "New" tab: fresh entries never flagged as edited
    #                 (matches both False and missing field on legacy docs)
    # edited=True   → "Edited" tab: rows the cashier changed after save
    # edited=None   → no edited filter (all unverified)
    if edited is True:
        query["edited_after_verification"] = True
    elif edited is False:
        query["edited_after_verification"] = {"$ne": True}

    and_clauses: list = []
    _append_text_filters(
        and_clauses, search=search, item=item, description=description,
    )
    if and_clauses:
        query["$and"] = and_clauses

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
        # Edited tab: filter by when the cashier edited, so any-date rows still show.
        date_field = "last_edited_at" if edited is True else "date"
        query[date_field] = df
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
    item="",
    description="",
) -> List[Transaction]:
    db = get_db()
    query = _build_inbox_query(
        search, truck, cashier_id, date_from, date_to, edited, item, description,
    )
    cursor = (
        db.transactions.find(query)
        .sort(list(UNVERIFIED_INBOX_SORT))
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
    item="",
    description="",
) -> int:
    db = get_db()
    query = _build_inbox_query(
        search, truck, cashier_id, date_from, date_to, edited, item, description,
    )
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
    item="",
    description="",
) -> List[Transaction]:
    """Rows the cashier edited after save (verified=False AND
    edited_after_verification=True). Sorted by most-recently-edited first."""
    db = get_db()
    query = _build_inbox_query(
        search, truck, cashier_id, date_from, date_to,
        edited=True, item=item, description=description,
    )
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
    item="",
    description="",
) -> int:
    db = get_db()
    query = _build_inbox_query(
        search, truck, cashier_id, date_from, date_to,
        edited=True, item=item, description=description,
    )
    return await db.transactions.count_documents(query)


async def get_edited_count() -> int:
    """Total edited rows awaiting re-approval (for the badge)."""
    db = get_db()
    return await db.transactions.count_documents(
        {
            "verified": False,
            "edited_after_verification": True,
            "rejected": {"$ne": True},
            "$or": [
                {"register_status": "submitted"},
                {"register_status": {"$exists": False}},
            ],
        }
    )


# ── Deletion-request queries (accountant "Deleted" sub-tab) ───────────────────

def _build_deletion_query(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item="",
    description="",
) -> dict:
    query: dict = {"deletion_requested": True}
    and_clauses: list = []
    _append_text_filters(
        and_clauses, search=search, item=item, description=description,
    )
    if and_clauses:
        query["$and"] = and_clauses
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
        # Prefer when the cashier requested deletion so any-date rows still show.
        query["deletion_requested_at"] = df
    return query


async def get_deletion_requested_filtered(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 25,
    skip: int = 0,
    item="",
    description="",
) -> List[Transaction]:
    """Approved rows the cashier asked to permanently remove."""
    db = get_db()
    query = _build_deletion_query(
        search, truck, cashier_id, date_from, date_to, item, description,
    )
    cursor = (
        db.transactions.find(query)
        .sort([("deletion_requested_at", -1), ("date", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [Transaction.from_doc(d) for d in docs]


async def count_deletion_requested_filtered(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item="",
    description="",
) -> int:
    db = get_db()
    query = _build_deletion_query(
        search, truck, cashier_id, date_from, date_to, item, description,
    )
    return await db.transactions.count_documents(query)


async def get_deletion_requested_count() -> int:
    """Total deletion requests awaiting accountant confirm/restore."""
    db = get_db()
    return await db.transactions.count_documents({"deletion_requested": True})


async def get_deletion_requested_trucks() -> List[str]:
    db = get_db()
    vals = await db.transactions.distinct("truck_number", {"deletion_requested": True})
    return sorted(v for v in vals if v)


async def get_deletion_requested_cashier_ids() -> List[ObjectId]:
    db = get_db()
    vals = await db.transactions.distinct("cashier_id", {"deletion_requested": True})
    return [v for v in vals if v]


async def get_deletion_requested_items(
    descriptions: Optional[List[str]] = None,
) -> List[str]:
    db = get_db()
    base: dict = {"deletion_requested": True}
    descs = _normalize_multi_filter(descriptions or "")
    if descs:
        base["$or"] = [
            {"description": {"$regex": re.escape(d), "$options": "i"}} for d in descs
        ]
    items = await db.transactions.distinct("item", base)
    cats = await db.transactions.distinct("category_name", base)
    return sorted({*(v for v in items if v), *(v for v in cats if v)})


async def get_deletion_requested_descriptions(
    items: Optional[List[str]] = None,
) -> List[str]:
    db = get_db()
    base: dict = {"deletion_requested": True}
    item_list = _normalize_multi_filter(items or "")
    if item_list:
        ors: list = []
        for it in item_list:
            esc = re.escape(it)
            ors.append({"item": {"$regex": f"^{esc}$", "$options": "i"}})
            ors.append({"category_name": {"$regex": f"^{esc}$", "$options": "i"}})
        base["$or"] = ors
    vals = await db.transactions.distinct("description", base)
    return sorted(v for v in vals if v)


async def confirm_deletion(
    tx_id: ObjectId,
    *,
    actor_id: ObjectId | None = None,
) -> bool:
    """Permanently delete an approved row flagged for removal.

    Also removes any pending-edit clones that still point at this original.
    """
    from tahmeed.db.mongo_txn import run_in_transaction, session_kwargs

    db = get_db()
    doc = await db.transactions.find_one({"_id": tx_id, "deletion_requested": True})
    if not doc:
        return False

    async def _body(session):
        kw = session_kwargs(session)
        await db.transactions.delete_many(
            {"original_transaction_id": tx_id}, **kw
        )
        result = await db.transactions.delete_one(
            {"_id": tx_id, "deletion_requested": True}, **kw
        )
        return result.deleted_count == 1

    ok = await run_in_transaction(_body)
    if ok:
        try:
            from tahmeed.services.audit_service import record_event

            await record_event(
                "txn.delete_confirm",
                actor_id=actor_id,
                entity_type="transaction",
                entity_ids=[tx_id],
            )
        except Exception:
            pass
    return ok


async def bulk_confirm_deletions(
    tx_ids: List[ObjectId],
    *,
    actor_id: ObjectId | None = None,
) -> int:
    if not tx_ids:
        return 0
    from tahmeed.db.mongo_txn import run_in_transaction, session_kwargs

    db = get_db()

    async def _body(session):
        kw = session_kwargs(session)
        await db.transactions.delete_many(
            {"original_transaction_id": {"$in": tx_ids}}, **kw
        )
        result = await db.transactions.delete_many(
            {"_id": {"$in": tx_ids}, "deletion_requested": True},
            **kw,
        )
        return int(result.deleted_count)

    n = await run_in_transaction(_body)
    if n:
        try:
            from tahmeed.services.audit_service import record_event

            await record_event(
                "txn.delete_confirm_bulk",
                actor_id=actor_id,
                entity_type="transaction",
                entity_ids=tx_ids,
                details={"count": n},
            )
        except Exception:
            pass
    return n


async def restore_deletion(tx_id: ObjectId) -> bool:
    """Clear deletion request flags so the row returns to Master / register."""
    db = get_db()
    result = await db.transactions.update_one(
        {"_id": tx_id, "deletion_requested": True},
        {"$set": {
            "deletion_requested": False,
            "deletion_requested_at": None,
            "deletion_requested_by": None,
        }},
    )
    return result.modified_count == 1


async def bulk_restore_deletions(tx_ids: List[ObjectId]) -> int:
    if not tx_ids:
        return 0
    db = get_db()
    result = await db.transactions.update_many(
        {"_id": {"$in": tx_ids}, "deletion_requested": True},
        {"$set": {
            "deletion_requested": False,
            "deletion_requested_at": None,
            "deletion_requested_by": None,
        }},
    )
    return int(result.modified_count)


# ── Rejected queries ──────────────────────────────────────────────────────────

def _build_rejected_query(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item="",
    description="",
) -> dict:
    query: dict = {"rejected": True, "discarded": {"$ne": True}}
    and_clauses: list = []
    _append_text_filters(
        and_clauses, search=search, item=item, description=description,
    )
    if and_clauses:
        query["$and"] = and_clauses
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
    item="",
    description="",
) -> List[Transaction]:
    db = get_db()
    query = _build_rejected_query(
        search, truck, cashier_id, date_from, date_to, item, description,
    )
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
    item="",
    description="",
) -> int:
    db = get_db()
    query = _build_rejected_query(
        search, truck, cashier_id, date_from, date_to, item, description,
    )
    return await db.transactions.count_documents(query)


async def get_rejected_count() -> int:
    """Total rejected entries across all cashiers (for the badge)."""
    db = get_db()
    return await db.transactions.count_documents({
        "rejected": True,
        "discarded": {"$ne": True},
    })


async def return_to_inbox(tx_id: ObjectId) -> bool:
    """Undo a rejection — clears rejected flag so the entry reappears in its original inbox tab."""
    db = get_db()
    result = await db.transactions.update_one(
        {"_id": tx_id, "rejected": True},
        {"$set": {"rejected": False, "rejection_reason": None, "discarded": False}},
    )
    return result.modified_count == 1


# ── Re-approve (edited entries) ───────────────────────────────────────────────

_CASCADE_FIELDS = {
    "date", "description", "truck_number", "amount", "currency",
    "lpo_do", "do_number", "memo", "receipt_status", "notes_flag",
    "ref_float", "ownership", "approver", "payee", "cheque", "reported_date",
    "category_name", "category_id", "item",
    "category_confidence", "month", "year",
}


async def re_approve_transaction(tx_id: ObjectId, accountant_id: ObjectId) -> bool:
    """Approve an edited row.

    If the pending-edit document carries an original_transaction_id, the edited
    values cascade to the original approved record and the pending doc is deleted.
    Otherwise (legacy in-place edits) the document is flipped verified=True directly.
    """
    from tahmeed.db.mongo_txn import run_in_transaction, session_kwargs

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

        async def _cascade(session):
            kw = session_kwargs(session)
            result = await db.transactions.update_one(
                {"_id": original_id}, {"$set": updates}, **kw
            )
            if result.modified_count != 1:
                return False
            await db.transactions.delete_one({"_id": tx_id}, **kw)
            return True

        ok = await run_in_transaction(_cascade)
        if ok:
            try:
                from tahmeed.services.audit_service import record_event

                await record_event(
                    "txn.re_approve",
                    actor_id=accountant_id,
                    entity_type="transaction",
                    entity_ids=[original_id, tx_id],
                    details={"cascade": True},
                )
            except Exception:
                pass
        return ok

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
    ok = result.modified_count == 1
    if ok:
        try:
            from tahmeed.services.audit_service import record_event

            await record_event(
                "txn.re_approve",
                actor_id=accountant_id,
                entity_type="transaction",
                entity_ids=[tx_id],
            )
        except Exception:
            pass
    return ok


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
    vals = await db.transactions.distinct(
        "truck_number", _inbox_base_query(rejected=False),
    )
    return sorted(v for v in vals if v)


def _inbox_base_query(*, rejected: bool = False) -> dict:
    if rejected:
        return {"rejected": True, "discarded": {"$ne": True}}
    return {
        "verified": False,
        "rejected": {"$ne": True},
        "$or": [
            {"register_status": "submitted"},
            {"register_status": {"$exists": False}},
        ],
    }


def _apply_cascade_filters(
    base: dict,
    *,
    items: Optional[List[str]] = None,
    descriptions: Optional[List[str]] = None,
) -> dict:
    """Narrow a distinct-options query by the other cascading filter."""
    query = dict(base)
    and_clauses: list = []
    _append_text_filters(
        and_clauses,
        item=items or [],
        description=descriptions or [],
    )
    if and_clauses:
        existing = query.pop("$and", None)
        if existing:
            and_clauses = list(existing) + and_clauses
        query["$and"] = and_clauses
    return query


async def get_unverified_items(
    descriptions: Optional[List[str]] = None,
) -> List[str]:
    """Distinct item / category names for Verify filter dropdowns."""
    db = get_db()
    base = _apply_cascade_filters(
        _inbox_base_query(rejected=False),
        descriptions=descriptions,
    )
    items = await db.transactions.distinct("item", base)
    cats = await db.transactions.distinct("category_name", base)
    names = {*(v for v in items if v), *(v for v in cats if v)}
    return sorted(names, key=str.lower)


async def get_unverified_descriptions(
    items: Optional[List[str]] = None,
) -> List[str]:
    """Distinct descriptions on unverified inbox rows (optionally scoped by items)."""
    db = get_db()
    base = _apply_cascade_filters(
        _inbox_base_query(rejected=False),
        items=items,
    )
    vals = await db.transactions.distinct("description", base)
    return sorted((v for v in vals if v), key=str.lower)


async def get_unverified_cashier_ids() -> List[ObjectId]:
    db = get_db()
    vals = await db.transactions.distinct(
        "cashier_id", _inbox_base_query(rejected=False),
    )
    return [v for v in vals if v is not None]


async def get_rejected_trucks() -> List[str]:
    db = get_db()
    vals = await db.transactions.distinct(
        "truck_number",
        {"rejected": True, "discarded": {"$ne": True}},
    )
    return sorted(v for v in vals if v)


async def get_rejected_items(
    descriptions: Optional[List[str]] = None,
) -> List[str]:
    db = get_db()
    base = _apply_cascade_filters(
        _inbox_base_query(rejected=True),
        descriptions=descriptions,
    )
    items = await db.transactions.distinct("item", base)
    cats = await db.transactions.distinct("category_name", base)
    names = {*(v for v in items if v), *(v for v in cats if v)}
    return sorted(names, key=str.lower)


async def get_rejected_descriptions(
    items: Optional[List[str]] = None,
) -> List[str]:
    db = get_db()
    base = _apply_cascade_filters(
        _inbox_base_query(rejected=True),
        items=items,
    )
    vals = await db.transactions.distinct("description", base)
    return sorted((v for v in vals if v), key=str.lower)


async def get_rejected_cashier_ids() -> List[ObjectId]:
    db = get_db()
    vals = await db.transactions.distinct(
        "cashier_id",
        {"rejected": True, "discarded": {"$ne": True}},
    )
    return [v for v in vals if v is not None]


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
    n = int(result.modified_count)
    if n:
        try:
            from tahmeed.services.audit_service import record_event

            await record_event(
                "txn.approve_bulk",
                actor_id=accountant_id,
                entity_type="transaction",
                entity_ids=tx_ids,
                details={"count": n},
            )
        except Exception:
            pass
    return n


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


async def bulk_update_transaction_category(
    tx_ids: List[ObjectId],
    category_name: str,
    category_id: Optional[ObjectId] = None,
) -> int:
    """Assign the same item/category to many transactions in one round-trip."""
    if not tx_ids:
        return 0
    db = get_db()
    update: dict = {"category_name": category_name, "item": category_name}
    if category_id:
        update["category_id"] = category_id
    result = await db.transactions.update_many(
        {"_id": {"$in": tx_ids}},
        {"$set": update},
    )
    return int(result.modified_count)


# ── Master Expenses Table queries ────────────────────────────────────────────


def _master_period_clause(start: datetime, end: datetime) -> dict:
    """Master year/month tabs filter by Excel transaction ``date`` only.

    Register day (``import_primary_date``) is used to file/open an upload as a
    batch elsewhere. Mixing it into Master month filters pulled Jan-dated rows
    into December when the upload was filed on 31 Dec — so Master stays
    date-true here.
    """
    return {"date": {"$gte": start, "$lte": end}}


def _build_master_query(
    year: Optional[int],
    month: int,          # 0 = all, 1‑12 = month index
    search: str,
    truck: str,
    category: str,
    receipt: str,
    description: str = "",   # exact-ish sub-route filter (description contains)
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    _year = year or date.today().year
    if month and 1 <= month <= 12:
        last_day = calendar.monthrange(_year, month)[1]
        start = datetime(_year, month, 1)
        end = datetime(_year, month, last_day, 23, 59, 59)
    else:
        start = datetime(_year, 1, 1)
        end = datetime(_year, 12, 31, 23, 59, 59)

    if date_from is not None:
        df = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
        start = max(start, df)
    if date_to is not None:
        dt = date_to.replace(hour=23, minute=59, second=59, microsecond=0)
        end = min(end, dt)

    query: dict = {
        "verified": True,
        "deletion_requested": {"$ne": True},
        **_master_period_clause(start, end),
    }

    and_clauses: list = []

    # Free-text search matches description or truck number.
    if search.strip():
        s = re.escape(search.strip())
        and_clauses.append({"$or": [
            {"description": {"$regex": s, "$options": "i"}},
            {"truck_number": {"$regex": s, "$options": "i"}},
        ]})

    if description.strip():
        and_clauses.append({
            "description": {"$regex": re.escape(description.strip()), "$options": "i"},
        })

    if and_clauses:
        if len(and_clauses) == 1:
            query.update(and_clauses[0])
        else:
            query["$and"] = and_clauses

    if truck.strip():
        query["truck_number"] = truck.strip()
    if category.strip():
        query["category_name"] = {
            "$regex": f"^{re.escape(category.strip())}$",
            "$options": "i",
        }
    if receipt.strip() and receipt != "all":
        r = receipt.strip()
        if r == "missing":
            query["receipt_status"] = {"$in": ["missing", "no_receipt"]}
        else:
            query["receipt_status"] = r
    return query


async def get_master_transactions(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    sort_field: str = "date",
    sort_asc: bool = False,
    limit: int = 50,
    skip: int = 0,
) -> List[Transaction]:
    db = get_db()
    query = _build_master_query(
        year, month, search, truck, category, receipt, description,
        date_from, date_to,
    )
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
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    db = get_db()
    query = _build_master_query(
        year, month, search, truck, category, receipt, description,
        date_from, date_to,
    )
    return await db.transactions.count_documents(query)


async def get_master_totals(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Aggregate TZS + USD totals for the current filter (all pages, not just current)."""
    db = get_db()
    query = _build_master_query(
        year, month, search, truck, category, receipt, description,
        date_from, date_to,
    )
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
    """Per-month TZS / USD / ZMW totals for the year (master ledger)."""
    db = get_db()
    _year = year
    start = datetime(_year, 1, 1)
    end = datetime(_year, 12, 31, 23, 59, 59)
    pipeline = [
        {"$match": {
            "verified": True,
            **_master_period_clause(start, end),
        }},
        {"$group": {
            "_id": {"$month": "$date"},
            "tzs": _sum_amount_if_currency("TZS", "TSH", "TZ"),
            "usd": _sum_amount_if_currency("USD"),
            "zmw": _sum_amount_if_currency("ZMW", "ZMB", "ZK"),
            "count": {"$sum": 1},
        }},
    ]
    docs = await db.transactions.aggregate(pipeline).to_list(12)
    return {
        doc["_id"]: {
            "tzs": doc.get("tzs", 0.0),
            "usd": doc.get("usd", 0.0),
            "zmw": doc.get("zmw", 0.0),
            "count": doc["count"],
        }
        for doc in docs
    }


async def get_master_available_years() -> List[int]:
    """Calendar years that have verified Master rows (Excel date or register day)."""
    db = get_db()
    pipeline = [
        {"$match": {"verified": True, "deletion_requested": {"$ne": True}}},
        {"$project": {
            "years": {
                "$setUnion": [
                    {"$cond": [
                        {"$ne": [{"$ifNull": ["$date", None]}, None]},
                        [{"$year": "$date"}],
                        [],
                    ]},
                    {"$cond": [
                        {"$ne": [{"$ifNull": ["$import_primary_date", None]}, None]},
                        [{"$year": "$import_primary_date"}],
                        [],
                    ]},
                ]
            }
        }},
        {"$unwind": "$years"},
        {"$group": {"_id": "$years"}},
        {"$sort": {"_id": -1}},
    ]
    docs = await db.transactions.aggregate(pipeline).to_list(50)
    years = [int(d["_id"]) for d in docs if d.get("_id") is not None]
    if not years:
        y = date.today().year
        return [y - 1, y, y + 1]
    # Keep a little headroom around the data so new FY entry is easy.
    lo = min(years) - 1
    hi = max(max(years), date.today().year) + 1
    return list(range(hi, lo - 1, -1))


async def get_master_trucks(year: Optional[int] = None) -> List[str]:
    db = get_db()
    _year = year or date.today().year
    start = datetime(_year, 1, 1)
    end = datetime(_year, 12, 31, 23, 59, 59)
    query = {
        "verified": True,
        **_master_period_clause(start, end),
    }
    vals = await db.transactions.distinct("truck_number", query)
    return sorted(v for v in vals if v)


async def get_master_categories(year: Optional[int] = None) -> List[str]:
    db = get_db()
    _year = year or date.today().year
    start = datetime(_year, 1, 1)
    end = datetime(_year, 12, 31, 23, 59, 59)
    query = {
        "verified": True,
        **_master_period_clause(start, end),
    }
    vals = await db.transactions.distinct("category_name", query)
    return sorted(v for v in vals if v)


# ── Diesel Cash (cashier-fed, verified transactions) ─────────────────────────

DIESEL_CASH_CATEGORY = "Diesel Cash"
DIESEL_CASH_ITEMS_SETTING = "diesel_cash_items"

# Default item name(s) when the accountant has not configured a list yet.
# Matching is case-insensitive exact on item / category_name — never description.
DIESEL_CASH_CATEGORIES = ("Diesel Cash",)

_MONTH_NAMES = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def normalize_diesel_cash_item_names(raw: Any = None) -> List[str]:
    """Unique item names, order preserved. ``None`` → default; ``[]`` stays empty."""
    if raw is None:
        values: Sequence[Any] = DIESEL_CASH_CATEGORIES
    elif isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = DIESEL_CASH_CATEGORIES
    names: List[str] = []
    seen: Set[str] = set()
    for value in values:
        name = str(value or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


async def get_diesel_cash_item_names() -> List[str]:
    """Configured item names that feed Fuel Consumption → Diesel Cash."""
    from tahmeed.services.settings_service import get_setting

    try:
        raw = await get_setting(DIESEL_CASH_ITEMS_SETTING)
    except Exception:
        raw = None
    if raw is None:
        return list(DIESEL_CASH_CATEGORIES)
    return normalize_diesel_cash_item_names(raw)


async def set_diesel_cash_item_names(names: Sequence[str]) -> List[str]:
    """Persist the item names that feed Diesel Cash. Empty list catches nothing."""
    from tahmeed.services.settings_service import set_setting

    cleaned = normalize_diesel_cash_item_names(list(names or []))
    await set_setting(DIESEL_CASH_ITEMS_SETTING, cleaned)
    return cleaned


def _diesel_cash_name_filter(names: Optional[Sequence[str]] = None) -> Optional[dict]:
    """Case-insensitive exact match against the configured diesel item names."""
    cleaned = (
        normalize_diesel_cash_item_names(names)
        if names is not None
        else list(DIESEL_CASH_CATEGORIES)
    )
    if not cleaned:
        return None
    # Longer names first so "Diesel Cash" is not shadowed by "Diesel".
    alternation = "|".join(
        re.escape(n) for n in sorted(cleaned, key=len, reverse=True)
    )
    return {"$regex": f"^(?:{alternation})$", "$options": "i"}


def is_diesel_cash_item(
    name: Optional[str],
    item_names: Optional[Sequence[str]] = None,
) -> bool:
    """True when *name* is one of the configured diesel item names."""
    if not (name or "").strip():
        return False
    name_filter = _diesel_cash_name_filter(item_names)
    if not name_filter:
        return False
    return bool(re.fullmatch(name_filter["$regex"], str(name), re.IGNORECASE))


def _diesel_cash_verified_match(names: Optional[Sequence[str]] = None) -> dict:
    """``verified`` + item/category_name match (source-agnostic: cashier or import)."""
    query: dict = {"verified": True}
    name_filter = _diesel_cash_name_filter(names)
    if name_filter is None:
        query["category_name"] = {"$in": []}
        return query
    query["$or"] = [
        {"category_name": name_filter},
        {"item": name_filter},
    ]
    return query


def _build_diesel_cash_query(
    year: Optional[int],
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item_names: Optional[Sequence[str]] = None,
) -> dict:
    query: dict = _diesel_cash_verified_match(item_names)

    _year = int(year or 0)
    if _year > 0:
        if month and 1 <= month <= 12:
            last_day = calendar.monthrange(_year, month)[1]
            start = datetime(_year, month, 1)
            end = datetime(_year, month, last_day, 23, 59, 59)
        else:
            start = datetime(_year, 1, 1)
            end = datetime(_year, 12, 31, 23, 59, 59)
        if date_from is not None:
            start = max(start, date_from.replace(hour=0, minute=0, second=0, microsecond=0))
        if date_to is not None:
            end = min(end, date_to.replace(hour=23, minute=59, second=59, microsecond=0))
        query["date"] = {"$gte": start, "$lte": end}
    else:
        clause = _date_range_clause("date", date_from, date_to)
        if clause:
            query.update(clause)

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
    match = _diesel_cash_verified_match(await get_diesel_cash_item_names())
    match["date"] = {
        "$gte": datetime(year, 1, 1),
        "$lte": datetime(year, 12, 31, 23, 59, 59),
    }
    pipeline = [
        {"$match": match},
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
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    sort_field: str = "date",
    sort_asc: bool = False,
    limit: int = 50,
    skip: int = 0,
) -> List[Transaction]:
    db = get_db()
    query = _build_diesel_cash_query(
        year, month, search, truck, receipt, date_from, date_to,
        item_names=await get_diesel_cash_item_names(),
    )
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
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    db = get_db()
    query = _build_diesel_cash_query(
        year, month, search, truck, receipt, date_from, date_to,
        item_names=await get_diesel_cash_item_names(),
    )
    return await db.transactions.count_documents(query)


async def get_diesel_cash_totals(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    db = get_db()
    query = _build_diesel_cash_query(
        year, month, search, truck, receipt, date_from, date_to,
        item_names=await get_diesel_cash_item_names(),
    )
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


def _parse_toll_date(val) -> Optional[datetime]:
    """Best-effort parse of a toll/payment/sales date from import (incl. Excel serials)."""
    return parse_excel_date(val)


def _toll_month_regex_patterns(year: int, month: int) -> List[str]:
    """Regex fragments matching common toll_date string formats for a month."""
    abbr = calendar.month_abbr[month]
    full = calendar.month_name[month]
    y, m2 = str(year), f"{month:02d}"
    return [
        rf"{y}-{m2}", rf"{y}/{m2}", rf"{m2}/{y}", rf"{m2}-{y}",
        rf"\b{abbr}\b.*{y}", rf"\b{full}\b.*{y}",
        rf"{y}.*\b{abbr}\b", rf"{y}.*\b{full}\b",
    ]


def _toll_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering toll records by calendar year and optional month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"transaction_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"transaction_date": {"$exists": False}},
                    {"transaction_date": None},
                ]},
                {"$or": [{"toll_date": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _toll_plaza_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Build a MongoDB query for all Toll Plaza records (cross-upload)."""
    clauses: List[dict] = [{"feed_type": "toll_plaza"}]
    if search.strip():
        s = re.escape(search.strip())
        clauses.append({"$or": [
            {"vehicle_reg":  {"$regex": s, "$options": "i"}},
            {"toll_plaza":   {"$regex": s, "$options": "i"}},
            {"receipt_no":   {"$regex": s, "$options": "i"}},
            {"cashier_name": {"$regex": s, "$options": "i"}},
            {"client_name":  {"$regex": s, "$options": "i"}},
            {"card_no":      {"$regex": s, "$options": "i"}},
        ]})
    if year > 0:
        clauses.append(_toll_date_filter(year, month))
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_toll_plaza_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated Toll Plaza records across all uploads."""
    db = get_db()
    query = _toll_plaza_all_query(search, year, month, date_from, date_to)
    cursor = (
        db.imported_feeds
        .find(query)
        .sort([("transaction_date", -1), ("toll_date", -1), ("import_date", -1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_toll_plaza_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    """Count Toll Plaza records across all uploads."""
    db = get_db()
    return await db.imported_feeds.count_documents(
        _toll_plaza_all_query(search, year, month, date_from, date_to)
    )


async def get_toll_plaza_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Return record count and total ZMW for filtered Toll Plaza records."""
    db = get_db()
    pipeline = [
        {"$match": _toll_plaza_all_query(search, year, month, date_from, date_to)},
        {"$group": {
            "_id": None,
            "count":     {"$sum": 1},
            "total_zmw": {"$sum": _safe_double("tender_amount")},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"count": 0, "total_zmw": 0.0}


async def _feed_available_years(feed_type: str, date_field: str) -> List[int]:
    """Return distinct calendar years present in uploaded records, newest first."""
    db = get_db()
    years: set[int] = set()

    pipeline = [
        {"$match": {
            "feed_type": feed_type,
            "transaction_date": {"$exists": True, "$ne": None},
        }},
        {"$group": {"_id": {"$year": "$transaction_date"}}},
    ]
    for doc in await db.imported_feeds.aggregate(pipeline).to_list(length=None):
        yr = doc.get("_id")
        if isinstance(yr, int) and 1990 <= yr <= 2100:
            years.add(yr)

    legacy_dates = await db.imported_feeds.distinct(
        date_field,
        {
            "feed_type": feed_type,
            "$or": [
                {"transaction_date": {"$exists": False}},
                {"transaction_date": None},
            ],
        },
    )
    for val in legacy_dates:
        parsed = _parse_toll_date(val)
        if parsed and 1990 <= parsed.year <= 2100:
            years.add(parsed.year)

    return sorted(years, reverse=True)


async def get_toll_plaza_available_years() -> List[int]:
    """Years that appear in uploaded Toll Plaza records."""
    return await _feed_available_years("toll_plaza", "toll_date")


async def get_parking_congo_available_years() -> List[int]:
    """Years that appear in uploaded Parking Congo records."""
    return await _feed_available_years("parking_congo", "payment_date")


_IMPORT_UPPER_SKIP = frozenset({
    "feed_type", "upload_id", "source_filename", "upload_label", "content_hash",
    "import_date", "created_at",
    "transaction_date", "expense_date", "expense_type", "row_index", "_id",
    "installation_label", "total_payable_label", "skipped_row_id",
})


def _uppercase_import_text(doc: dict) -> None:
    """Uppercase string fields on an imported record (in place)."""
    for key, val in doc.items():
        if key in _IMPORT_UPPER_SKIP:
            continue
        if isinstance(val, str):
            doc[key] = val.upper()


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
        if doc.get("feed_type") == "toll_plaza":
            normalize_date_fields(doc, "toll_date", store_as="transaction_date")
        elif doc.get("feed_type") == "parking_congo":
            normalize_date_fields(doc, "payment_date", store_as="transaction_date")
            # Deposits are account credits (no truck) — tag for Deposited tab
            tt = str(doc.get("transaction_type", "") or "").strip().lower()
            if tt == "deposit" or doc.get("is_deposit"):
                doc["is_deposit"] = True
                vn = str(doc.get("vehicle_no", "") or "").strip()
                if vn.lower() in ("", "-", "–", "—", "−", ".", "n/a", "na", "none"):
                    doc["vehicle_no"] = ""
                for _k in ("direction", "gate_in"):
                    val = str(doc.get(_k, "") or "").strip()
                    if val.lower() in ("", "-", "–", "—", "−", ".", "n/a", "na", "none"):
                        doc[_k] = ""
        elif doc.get("feed_type") == "rahntech":
            normalize_date_fields(doc, "sales_date", store_as="transaction_date")
        elif doc.get("feed_type") == "zambia_parking":
            # Prefer existing transaction_date; fall back to Date column (serials OK).
            if isinstance(doc.get("transaction_date"), datetime):
                pretty = format_excel_date(doc["transaction_date"], "%d %b %Y")
                if pretty:
                    doc["date"] = pretty
            else:
                normalize_date_fields(
                    doc, "date", "transaction_date", store_as="transaction_date",
                )
        elif str(doc.get("feed_type", "")).startswith("diesel_"):
            normalize_date_fields(doc, "date", store_as="transaction_date")
            apply_diesel_computed_fields(doc)
        _uppercase_import_text(doc)
        docs.append(doc)

    # Stamp whole-batch content hash on Zambia Parking / diesel rows
    # (exact-file gate). Keep a pre-set hash (from the full file before
    # truck-gate filtering).
    zambia_docs = [d for d in docs if d.get("feed_type") == "zambia_parking"]
    if zambia_docs and not str(zambia_docs[0].get("content_hash") or "").strip():
        batch_hash = zambia_batch_content_hash(zambia_docs)
        for d in zambia_docs:
            d["content_hash"] = batch_hash

    diesel_docs = [d for d in docs if str(d.get("feed_type", "")).startswith("diesel_")]
    if diesel_docs and not str(diesel_docs[0].get("content_hash") or "").strip():
        batch_hash = diesel_batch_content_hash(diesel_docs)
        for d in diesel_docs:
            d["content_hash"] = batch_hash

    from tahmeed.db.import_idempotency import insert_many_idempotent

    inserted, _dupes = await insert_many_idempotent(db.imported_feeds, docs)
    return inserted


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


async def delete_toll_plaza_upload(upload_id: str) -> int:
    """Delete every Toll Plaza record belonging to one upload batch."""
    if not upload_id:
        return 0
    db = get_db()
    result = await db.imported_feeds.delete_many(
        {"feed_type": "toll_plaza", "upload_id": upload_id}
    )
    return result.deleted_count


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


def _parking_month_regex_patterns(year: int, month: int) -> List[str]:
    """Regex fragments matching common payment_date string formats for a month."""
    return _toll_month_regex_patterns(year, month)


def _parking_congo_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering Parking Congo records by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _parking_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"transaction_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"transaction_date": {"$exists": False}},
                    {"transaction_date": None},
                ]},
                {"$or": [{"payment_date": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _parking_congo_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Build a MongoDB query for all Parking Congo records (cross-upload)."""
    clauses: List[dict] = [{"feed_type": "parking_congo"}]
    if search.strip():
        s = re.escape(search.strip())
        clauses.append({"$or": [
            {"vehicle_no":          {"$regex": s, "$options": "i"}},
            {"ledger_id":           {"$regex": s, "$options": "i"}},
            {"transaction_type":    {"$regex": s, "$options": "i"}},
            {"cashier":             {"$regex": s, "$options": "i"}},
            {"transaction_details": {"$regex": s, "$options": "i"}},
            {"direction":           {"$regex": s, "$options": "i"}},
        ]})
    if year > 0:
        clauses.append(_parking_congo_date_filter(year, month))
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_parking_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated Parking Congo records across all uploads."""
    db = get_db()
    query = _parking_congo_all_query(search, year, month, date_from, date_to)
    cursor = (
        db.imported_feeds
        .find(query)
        .sort([("transaction_date", -1), ("payment_date", -1), ("import_date", -1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_parking_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    """Count Parking Congo records across all uploads."""
    db = get_db()
    return await db.imported_feeds.count_documents(
        _parking_congo_all_query(search, year, month, date_from, date_to)
    )


async def get_parking_congo_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Return record count and total amount for filtered Parking Congo records."""
    db = get_db()
    pipeline = [
        {"$match": _parking_congo_all_query(search, year, month, date_from, date_to)},
        {"$group": {
            "_id": None,
            "count":  {"$sum": 1},
            "amount": {"$sum": _safe_double("amount")},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"count": 0, "amount": 0.0}


async def delete_parking_congo_upload(upload_id: str) -> int:
    """Delete every Parking Congo record belonging to one upload batch."""
    if not upload_id:
        return 0
    db = get_db()
    result = await db.imported_feeds.delete_many(
        {"feed_type": "parking_congo", "upload_id": upload_id}
    )
    return result.deleted_count


def _parking_congo_deposit_clause() -> dict:
    """Match Parking Congo deposit rows (tagged or by Type)."""
    return {"$or": [
        {"is_deposit": True},
        {"transaction_type": {"$regex": r"^deposit$", "$options": "i"}},
    ]}


def _parking_congo_deposits_query(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """MongoDB query for Parking Congo deposit records."""
    clauses: List[dict] = [
        {"feed_type": "parking_congo"},
        _parking_congo_deposit_clause(),
    ]
    if search.strip():
        s = re.escape(search.strip())
        clauses.append({"$or": [
            {"ledger_id":           {"$regex": s, "$options": "i"}},
            {"cashier":             {"$regex": s, "$options": "i"}},
            {"transaction_details": {"$regex": s, "$options": "i"}},
            {"source_filename":     {"$regex": s, "$options": "i"}},
            {"payment_date":        {"$regex": s, "$options": "i"}},
        ]})
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    return {"$and": clauses}


async def get_parking_congo_deposits(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated Parking Congo deposit records."""
    db = get_db()
    query = _parking_congo_deposits_query(search, date_from, date_to)
    cursor = (
        db.imported_feeds
        .find(query)
        .sort([("transaction_date", -1), ("payment_date", -1), ("import_date", -1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_parking_congo_deposits(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    """Count Parking Congo deposit records."""
    db = get_db()
    return await db.imported_feeds.count_documents(
        _parking_congo_deposits_query(search, date_from, date_to)
    )


async def get_parking_congo_deposit_totals(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Return count and total amount for filtered Parking Congo deposits."""
    db = get_db()
    pipeline = [
        {"$match": _parking_congo_deposits_query(search, date_from, date_to)},
        {"$group": {
            "_id": None,
            "count":  {"$sum": 1},
            "amount": {"$sum": _safe_double("amount")},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"count": 0, "amount": 0.0}


async def get_parking_congo_upload_by_id(upload_id: str) -> Optional[dict]:
    """Return a browse-style summary doc for one Parking Congo upload batch."""
    if not upload_id:
        return None
    db = get_db()
    pipeline = [
        {"$match": {"feed_type": "parking_congo", "upload_id": upload_id}},
        {"$group": {
            "_id":             "$upload_id",
            "source_filename": {"$first": "$source_filename"},
            "import_date":     {"$first": "$import_date"},
            "record_count":    {"$sum": 1},
            "min_date":        {"$min": "$payment_date"},
            "max_date":        {"$max": "$payment_date"},
        }},
    ]
    rows = await db.imported_feeds.aggregate(pipeline).to_list(1)
    return rows[0] if rows else None


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


def _rahntech_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering RahnTech records by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"transaction_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"transaction_date": {"$exists": False}},
                    {"transaction_date": None},
                ]},
                {"$or": [{"sales_date": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _rahntech_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Build a MongoDB query for all RahnTech records (cross-upload)."""
    clauses: List[dict] = [{"feed_type": "rahntech"}]
    if search.strip():
        s = re.escape(search.strip())
        clauses.append({"$or": [
            {"truck_number":  {"$regex": s, "$options": "i"}},
            {"driver_name":   {"$regex": s, "$options": "i"}},
            {"trip_number":   {"$regex": s, "$options": "i"}},
            {"device_number": {"$regex": s, "$options": "i"}},
            {"do_number":     {"$regex": s, "$options": "i"}},
            {"sales_date":    {"$regex": s, "$options": "i"}},
        ]})
    if year > 0:
        clauses.append(_rahntech_date_filter(year, month))
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_rahntech_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated RahnTech records across all uploads."""
    db = get_db()
    query = _rahntech_all_query(search, year, month, date_from, date_to)
    cursor = (
        db.imported_feeds
        .find(query)
        .sort([("transaction_date", -1), ("sales_date", -1), ("import_date", -1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_rahntech_all_records(search: str = "", year: int = 0, month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    """Count RahnTech records across all uploads."""
    db = get_db()
    return await db.imported_feeds.count_documents(_rahntech_all_query(search, year, month, date_from, date_to))


async def get_rahntech_all_totals(search: str = "", year: int = 0, month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Return record count for filtered RahnTech records."""
    db = get_db()
    pipeline = [
        {"$match": _rahntech_all_query(search, year, month, date_from, date_to)},
        {"$group": {
            "_id": None,
            "count": {"$sum": 1},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"count": 0}


async def get_rahntech_available_years() -> List[int]:
    """Years that appear in uploaded RahnTech records."""
    return await _feed_available_years("rahntech", "sales_date")


async def delete_rahntech_upload(upload_id: str) -> int:
    """Delete every RahnTech record belonging to one upload batch."""
    if not upload_id:
        return 0
    db = get_db()
    result = await db.imported_feeds.delete_many({"feed_type": "rahntech", "upload_id": upload_id})
    return result.deleted_count


# ── Zambia Parking — weekly statement import (sheet tab = week label) ─────────

def _norm_sheet_label(label: str) -> str:
    return str(label or "").strip().upper()


def _zambia_hash_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value).strip().upper()


def zambia_batch_content_hash(records: List[dict]) -> str:
    """SHA-256 of the exact ordered statement rows (whole-file duplicate gate).

    Does not use ticket-only matching — the full row set must match.
    """
    lines: List[str] = []
    for rec in records:
        lines.append("|".join([
            _zambia_hash_field(rec.get("date")),
            _zambia_hash_field(rec.get("type")),
            _zambia_hash_field(rec.get("plate_num")),
            _zambia_hash_field(rec.get("ticket_no")),
            _zambia_hash_field(rec.get("debit")),
            _zambia_hash_field(rec.get("credit")),
            _zambia_hash_field(rec.get("balance")),
            _zambia_hash_field(rec.get("heading_to")),
        ]))
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def zambia_sheet_exists(sheet_label: str) -> bool:
    """True when a weekly sheet tab was already imported (case-insensitive)."""
    if not sheet_label.strip():
        return False
    db = get_db()
    count = await db.imported_feeds.count_documents({
        "feed_type": "zambia_parking",
        "sheet_label": {"$regex": f"^{re.escape(sheet_label.strip())}$", "$options": "i"},
    })
    return count > 0


async def zambia_existing_sheet_labels(labels: List[str]) -> set:
    """Return sheet tab names from *labels* that were already imported (case-insensitive)."""
    clean = [l.strip() for l in labels if l and str(l).strip()]
    if not clean:
        return set()
    db = get_db()
    clauses = [
        {"sheet_label": {"$regex": f"^{re.escape(l)}$", "$options": "i"}}
        for l in clean
    ]
    found = await db.imported_feeds.distinct(
        "sheet_label",
        {"feed_type": "zambia_parking", "$or": clauses},
    )
    found_upper = {_norm_sheet_label(s) for s in found if s}
    return {l for l in clean if _norm_sheet_label(l) in found_upper}


async def find_zambia_uploads_by_content_hash(content_hash: str) -> List[dict]:
    """Return existing Zambia Parking upload summaries that match *content_hash*."""
    if not content_hash:
        return []
    db = get_db()

    # Fast path: rows stamped with content_hash on save.
    stamped_ids = await db.imported_feeds.distinct(
        "upload_id",
        {"feed_type": "zambia_parking", "content_hash": content_hash},
    )
    matches: Dict[str, dict] = {}
    uploads = await get_zambia_parking_uploads()
    by_id = {str(u.get("_id") or ""): u for u in uploads}

    for uid in stamped_ids:
        key = str(uid or "").strip()
        if not key:
            continue
        up = by_id.get(key)
        if up:
            matches[key] = {
                "upload_id": key,
                "sheet_label": up.get("sheet_label") or "",
                "source_filename": up.get("source_filename") or "",
                "import_date": up.get("import_date"),
                "record_count": int(up.get("record_count", 0) or 0),
                "content_hash": content_hash,
            }

    # Legacy uploads (no content_hash): re-hash each batch once.
    for up in uploads:
        uid = str(up.get("_id") or "").strip()
        if not uid or uid in matches:
            continue
        rows = await db.imported_feeds.find(
            {"feed_type": "zambia_parking", "upload_id": uid},
            {
                "date": 1, "type": 1, "plate_num": 1, "ticket_no": 1,
                "debit": 1, "credit": 1, "balance": 1, "heading_to": 1,
                "row_index": 1, "content_hash": 1,
            },
        ).sort([("row_index", 1)]).to_list(length=None)
        if not rows:
            continue
        # If any row already has a different stored hash, skip; if same, caught above.
        stored = {
            str(r.get("content_hash") or "").strip()
            for r in rows
            if str(r.get("content_hash") or "").strip()
        }
        if stored:
            continue
        if zambia_batch_content_hash(rows) == content_hash:
            matches[uid] = {
                "upload_id": uid,
                "sheet_label": up.get("sheet_label") or "",
                "source_filename": up.get("source_filename") or "",
                "import_date": up.get("import_date"),
                "record_count": int(up.get("record_count", 0) or 0),
                "content_hash": content_hash,
            }

    return list(matches.values())


async def zambia_exact_content_already_uploaded(records: List[dict]) -> List[dict]:
    """If this exact statement body was imported before, return matching upload(s)."""
    if not records:
        return []
    return await find_zambia_uploads_by_content_hash(zambia_batch_content_hash(records))


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


def _zambia_parking_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering Zambia Parking records by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"transaction_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"transaction_date": {"$exists": False}},
                    {"transaction_date": None},
                ]},
                {"$or": [{"date": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _zambia_parking_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    credit_only: bool = False,
) -> dict:
    """Build a MongoDB query for Zambia Parking rows (cross-upload)."""
    clauses: List[dict] = [{"feed_type": "zambia_parking"}]
    if credit_only:
        clauses.append({"credit": {"$gt": 0}})
    if search.strip():
        s = re.escape(search.strip())
        clauses.append({"$or": [
            {"plate_num":  {"$regex": s, "$options": "i"}},
            {"ticket_no":  {"$regex": s, "$options": "i"}},
            {"heading_to": {"$regex": s, "$options": "i"}},
            {"type":       {"$regex": s, "$options": "i"}},
            {"date":       {"$regex": s, "$options": "i"}},
        ]})
    if year > 0:
        clauses.append(_zambia_parking_date_filter(year, month))
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_zambia_parking_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    credit_only: bool = False,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated Zambia Parking rows across all uploads."""
    db = get_db()
    query = _zambia_parking_all_query(search, year, month, date_from, date_to, credit_only)
    cursor = (
        db.imported_feeds
        .find(query)
        .sort([("transaction_date", -1), ("date", -1), ("import_date", -1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_zambia_parking_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    credit_only: bool = False,
) -> int:
    """Count Zambia Parking rows across all uploads."""
    db = get_db()
    return await db.imported_feeds.count_documents(
        _zambia_parking_all_query(search, year, month, date_from, date_to, credit_only)
    )


async def get_zambia_parking_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    credit_only: bool = False,
) -> dict:
    """Return record count and debit/credit totals for filtered Zambia Parking rows."""
    db = get_db()
    pipeline = [
        {"$match": _zambia_parking_all_query(search, year, month, date_from, date_to, credit_only)},
        {"$group": {
            "_id": None,
            "count":        {"$sum": 1},
            "total_debit":  {"$sum": _safe_double("debit")},
            "total_credit": {"$sum": _safe_double("credit")},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        row = result[0]
        row["balance_zmw"] = (
            float(row.get("total_credit", 0) or 0)
            - float(row.get("total_debit", 0) or 0)
        )
        return row
    return {"count": 0, "total_debit": 0.0, "total_credit": 0.0, "balance_zmw": 0.0}


async def get_zambia_parking_available_years() -> List[int]:
    """Years present in uploaded Zambia Parking records."""
    return await _feed_available_years("zambia_parking", "date")


async def delete_zambia_parking_upload(upload_id: str) -> int:
    """Delete every Zambia Parking row belonging to one upload batch."""
    if not upload_id:
        return 0
    db = get_db()
    result = await db.imported_feeds.delete_many({
        "feed_type": "zambia_parking",
        "upload_id": upload_id,
    })
    return result.deleted_count


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


def _diesel_computed_amount() -> dict:
    """Litres × rate — Excel totals are not trusted."""
    return {"$multiply": [_safe_double("ltrs"), _safe_double("price_per_ltr")]}


async def get_diesel_totals(feed_type: str) -> dict:
    db = get_db()
    pipeline = [
        {"$match": {"feed_type": feed_type}},
        {"$group": {
            "_id":          None,
            "ltrs":         {"$sum": _safe_double("ltrs")},
            "total_amount": {"$sum": _diesel_computed_amount()},
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
            "upload_label":    {"$first": "$upload_label"},
            "sheet_label":     {"$first": "$sheet_label"},
            "import_date":     {"$first": "$import_date"},
            "record_count":    {"$sum": 1},
            "ltrs":            {"$sum": _safe_double("ltrs")},
            "total_amount":    {"$sum": _diesel_computed_amount()},
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
            {"upload_label": {"$regex": s, "$options": "i"}},
            {"source_filename": {"$regex": s, "$options": "i"}},
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
            "total_amount": {"$sum": _diesel_computed_amount()},
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


def _diesel_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering diesel records by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"transaction_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"transaction_date": {"$exists": False}},
                    {"transaction_date": None},
                ]},
                {"$or": [{"date": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def diesel_display_label(rec: dict) -> str:
    """All Entries FILE NAME value: import description, else Excel file name."""
    label = str(rec.get("upload_label") or "").strip()
    if label:
        return label
    return str(rec.get("source_filename") or "").strip()


def unique_diesel_file_labels(labels: Iterable[str]) -> List[str]:
    """Case-insensitive unique file-name / description labels, sorted."""
    seen: Set[str] = set()
    out: List[str] = []
    for raw in labels:
        label = str(raw or "").strip()
        key = label.lower()
        if label and key not in seen:
            seen.add(key)
            out.append(label)
    out.sort(key=str.lower)
    return out


def _diesel_empty_upload_label_clause() -> dict:
    """Match rows whose import description is missing or blank."""
    return {"$or": [
        {"upload_label": {"$exists": False}},
        {"upload_label": None},
        {"upload_label": {"$regex": r"^\s*$"}},
    ]}


def _diesel_file_labels_clause(file_labels: Optional[Sequence[str]] = None) -> Optional[dict]:
    """Match rows whose displayed FILE NAME is any of the selected labels."""
    labels = unique_diesel_file_labels(file_labels or [])
    if not labels:
        return None
    empty = _diesel_empty_upload_label_clause()
    branches: List[dict] = []
    for label in labels:
        exact = {"$regex": f"^{re.escape(label)}$", "$options": "i"}
        branches.append({"upload_label": exact})
        branches.append({"$and": [empty, {"source_filename": exact}]})
    if len(branches) == 1:
        return branches[0]
    return {"$or": branches}


def _diesel_all_query(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    file_labels: Optional[Sequence[str]] = None,
) -> dict:
    """Build a MongoDB query for diesel rows across all uploads."""
    clauses: List[dict] = [{"feed_type": feed_type}]
    if search.strip():
        s = re.escape(search.strip())
        clauses.append({"$or": [
            {"truck_no":        {"$regex": s, "$options": "i"}},
            {"lpo_no":          {"$regex": s, "$options": "i"}},
            {"do_sdo_no":       {"$regex": s, "$options": "i"}},
            {"clients_name":    {"$regex": s, "$options": "i"}},
            {"destinations":    {"$regex": s, "$options": "i"}},
            {"diesel_at":       {"$regex": s, "$options": "i"}},
            {"ownership":       {"$regex": s, "$options": "i"}},
            {"source_filename": {"$regex": s, "$options": "i"}},
            {"upload_label":    {"$regex": s, "$options": "i"}},
            {"date":            {"$regex": s, "$options": "i"}},
        ]})
    if year > 0:
        clauses.append(_diesel_date_filter(year, month))
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    labels_clause = _diesel_file_labels_clause(file_labels)
    if labels_clause:
        clauses.append(labels_clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_diesel_file_labels(feed_type: str) -> List[str]:
    """Distinct FILE NAME / description labels for a diesel station."""
    db = get_db()
    pipeline = [
        {"$match": {"feed_type": feed_type}},
        {"$project": {
            "label": {
                "$let": {
                    "vars": {
                        "ul": {"$trim": {"input": {"$ifNull": ["$upload_label", ""]}}},
                        "fn": {"$trim": {"input": {"$ifNull": ["$source_filename", ""]}}},
                    },
                    "in": {"$cond": [{"$ne": ["$$ul", ""]}, "$$ul", "$$fn"]},
                }
            }
        }},
        {"$match": {"label": {"$nin": ["", None]}}},
        {"$group": {"_id": "$label"}},
        {"$sort": {"_id": 1}},
    ]
    docs = await db.imported_feeds.aggregate(pipeline).to_list(length=None)
    return unique_diesel_file_labels(
        str(d.get("_id") or "") for d in docs
    )


async def get_diesel_all_records(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    file_labels: Optional[Sequence[str]] = None,
) -> list:
    """Return paginated diesel rows across all uploads."""
    db = get_db()
    cursor = (
        db.imported_feeds
        .find(_diesel_all_query(
            feed_type, search, year, month, date_from, date_to, file_labels,
        ))
        .sort([("transaction_date", -1), ("date", -1), ("import_date", -1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_diesel_all_records(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    file_labels: Optional[Sequence[str]] = None,
) -> int:
    """Count diesel rows across all uploads."""
    db = get_db()
    return await db.imported_feeds.count_documents(
        _diesel_all_query(
            feed_type, search, year, month, date_from, date_to, file_labels,
        )
    )


async def get_diesel_all_totals(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    file_labels: Optional[Sequence[str]] = None,
) -> dict:
    """Return row count and litre/amount totals for filtered diesel rows."""
    db = get_db()
    pipeline = [
        {"$match": _diesel_all_query(
            feed_type, search, year, month, date_from, date_to, file_labels,
        )},
        {"$group": {
            "_id":          None,
            "count":        {"$sum": 1},
            "ltrs":         {"$sum": _safe_double("ltrs")},
            "total_amount": {"$sum": _diesel_computed_amount()},
        }},
    ]
    result = await db.imported_feeds.aggregate(pipeline).to_list(1)
    if result:
        return result[0]
    return {"count": 0, "ltrs": 0.0, "total_amount": 0.0}


async def get_diesel_available_years(feed_type: str) -> List[int]:
    """Years that appear in uploaded diesel records."""
    return await _feed_available_years(feed_type, "date")


# Fields hashed for the diesel whole-file duplicate gate (order matters).
_DIESEL_HASH_FIELDS = (
    "date", "lpo_no", "do_sdo_no", "diesel_at", "ownership",
    "clients_name", "destinations", "truck_no", "ltrs",
    "price_per_ltr", "total_amount",
)


def diesel_batch_content_hash(records: List[dict]) -> str:
    """SHA-256 of the exact ordered diesel rows (whole-file duplicate gate)."""
    lines: List[str] = []
    for rec in records:
        lines.append("|".join(
            _zambia_hash_field(rec.get(field)) for field in _DIESEL_HASH_FIELDS
        ))
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _diesel_filename_sheet_query(
    feed_type: str,
    source_filename: str,
    sheet_label: str = "",
) -> Optional[dict]:
    """Match a prior diesel upload by file name + sheet tab (case-insensitive)."""
    fname = str(source_filename or "").strip()
    if not feed_type or not fname:
        return None
    query: dict = {
        "feed_type": feed_type,
        "source_filename": {
            "$regex": f"^{re.escape(fname)}$",
            "$options": "i",
        },
    }
    sheet = str(sheet_label or "").strip()
    if sheet:
        query["sheet_label"] = {
            "$regex": f"^{re.escape(sheet)}$",
            "$options": "i",
        }
    else:
        query["$or"] = [
            {"sheet_label": {"$in": ["", None]}},
            {"sheet_label": {"$exists": False}},
        ]
    return query


def _diesel_upload_summary(up: dict, *, content_hash: str = "") -> dict:
    uid = str(up.get("upload_id") or up.get("_id") or "").strip()
    summary = {
        "upload_id": uid,
        "sheet_label": up.get("sheet_label") or "",
        "source_filename": up.get("source_filename") or "",
        "import_date": up.get("import_date"),
        "record_count": int(up.get("record_count", 0) or 0),
    }
    if content_hash:
        summary["content_hash"] = content_hash
    return summary


async def diesel_filename_already_uploaded(
    feed_type: str,
    source_filename: str,
    sheet_label: str = "",
) -> List[dict]:
    """Prior diesel batches with the same file name and sheet tab."""
    query = _diesel_filename_sheet_query(feed_type, source_filename, sheet_label)
    if not query:
        return []
    db = get_db()
    pipeline = [
        {"$match": query},
        {"$group": {
            "_id":             "$upload_id",
            "source_filename": {"$first": "$source_filename"},
            "sheet_label":     {"$first": "$sheet_label"},
            "import_date":     {"$first": "$import_date"},
            "record_count":    {"$sum": 1},
        }},
        {"$sort": {"import_date": -1}},
    ]
    found = await db.imported_feeds.aggregate(pipeline).to_list(length=None)
    matches: List[dict] = []
    for up in found:
        uid = str(up.get("_id") or "").strip()
        if not uid:
            continue
        matches.append(_diesel_upload_summary({**up, "upload_id": uid}))
    return matches


async def find_diesel_uploads_by_content_hash(
    feed_type: str, content_hash: str,
) -> List[dict]:
    """Return existing diesel upload summaries that match *content_hash*."""
    if not feed_type or not content_hash:
        return []
    db = get_db()

    stamped_ids = await db.imported_feeds.distinct(
        "upload_id",
        {"feed_type": feed_type, "content_hash": content_hash},
    )
    matches: Dict[str, dict] = {}
    uploads = await get_diesel_uploads(feed_type)
    by_id = {str(u.get("_id") or ""): u for u in uploads}

    for uid in stamped_ids:
        key = str(uid or "").strip()
        if not key:
            continue
        up = by_id.get(key)
        if up:
            matches[key] = _diesel_upload_summary(
                {**up, "upload_id": key}, content_hash=content_hash,
            )

    # Legacy uploads (no content_hash): re-hash each batch once.
    projection = {field: 1 for field in _DIESEL_HASH_FIELDS}
    projection["content_hash"] = 1
    for up in uploads:
        uid = str(up.get("_id") or "").strip()
        if not uid or uid in matches:
            continue
        rows = await db.imported_feeds.find(
            {"feed_type": feed_type, "upload_id": uid},
            projection,
        ).to_list(length=None)
        if not rows:
            continue
        stored = {
            str(r.get("content_hash") or "").strip()
            for r in rows
            if str(r.get("content_hash") or "").strip()
        }
        if stored:
            continue
        if diesel_batch_content_hash(rows) == content_hash:
            matches[uid] = _diesel_upload_summary(
                {**up, "upload_id": uid}, content_hash=content_hash,
            )

    return list(matches.values())


async def diesel_already_uploaded(
    feed_type: str,
    records: List[dict],
    source_filename: str = "",
    sheet_label: str = "",
) -> List[dict]:
    """Matching prior diesel uploads by file/sheet identity or exact contents."""
    matches: Dict[str, dict] = {}
    for up in await diesel_filename_already_uploaded(
        feed_type, source_filename, sheet_label,
    ):
        uid = str(up.get("upload_id") or "").strip()
        if uid:
            matches[uid] = up
    if records:
        file_hash = diesel_batch_content_hash(records)
        for up in await find_diesel_uploads_by_content_hash(feed_type, file_hash):
            uid = str(up.get("upload_id") or "").strip()
            if uid:
                matches[uid] = up
    return list(matches.values())


# ── Ahmed Kimvi — Excel import (last sheet per workbook) ─────────────────────

async def kimvi_sheet_exists(sheet_label: str) -> bool:
    """True when a sheet with this tab name was already imported (case-insensitive)."""
    if not sheet_label.strip():
        return False
    db = get_db()
    count = await db.separate_expenses.count_documents({
        "expense_type": "ahmed_kimvi",
        "sheet_label": {
            "$regex": f"^{re.escape(sheet_label.strip())}$",
            "$options": "i",
        },
    })
    return count > 0


async def kimvi_existing_sheet_labels(labels: List[str]) -> set:
    """Return sheet tab names from *labels* that were already imported (case-insensitive)."""
    clean = [l.strip() for l in labels if l and str(l).strip()]
    if not clean:
        return set()
    db = get_db()
    clauses = [
        {"sheet_label": {"$regex": f"^{re.escape(l)}$", "$options": "i"}}
        for l in clean
    ]
    found = await db.separate_expenses.distinct(
        "sheet_label",
        {"expense_type": "ahmed_kimvi", "$or": clauses},
    )
    found_upper = {_norm_sheet_label(s) for s in found if s}
    return {l for l in clean if _norm_sheet_label(l) in found_upper}


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
        if not isinstance(doc.get("expense_date"), datetime):
            normalize_date_fields(
                doc, "date_str", "expense_date", store_as="expense_date",
                display_fmt="%d %b %Y",
            )
            if isinstance(doc.get("expense_date"), datetime):
                doc["date_str"] = format_excel_date(doc["expense_date"], "%d %b %Y")
        _uppercase_import_text(doc)
        docs.append(doc)
    from tahmeed.db.import_idempotency import insert_many_idempotent

    inserted, _dupes = await insert_many_idempotent(db.separate_expenses, docs)
    return inserted


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


def _kimvi_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering Ahmed Kimvi rows by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"expense_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"expense_date": {"$exists": False}},
                    {"expense_date": None},
                ]},
                {"$or": [{"date_str": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _kimvi_entries_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
) -> dict:
    """Build a MongoDB query for Ahmed Kimvi rows (cross-upload)."""
    clauses: List[dict] = [{"expense_type": "ahmed_kimvi"}]
    if money_in_only:
        clauses.append({"amount_usd": {"$lt": 0}})
    if search.strip():
        s = re.escape(search.strip())
        clauses.append({"$or": [
            {"description": {"$regex": s, "$options": "i"}},
            {"truck_no":    {"$regex": s, "$options": "i"}},
            {"date_str":    {"$regex": s, "$options": "i"}},
        ]})
    if year > 0:
        clauses.append(_kimvi_date_filter(year, month))
    clause = _date_range_clause("expense_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_kimvi_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    money_in_only: bool = False,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated Ahmed Kimvi rows across all uploads."""
    db = get_db()
    query = _kimvi_entries_query(search, year, month, date_from, date_to, money_in_only)
    cursor = (
        db.separate_expenses
        .find(query)
        .sort([("expense_date", -1), ("date_str", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_kimvi_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
) -> int:
    """Count Ahmed Kimvi rows across all uploads."""
    db = get_db()
    return await db.separate_expenses.count_documents(
        _kimvi_entries_query(search, year, month, date_from, date_to, money_in_only)
    )


async def get_kimvi_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
) -> dict:
    """Return record count and money in/out totals for filtered Ahmed Kimvi rows."""
    db = get_db()
    pipeline = [
        {"$match": _kimvi_entries_query(search, year, month, date_from, date_to, money_in_only)},
        {"$group": {
            "_id": None,
            "count":     {"$sum": 1},
            "money_in":  {"$sum": {
                "$cond": [
                    {"$lt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                    {"$ifNull": ["$amount_usd", 0]},
                    0,
                ],
            }},
            "money_out": {"$sum": {
                "$cond": [
                    {"$gt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                    {"$ifNull": ["$amount_usd", 0]},
                    0,
                ],
            }},
        }},
    ]
    result = await db.separate_expenses.aggregate(pipeline).to_list(1)
    if result:
        row = result[0]
        row["balance_usd"] = row.get("money_in", 0) + row.get("money_out", 0)
        return row
    return {"count": 0, "money_in": 0.0, "money_out": 0.0, "balance_usd": 0.0}


async def get_kimvi_available_years() -> List[int]:
    """Years present in uploaded Ahmed Kimvi records."""
    db = get_db()
    years: set[int] = set()

    pipeline = [
        {"$match": {
            "expense_type": "ahmed_kimvi",
            "expense_date": {"$exists": True, "$ne": None},
        }},
        {"$group": {"_id": {"$year": "$expense_date"}}},
    ]
    for doc in await db.separate_expenses.aggregate(pipeline).to_list(length=None):
        yr = doc.get("_id")
        if isinstance(yr, int) and 1990 <= yr <= 2100:
            years.add(yr)

    legacy_dates = await db.separate_expenses.distinct(
        "date_str",
        {
            "expense_type": "ahmed_kimvi",
            "$or": [
                {"expense_date": {"$exists": False}},
                {"expense_date": None},
            ],
        },
    )
    for val in legacy_dates:
        parsed = _parse_toll_date(val)
        if parsed and 1990 <= parsed.year <= 2100:
            years.add(parsed.year)

    return sorted(years, reverse=True)


async def delete_kimvi_upload(upload_id: str) -> int:
    """Delete every Ahmed Kimvi row belonging to one upload batch."""
    if not upload_id:
        return 0
    db = get_db()
    result = await db.separate_expenses.delete_many({
        "expense_type": "ahmed_kimvi",
        "upload_id": upload_id,
    })
    return result.deleted_count


# ── Congo Expenses — Excel import (last sheet per workbook) ──────────────────

async def congo_sheet_exists(sheet_label: str) -> bool:
    """True when a sheet with this tab name was already imported (case-insensitive)."""
    if not sheet_label.strip():
        return False
    db = get_db()
    count = await db.separate_expenses.count_documents({
        "expense_type": "congo_expenses",
        "sheet_label": {
            "$regex": f"^{re.escape(sheet_label.strip())}$",
            "$options": "i",
        },
    })
    return count > 0


async def congo_existing_sheet_labels(labels: List[str]) -> set:
    """Return sheet tab names from *labels* that were already imported (case-insensitive)."""
    clean = [l.strip() for l in labels if l and str(l).strip()]
    if not clean:
        return set()
    db = get_db()
    clauses = [
        {"sheet_label": {"$regex": f"^{re.escape(l)}$", "$options": "i"}}
        for l in clean
    ]
    found = await db.separate_expenses.distinct(
        "sheet_label",
        {"expense_type": "congo_expenses", "$or": clauses},
    )
    found_upper = {_norm_sheet_label(s) for s in found if s}
    return {l for l in clean if _norm_sheet_label(l) in found_upper}


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
        if not isinstance(doc.get("expense_date"), datetime):
            normalize_date_fields(
                doc, "date_str", "expense_date", store_as="expense_date",
                display_fmt="%d %b %Y",
            )
            if isinstance(doc.get("expense_date"), datetime):
                doc["date_str"] = format_excel_date(doc["expense_date"], "%d %b %Y")
        _uppercase_import_text(doc)
        docs.append(doc)
    from tahmeed.db.import_idempotency import insert_many_idempotent

    inserted, _dupes = await insert_many_idempotent(db.separate_expenses, docs)
    return inserted


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


def _congo_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering Congo Expenses by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"expense_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"expense_date": {"$exists": False}},
                    {"expense_date": None},
                ]},
                {"$or": [{"date_str": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _congo_entries_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
) -> dict:
    """Build a MongoDB query for Congo Expenses rows (cross-upload)."""
    clauses: List[dict] = [{"expense_type": "congo_expenses"}]
    if money_in_only:
        clauses.append({"amount_usd": {"$lt": 0}})
    if search.strip():
        s = re.escape(search.strip())
        clauses.append({"$or": [
            {"description": {"$regex": s, "$options": "i"}},
            {"truck_no":    {"$regex": s, "$options": "i"}},
            {"lpo_no":      {"$regex": s, "$options": "i"}},
            {"date_str":    {"$regex": s, "$options": "i"}},
        ]})
    if year > 0:
        clauses.append(_congo_date_filter(year, month))
    clause = _date_range_clause("expense_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    money_in_only: bool = False,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated Congo Expenses rows across all uploads."""
    db = get_db()
    query = _congo_entries_query(search, year, month, date_from, date_to, money_in_only)
    cursor = (
        db.separate_expenses
        .find(query)
        .sort([("expense_date", -1), ("date_str", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
) -> int:
    """Count Congo Expenses rows across all uploads."""
    db = get_db()
    return await db.separate_expenses.count_documents(
        _congo_entries_query(search, year, month, date_from, date_to, money_in_only)
    )


async def get_congo_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
) -> dict:
    """Return record count and money in/out totals for filtered Congo rows."""
    db = get_db()
    pipeline = [
        {"$match": _congo_entries_query(search, year, month, date_from, date_to, money_in_only)},
        {"$group": {
            "_id": None,
            "count":     {"$sum": 1},
            "money_in":  {"$sum": {
                "$cond": [
                    {"$lt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                    {"$ifNull": ["$amount_usd", 0]},
                    0,
                ],
            }},
            "money_out": {"$sum": {
                "$cond": [
                    {"$gt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                    {"$ifNull": ["$amount_usd", 0]},
                    0,
                ],
            }},
        }},
    ]
    result = await db.separate_expenses.aggregate(pipeline).to_list(1)
    if result:
        row = result[0]
        row["balance_usd"] = row.get("money_in", 0) + row.get("money_out", 0)
        return row
    return {"count": 0, "money_in": 0.0, "money_out": 0.0, "balance_usd": 0.0}


async def get_congo_available_years() -> List[int]:
    """Years present in uploaded Congo Expenses records."""
    db = get_db()
    years: set[int] = set()

    pipeline = [
        {"$match": {
            "expense_type": "congo_expenses",
            "expense_date": {"$exists": True, "$ne": None},
        }},
        {"$group": {"_id": {"$year": "$expense_date"}}},
    ]
    for doc in await db.separate_expenses.aggregate(pipeline).to_list(length=None):
        yr = doc.get("_id")
        if isinstance(yr, int) and 1990 <= yr <= 2100:
            years.add(yr)

    legacy_dates = await db.separate_expenses.distinct(
        "date_str",
        {
            "expense_type": "congo_expenses",
            "$or": [
                {"expense_date": {"$exists": False}},
                {"expense_date": None},
            ],
        },
    )
    for val in legacy_dates:
        parsed = _parse_toll_date(val)
        if parsed and 1990 <= parsed.year <= 2100:
            years.add(parsed.year)

    return sorted(years, reverse=True)


async def delete_congo_upload(upload_id: str) -> int:
    """Delete every Congo Expenses row belonging to one upload batch."""
    if not upload_id:
        return 0
    db = get_db()
    result = await db.separate_expenses.delete_many({
        "expense_type": "congo_expenses",
        "upload_id": upload_id,
    })
    return result.deleted_count


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


def _truck_and_trailer_match(value: str) -> dict:
    """Match a truck in SM Burhani ``T469EKZ/T689ELK`` cells and spaced plates."""
    from tahmeed.services.import_truck_check import truck_and_trailer_search_regex

    pattern = truck_and_trailer_search_regex(value)
    if not pattern:
        return _truck_exact(value)
    return {"$regex": pattern, "$options": "i"}


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


async def _truck_overview_master_rows(
    truck: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list:
    db = get_db()
    diesel_items = await get_diesel_cash_item_names()
    query = _with_date_range(
        {"verified": True, "truck_number": _truck_exact(truck)},
        "date",
        date_from,
        date_to,
    )
    docs = await db.transactions.find(query).sort(
        [("date", -1), ("created_at", -1)]
    ).to_list(length=None)

    rows = []
    for doc in docs:
        tx = Transaction.from_doc(doc)
        is_diesel_cash = (
            is_diesel_cash_item(tx.category_name, diesel_items)
            or is_diesel_cash_item(tx.item, diesel_items)
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


async def _truck_overview_diesel_rows(
    truck: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list:
    db = get_db()
    rows: list = []
    for feed_type, label in _TRUCK_OVERVIEW_DIESEL_FEEDS:
        query = _with_date_range(
            {"feed_type": feed_type, "truck_no": _truck_exact(truck)},
            "date",
            date_from,
            date_to,
        )
        docs = await db.imported_feeds.find(query).sort(
            [("date", -1), ("import_date", -1)]
        ).to_list(length=None)
        for doc in docs:
            rows.append(_normalized_row(
                source_group="diesel_imports",
                source=label,
                date_value=doc.get("date"),
                truck_value=str(doc.get("truck_no", "") or ""),
                description=doc.get("destinations") or doc.get("clients_name") or "Diesel import",
                reference=doc.get("lpo_no") or doc.get("do_sdo_no") or doc.get("sheet_label") or "",
                currency="TZS",
                amount=diesel_line_total(doc.get("ltrs"), doc.get("price_per_ltr")),
                liters=doc.get("ltrs"),
                rate=doc.get("price_per_ltr"),
                station=doc.get("diesel_at") or "",
            ))
    return rows


async def _truck_overview_imported_feed_rows(
    truck: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list:
    db = get_db()
    rows: list = []

    toll_docs = await db.imported_feeds.find(
        _with_date_range(
            {"feed_type": "toll_plaza", "vehicle_reg": _truck_exact(truck)},
            "toll_date",
            date_from,
            date_to,
        )
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
        _with_date_range(
            {"feed_type": "parking_congo", "vehicle_no": _truck_exact(truck)},
            "payment_date",
            date_from,
            date_to,
        )
    ).sort([("payment_date", -1), ("import_date", -1)]).to_list(length=None)
    for doc in parking_docs:
        rows.append(_normalized_row(
            source_group="parking_congo",
            source="Parking Congo",
            date_value=doc.get("payment_date"),
            truck_value=str(doc.get("vehicle_no", "") or ""),
            description=doc.get("transaction_details") or doc.get("transaction_type") or "Parking transaction",
            reference=doc.get("ledger_id") or doc.get("direction") or "",
            currency="USD",
            amount=doc.get("amount"),
            station=doc.get("cashier") or "",
        ))

    zambia_docs = await db.imported_feeds.find(
        _with_date_range(
            {"feed_type": "zambia_parking", "plate_num": _truck_exact(truck)},
            "transaction_date",
            date_from,
            date_to,
        )
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


async def _truck_overview_separate_rows(
    truck: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list:
    db = get_db()
    rows: list = []

    congo_docs = await db.separate_expenses.find(
        _with_date_range(
            {"expense_type": "congo_expenses", "truck_no": _truck_exact(truck)},
            "expense_date",
            date_from,
            date_to,
        )
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
        _with_date_range(
            {"expense_type": "ahmed_kimvi", "truck_no": _truck_exact(truck)},
            "expense_date",
            date_from,
            date_to,
        )
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


async def _truck_overview_extra_sidebar_rows(
    truck: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list:
    db = get_db()
    rows: list = []

    afritrack_docs = await db.imported_feeds.find(
        _with_date_range(
            {"feed_type": "afritrack", "truck": _truck_exact(truck)},
            "import_date",
            date_from,
            date_to,
        )
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
        _with_date_range(
            {"feed_type": "rahntech", "truck_number": _truck_exact(truck)},
            "sales_date",
            date_from,
            date_to,
        )
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
        _with_date_range(
            {"feed_type": "comesa", "truck_reg": _truck_exact(truck)},
            "import_date",
            date_from,
            date_to,
        )
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
        _with_date_range(
            {"feed_type": "third_party", "reg_no": _truck_exact(truck)},
            "import_date",
            date_from,
            date_to,
        )
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
        _with_date_range(
            {
                "entity": "sm_burhani",
                "truck_and_trailer": _truck_and_trailer_match(truck),
            },
            "t1_date",
            date_from,
            date_to,
        )
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
            "installation_tahmeed": {"$first": "$installation_tahmeed"},
            "installation_invoice": {"$first": "$installation_invoice"},
            "balance_mar":          {"$first": "$balance_mar"},
            "vat_rate":             {"$first": "$vat_rate"},
            "installation_label":   {"$first": "$installation_label"},
            "total_payable_label":  {"$first": "$total_payable_label"},
            "statement_tahmeed":    {"$first": "$statement_tahmeed"},
            "statement_invoice":    {"$first": "$statement_invoice"},
        }},
        {"$sort": {"import_date": -1}},
    ]
    return await db.imported_feeds.aggregate(pipeline).to_list(length=None)


def _afritrack_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Build a MongoDB query for Afritrack rows across all uploads."""
    clauses: List[dict] = [{"feed_type": "afritrack"}]
    if search.strip():
        s = re.escape(search.strip())
        clauses.append({"$or": [
            {"truck": {"$regex": s, "$options": "i"}},
            {"remarks": {"$regex": s, "$options": "i"}},
            {"period": {"$regex": s, "$options": "i"}},
            {"source_filename": {"$regex": s, "$options": "i"}},
        ]})
    if year > 0:
        if month >= 1:
            start = datetime(year, month, 1)
            end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        else:
            start = datetime(year, 1, 1)
            end = datetime(year + 1, 1, 1)
        clauses.append({"import_date": {"$gte": start, "$lt": end}})
    clause = _date_range_clause("import_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_afritrack_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    """Return paginated Afritrack rows across all uploads."""
    db = get_db()
    cursor = (
        db.imported_feeds.find(_afritrack_all_query(search, year, month, date_from, date_to))
        .sort([("import_date", -1), ("period", -1), ("row_index", 1)])
        .skip(skip)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def count_afritrack_all_records(search: str = "", year: int = 0, month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    """Count Afritrack rows across all uploads."""
    db = get_db()
    return await db.imported_feeds.count_documents(_afritrack_all_query(search, year, month, date_from, date_to))


async def get_afritrack_all_totals(search: str = "", year: int = 0, month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Return row count and totals for filtered Afritrack rows."""
    db = get_db()
    pipeline = [
        {"$match": _afritrack_all_query(search, year, month, date_from, date_to)},
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


async def get_afritrack_available_years() -> List[int]:
    """Years present in uploaded Afritrack records."""
    db = get_db()
    years: set[int] = set()
    pipeline = [
        {"$match": {
            "feed_type": "afritrack",
            "import_date": {"$exists": True, "$ne": None},
        }},
        {"$group": {"_id": {"$year": "$import_date"}}},
    ]
    for doc in await db.imported_feeds.aggregate(pipeline).to_list(length=None):
        yr = doc.get("_id")
        if isinstance(yr, int) and 1990 <= yr <= 2100:
            years.add(yr)
    return sorted(years, reverse=True)


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


async def _load_truck_overview_rows(
    truck: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list:
    master_rows, diesel_rows, imported_rows, separate_rows, extra_rows = await asyncio.gather(
        _truck_overview_master_rows(truck, date_from, date_to),
        _truck_overview_diesel_rows(truck, date_from, date_to),
        _truck_overview_imported_feed_rows(truck, date_from, date_to),
        _truck_overview_separate_rows(truck, date_from, date_to),
        _truck_overview_extra_sidebar_rows(truck, date_from, date_to),
    )
    return master_rows + diesel_rows + imported_rows + separate_rows + extra_rows


def _normalize_truck_overview_sources(
    source: Union[str, Sequence[str], None],
) -> Optional[Set[str]]:
    """Return ``None`` for all sources, else the selected ``source_group`` keys."""
    if source is None or source == "" or source == "all":
        return None
    if isinstance(source, str):
        keys = [source]
    else:
        keys = [str(k) for k in source]
    if not keys or "all" in keys:
        return None
    wanted = {k for k in keys if k in _TRUCK_OVERVIEW_SOURCES and k != "all"}
    return wanted or None


def _normalize_truck_overview_currency(currency: str = "") -> str:
    cur = (currency or "").strip().upper()
    if cur in ("", "ALL"):
        return ""
    if cur in ("TZS", "TSH", "TZ"):
        return "TZS"
    if cur == "USD":
        return "USD"
    if cur in ("ZMW", "ZMB", "ZK"):
        return "ZMW"
    return cur


def _truck_row_matches_currency(row: dict, currency: str = "") -> bool:
    wanted = _normalize_truck_overview_currency(currency)
    if not wanted:
        return True
    return _normalize_truck_overview_currency(row.get("currency") or "") == wanted


def _filter_truck_overview_rows(
    rows: list,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
) -> list:
    wanted = _normalize_truck_overview_sources(source)
    filtered = [
        row for row in rows
        if (wanted is None or row["source_group"] in wanted)
        and _truck_row_in_date_range(row, date_from, date_to)
        and _truck_row_matches_currency(row, currency)
    ]
    filtered.sort(key=lambda row: (row.get("date") or datetime.min))
    return filtered


async def get_truck_overview_records(
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    if not truck.strip():
        return []
    rows = _filter_truck_overview_rows(
        await _load_truck_overview_rows(truck.strip(), date_from, date_to),
        source=source,
        date_from=date_from,
        date_to=date_to,
        currency=currency,
    )
    return rows[skip: skip + limit]


async def count_truck_overview_records(
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
) -> int:
    if not truck.strip():
        return 0
    rows = _filter_truck_overview_rows(
        await _load_truck_overview_rows(truck.strip(), date_from, date_to),
        source=source,
        date_from=date_from,
        date_to=date_to,
        currency=currency,
    )
    return len(rows)


async def get_truck_overview_summary(
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
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
        await _load_truck_overview_rows(truck.strip(), date_from, date_to),
        source=source,
        date_from=date_from,
        date_to=date_to,
        currency=currency,
    )
    tzs_total = 0.0
    usd_total = 0.0
    zmw_total = 0.0
    liters_total = 0.0
    seen_sources: set[str] = set()
    for row in rows:
        seen_sources.add(row.get("source", ""))
        amount = row.get("amount")
        row_currency = _normalize_truck_overview_currency(row.get("currency") or "")
        if amount is not None:
            if row_currency == "TZS":
                tzs_total += amount
            elif row_currency == "USD":
                usd_total += amount
            elif row_currency == "ZMW":
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


# ── Skipped import rows (fleet check park-for-follow-up) ──────────────────────

async def save_skipped_import_rows(docs: list) -> int:
    """Persist rows skipped during fleet validation for later edit / re-upload."""
    if not docs:
        return 0
    db = get_db()
    now = datetime.utcnow()
    payload = []
    for doc in docs:
        item = dict(doc)
        item["skipped_at"] = now
        payload.append(item)
    result = await db.skipped_import_rows.insert_many(payload, ordered=False)
    return len(result.inserted_ids)


async def list_skipped_import_rows(
    feed_key: str,
    *,
    search: str = "",
    limit: int = 200,
    skip: int = 0,
) -> list:
    db = get_db()
    query: dict = {"feed_key": feed_key}
    term = (search or "").strip()
    if term:
        query.update(_skipped_import_search_clause(term))
    cursor = (
        db.skipped_import_rows.find(query)
        .sort([("skipped_at", -1), ("source_row", 1)])
        .skip(max(0, skip))
        .limit(max(1, limit))
    )
    return await cursor.to_list(length=limit)


async def count_skipped_import_rows(feed_key: str, search: str = "") -> int:
    db = get_db()
    query: dict = {"feed_key": feed_key}
    term = (search or "").strip()
    if term:
        query.update(_skipped_import_search_clause(term))
    return await db.skipped_import_rows.count_documents(query)


def _skipped_import_search_clause(term: str) -> dict:
    """Build ``$or`` search across parked-row metadata and nested record fields."""
    rx = {"$regex": re.escape(term), "$options": "i"}
    clauses: list = [
        {"truck_value": rx},
        {"original_truck": rx},
        {"source_filename": rx},
        {"sheet_label": rx},
        {"target_upload_id": rx},
        {"reason": rx},
        {"record.receipt_no": rx},
        {"record.ledger_id": rx},
        {"record.lpo_no": rx},
        {"record.serial": rx},
        {"record.toll_plaza": rx},
        {"record.vehicle_reg": rx},
        {"record.vehicle_no": rx},
        {"record.plate_num": rx},
        {"record.client_name": rx},
        {"record.cashier_name": rx},
        {"record.description": rx},
        {"record.transaction_details": rx},
    ]
    if term.isdigit():
        clauses.append({"source_row": int(term)})
    return {"$or": clauses}


async def update_skipped_import_truck(doc_id: str, truck_value: str) -> bool:
    """Update the parked truck value (and nested record field) before re-upload."""
    from bson import ObjectId

    db = get_db()
    try:
        oid = ObjectId(doc_id)
    except Exception:
        return False
    doc = await db.skipped_import_rows.find_one({"_id": oid})
    if not doc:
        return False
    field = doc.get("truck_field") or "truck_no"
    record = dict(doc.get("record") or {})
    record[field] = truck_value
    result = await db.skipped_import_rows.update_one(
        {"_id": oid},
        {"$set": {"truck_value": truck_value, "record": record}},
    )
    return result.modified_count > 0 or result.matched_count > 0


async def delete_skipped_import_rows(ids: list[str]) -> int:
    from bson import ObjectId

    db = get_db()
    oids = []
    for raw in ids:
        try:
            oids.append(ObjectId(raw))
        except Exception:
            continue
    if not oids:
        return 0
    result = await db.skipped_import_rows.delete_many({"_id": {"$in": oids}})
    return int(result.deleted_count)


async def reupload_skipped_import_rows(ids: list[str]) -> int:
    """Insert parked records into their original upload batch, then delete skips.

    Uses each doc's ``target_upload_id`` and ``save_target`` so rows join the
    upload they were skipped from (not a new batch).

    Each inserted feed/expense doc carries ``skipped_row_id`` so a retry after a
    crash cannot duplicate rows (unique sparse index).
    """
    from bson import ObjectId

    from tahmeed.db.import_idempotency import ensure_import_indexes

    db = get_db()
    await ensure_import_indexes()
    oids = []
    for raw in ids:
        try:
            oids.append(ObjectId(raw))
        except Exception:
            continue
    if not oids:
        return 0
    docs = await db.skipped_import_rows.find({"_id": {"$in": oids}}).to_list(length=None)
    if not docs:
        return 0

    feed_records: dict[str, list] = {}
    for doc in docs:
        rec = dict(doc.get("record") or {})
        rec.pop("_raw", None)
        upload_id = doc.get("target_upload_id") or rec.get("upload_id") or ""
        rec["upload_id"] = upload_id
        rec["skipped_row_id"] = str(doc["_id"])
        if doc.get("source_filename") and not rec.get("source_filename"):
            rec["source_filename"] = doc["source_filename"]
        if doc.get("sheet_label") and not rec.get("sheet_label"):
            rec["sheet_label"] = doc["sheet_label"]
        field = doc.get("truck_field")
        if field:
            rec[field] = doc.get("truck_value") or rec.get(field, "")
        feed_key = doc.get("feed_key") or ""
        feed_records.setdefault(feed_key, []).append(rec)

    saved = 0
    for feed_key, records in feed_records.items():
        if feed_key == "congo_expenses":
            saved += await save_congo_import(records)
        elif feed_key == "ahmed_kimvi":
            saved += await save_kimvi_import(records)
        elif feed_key in ("rpa_schedule", "bonds", "sm_burhani"):
            from tahmeed.models.reconciliation import ReconciliationEntry
            from tahmeed.services.reconciliation_service import save_reconciliation_rows

            saved += await save_reconciliation_rows(
                [ReconciliationEntry.from_doc(rec) for rec in records]
            )
        else:
            saved += await save_imported_feed(records)

    await db.skipped_import_rows.delete_many({"_id": {"$in": oids}})
    return saved
