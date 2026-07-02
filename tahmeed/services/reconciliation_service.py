"""Reconciliation service — async Motor CRUD for SM Burhani schedules.

Backs the SM Burhani views (RPA Schedule + Bonds station tabs). Rows live in
``reconciliation_entries``; the dynamic station list lives in ``recon_stations``
so the accountant can add a station before any data is imported for it.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.reconciliation import ReconciliationEntry

ENTITY = "sm_burhani"

# Stations seeded the first time the Bonds view is opened.
_DEFAULT_STATIONS = [
    ("nakonde",     "Nakonde",     "Tanzania – Zambia border"),
    ("kasumbalesa", "Kasumbalesa", "Zambia – DRC border"),
    ("sakania",     "Sakania",     "Zambia – DRC border"),
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _safe_double(field: str) -> dict:
    return {"$ifNull": [f"${field}", 0.0]}


# ── Query builder ───────────────────────────────────────────────────────────────

def _build_query(table: str, station: str = "", search: str = "") -> dict:
    query: dict = {"entity": ENTITY, "table": table}
    if station.strip():
        query["station"] = station.strip()
    if search.strip():
        rx = {"$regex": re.escape(search.strip()), "$options": "i"}
        query["$or"] = [
            {"sm_ref_no": rx},
            {"prn_number": rx},
            {"entry_reg_no": rx},
            {"t1_no": rx},
            {"importer": rx},
            {"exporter": rx},
            {"consignment": rx},
            {"truck_and_trailer": rx},
        ]
    return query


# ── Read ────────────────────────────────────────────────────────────────────────

async def get_reconciliation_rows(
    table: str,
    station: str = "",
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> List[ReconciliationEntry]:
    db = get_db()
    query = _build_query(table, station, search)
    cursor = (
        db.reconciliation_entries.find(query)
        .sort([("sr_no", 1), ("import_date", 1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [ReconciliationEntry.from_doc(d) for d in docs]


async def count_reconciliation_rows(table: str, station: str = "", search: str = "") -> int:
    db = get_db()
    return await db.reconciliation_entries.count_documents(_build_query(table, station, search))


async def get_reconciliation_totals(table: str, station: str = "") -> dict:
    """Charge totals + confirmed/disputed counts for the reconciliation summary."""
    db = get_db()
    query = _build_query(table, station)
    result = await db.reconciliation_entries.aggregate([
        {"$match": query},
        {"$group": {
            "_id": None,
            "count": {"$sum": 1},
            "charge_total": {"$sum": {"$ifNull": ["$charge", 0]}},
            "confirmed_total": {
                "$sum": {"$cond": ["$confirmed", {"$ifNull": ["$charge", 0]}, 0]}
            },
            "disputed_count": {"$sum": {"$cond": ["$disputed", 1, 0]}},
        }},
    ]).to_list(1)
    if result:
        r = result[0]
        invoiced = r.get("charge_total", 0.0)
        confirmed = r.get("confirmed_total", 0.0)
        return {
            "count": r.get("count", 0),
            "invoiced": invoiced,
            "confirmed": confirmed,
            "variance": invoiced - confirmed,
            "disputed": r.get("disputed_count", 0),
        }
    return {"count": 0, "invoiced": 0.0, "confirmed": 0.0, "variance": 0.0, "disputed": 0}


# ── Upload batches ──────────────────────────────────────────────────────────────

async def get_recon_uploads(table: str) -> List[dict]:
    """Return one summary doc per import batch for an SM Burhani table type."""
    db = get_db()
    pipeline = [
        {"$match": {
            "entity": ENTITY,
            "table": table,
            "upload_id": {"$exists": True, "$ne": ""},
        }},
        {"$group": {
            "_id":             "$upload_id",
            "source_filename": {"$first": "$source_filename"},
            "import_date":     {"$first": "$import_date"},
            "schedule_period": {"$first": "$schedule_period"},
            "record_count":    {"$sum": 1},
            "total_charge":    {"$sum": _safe_double("charge")},
        }},
        {"$sort": {"import_date": -1}},
    ]
    return await db.reconciliation_entries.aggregate(pipeline).to_list(length=None)


def _upload_record_query(
    upload_id: str, table: str, station: str = "", search: str = ""
) -> dict:
    query: dict = {"entity": ENTITY, "table": table, "upload_id": upload_id}
    if station.strip():
        query["station"] = station.strip()
    if search.strip():
        rx = {"$regex": re.escape(search.strip()), "$options": "i"}
        query["$or"] = [
            {"sm_ref_no": rx},
            {"prn_number": rx},
            {"entry_reg_no": rx},
            {"t1_no": rx},
            {"importer": rx},
            {"exporter": rx},
            {"consignment": rx},
            {"truck_and_trailer": rx},
        ]
    return query


async def get_recon_upload_records(
    upload_id: str,
    table: str,
    station: str = "",
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> List[ReconciliationEntry]:
    db = get_db()
    query = _upload_record_query(upload_id, table, station, search)
    cursor = (
        db.reconciliation_entries.find(query)
        .sort([("sr_no", 1), ("import_date", 1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [ReconciliationEntry.from_doc(d) for d in docs]


async def count_recon_upload_records(
    upload_id: str, table: str, station: str = "", search: str = ""
) -> int:
    db = get_db()
    return await db.reconciliation_entries.count_documents(
        _upload_record_query(upload_id, table, station, search)
    )


async def get_recon_upload_totals(
    upload_id: str, table: str, station: str = ""
) -> dict:
    """Charge totals + confirmed/disputed counts for one upload batch."""
    db = get_db()
    query = _upload_record_query(upload_id, table, station)
    result = await db.reconciliation_entries.aggregate([
        {"$match": query},
        {"$group": {
            "_id": None,
            "count": {"$sum": 1},
            "charge_total": {"$sum": _safe_double("charge")},
            "confirmed_total": {
                "$sum": {"$cond": ["$confirmed", _safe_double("charge"), 0]}
            },
            "disputed_count": {"$sum": {"$cond": ["$disputed", 1, 0]}},
        }},
    ]).to_list(1)
    if result:
        r = result[0]
        invoiced = r.get("charge_total", 0.0)
        confirmed = r.get("confirmed_total", 0.0)
        return {
            "count": r.get("count", 0),
            "invoiced": invoiced,
            "confirmed": confirmed,
            "variance": invoiced - confirmed,
            "disputed": r.get("disputed_count", 0),
        }
    return {"count": 0, "invoiced": 0.0, "confirmed": 0.0, "variance": 0.0, "disputed": 0}


# ── Dedup + write ────────────────────────────────────────────────────────────────

async def get_existing_recon_keys(keys: List[str]) -> set:
    """Return the dedup_key values already stored (composite PRN | ENTRY REG)."""
    if not keys:
        return set()
    db = get_db()
    docs = await db.reconciliation_entries.find(
        {"dedup_key": {"$in": keys}}, {"dedup_key": 1}
    ).to_list(length=None)
    return {d.get("dedup_key") for d in docs if d.get("dedup_key")}


async def save_reconciliation_rows(entries: List[ReconciliationEntry]) -> int:
    if not entries:
        return 0
    db = get_db()
    docs = [e.to_doc() for e in entries]
    result = await db.reconciliation_entries.insert_many(docs, ordered=False)
    return len(result.inserted_ids)


async def set_row_status(
    row_id: ObjectId,
    confirmed: Optional[bool] = None,
    disputed: Optional[bool] = None,
    dispute_note: Optional[str] = None,
) -> bool:
    db = get_db()
    update: dict = {}
    if confirmed is not None:
        update["confirmed"] = confirmed
    if disputed is not None:
        update["disputed"] = disputed
    if dispute_note is not None:
        update["dispute_note"] = dispute_note
    if not update:
        return False
    result = await db.reconciliation_entries.update_one({"_id": row_id}, {"$set": update})
    return result.modified_count == 1


# ── Stations (dynamic tabs) ─────────────────────────────────────────────────────

async def get_recon_stations(table: str = "bonds") -> List[dict]:
    """Return the station list for a table, seeding the Bonds defaults once."""
    db = get_db()
    docs = await db.recon_stations.find(
        {"entity": ENTITY, "table": table, "active": True}
    ).sort([("sort_order", 1), ("name", 1)]).to_list(length=None)

    if not docs and table == "bonds":
        seed = [
            {"entity": ENTITY, "table": table, "slug": slug, "name": name,
             "border_post": border, "active": True, "sort_order": i}
            for i, (slug, name, border) in enumerate(_DEFAULT_STATIONS)
        ]
        await db.recon_stations.insert_many(seed)
        docs = seed
    return docs


async def add_recon_station(
    name: str, border_post: str = "", table: str = "bonds"
) -> dict:
    db = get_db()
    slug = _slug(name)
    existing = await db.recon_stations.find_one(
        {"entity": ENTITY, "table": table, "slug": slug}
    )
    if existing:
        await db.recon_stations.update_one(
            {"_id": existing["_id"]}, {"$set": {"active": True}}
        )
        return existing
    count = await db.recon_stations.count_documents({"entity": ENTITY, "table": table})
    doc = {
        "entity": ENTITY, "table": table, "slug": slug, "name": name.strip(),
        "border_post": border_post.strip(), "active": True, "sort_order": count,
    }
    result = await db.recon_stations.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc
