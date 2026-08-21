"""Reconciliation service — async Motor CRUD for SM Burhani schedules.

Backs the SM Burhani views (RPA Schedule + Bonds station tabs). Rows live in
``reconciliation_entries``; the dynamic station list lives in ``recon_stations``
so the accountant can add a station before any data is imported for it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional

from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.reconciliation import ReconciliationEntry

ENTITY = "sm_burhani"
ALL_STATION_SLUG = "all"

# Stations seeded the first time the Bonds view is opened.
_DEFAULT_STATIONS = [
    ("nakonde",     "Nakonde",     "Tanzania – Zambia border"),
    ("kasumbalesa", "Kasumbalesa", "Zambia – DRC border"),
    ("sakania",     "Sakania",     "Zambia – DRC border"),
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def station_query_value(slug: str) -> str:
    """Empty string means no station filter (the All chip)."""
    s = (slug or "").strip()
    return "" if not s or s == ALL_STATION_SLUG else s


def station_display_name(slug: str) -> str:
    s = (slug or "").strip()
    if not s or s == ALL_STATION_SLUG:
        return "All"
    return s.replace("_", " ").title()


def unique_station_docs(docs: List[dict]) -> List[dict]:
    """Keep the first row for each slug so chips are not drawn twice."""
    seen: set = set()
    out: List[dict] = []
    for doc in docs:
        slug = str(doc.get("slug") or "").strip()
        if not slug or slug == ALL_STATION_SLUG or slug in seen:
            continue
        seen.add(slug)
        out.append(doc)
    return out


def unique_stations_in_order(slugs) -> List[str]:
    """First-seen station slugs, skipping blanks and the All chip."""
    seen: set = set()
    out: List[str] = []
    for raw in slugs:
        s = str(raw or "").strip()
        if not s or s == ALL_STATION_SLUG or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def format_station_counts(slugs) -> str:
    """'Nakonde 67, Kasumbalesa 110' in file order."""
    from collections import Counter

    values = [str(s or "").strip() for s in slugs]
    ordered = unique_stations_in_order(values)
    counts = Counter(values)
    return ", ".join(
        f"{station_display_name(slug)} {counts[slug]:,}" for slug in ordered
    )


def _safe_double(field: str) -> dict:
    return {"$ifNull": [f"${field}", 0.0]}


# ── Query builder ───────────────────────────────────────────────────────────────

def _truck_search_clauses(search: str) -> list:
    """Match SM Burhani truck cells; plate queries do not scan other columns."""
    from tahmeed.services.import_truck_check import truck_field_search_regex

    text = search.strip()
    plate_rx = truck_field_search_regex(text)
    if plate_rx:
        return [{"truck_and_trailer": {"$regex": plate_rx, "$options": "i"}}]
    rx = {"$regex": re.escape(text), "$options": "i"}
    return [
        {"sm_ref_no": rx},
        {"prn_number": rx},
        {"entry_reg_no": rx},
        {"t1_no": rx},
        {"importer": rx},
        {"exporter": rx},
        {"consignment": rx},
        {"truck_and_trailer": rx},
    ]


def _build_query(table: str, station: str = "", search: str = "") -> dict:
    query: dict = {"entity": ENTITY, "table": table}
    if station.strip():
        query["station"] = station.strip()
    if search.strip():
        query["$or"] = _truck_search_clauses(search)
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
        {"$sort": {"source_index": 1, "_id": 1}},
        {"$group": {
            "_id":             "$upload_id",
            "source_filename": {"$first": "$source_filename"},
            "import_date":     {"$first": "$import_date"},
            "schedule_period": {"$first": "$schedule_period"},
            "record_count":    {"$sum": 1},
            "total_charge":    {"$sum": _safe_double("charge")},
            "stations":        {"$push": "$station"},
        }},
        {"$sort": {"import_date": -1}},
    ]
    docs = await db.reconciliation_entries.aggregate(pipeline).to_list(length=None)
    for doc in docs:
        doc["stations"] = unique_stations_in_order(doc.get("stations") or [])
    return docs


def _all_records_query(
    table: str,
    station: str = "",
    search: str = "",
    year: int = 0,
    month: int = 0,
) -> dict:
    query = _build_query(table, station, search)
    if year > 0:
        if month >= 1:
            start = datetime(year, month, 1)
            end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        else:
            start = datetime(year, 1, 1)
            end = datetime(year + 1, 1, 1)
        query["$and"] = query.get("$and", [])
        query["$and"].append({
            "$or": [
                {"t1_date": {"$gte": start, "$lt": end}},
                {
                    "$and": [
                        {"$or": [{"t1_date": {"$exists": False}}, {"t1_date": None}]},
                        {"import_date": {"$gte": start, "$lt": end}},
                    ]
                },
            ]
        })
    return query


async def get_recon_all_records(
    table: str,
    station: str = "",
    search: str = "",
    year: int = 0,
    month: int = 0,
    limit: int = 50,
    skip: int = 0,
) -> List[ReconciliationEntry]:
    db = get_db()
    query = _all_records_query(table, station, search, year, month)
    cursor = (
        db.reconciliation_entries.find(query)
        .sort([("t1_date", -1), ("import_date", -1), ("sr_no", 1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [ReconciliationEntry.from_doc(d) for d in docs]


async def count_recon_all_records(
    table: str, station: str = "", search: str = "", year: int = 0, month: int = 0
) -> int:
    db = get_db()
    return await db.reconciliation_entries.count_documents(
        _all_records_query(table, station, search, year, month)
    )


async def get_recon_all_totals(
    table: str, station: str = "", search: str = "", year: int = 0, month: int = 0
) -> dict:
    db = get_db()
    query = _all_records_query(table, station, search, year, month)
    result = await db.reconciliation_entries.aggregate([
        {"$match": query},
        {"$group": {
            "_id": None,
            "count": {"$sum": 1},
            "charge_total": {"$sum": _safe_double("charge")},
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


async def get_recon_available_years(table: str, station: str = "") -> List[int]:
    db = get_db()
    years: set[int] = set()
    query = {"entity": ENTITY, "table": table}
    if station.strip():
        query["station"] = station.strip()
    pipeline = [
        {"$match": {**query, "t1_date": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": {"$year": "$t1_date"}}},
    ]
    for doc in await db.reconciliation_entries.aggregate(pipeline).to_list(length=None):
        yr = doc.get("_id")
        if isinstance(yr, int) and 1990 <= yr <= 2100:
            years.add(yr)
    import_pipeline = [
        {"$match": {**query, "import_date": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": {"$year": "$import_date"}}},
    ]
    for doc in await db.reconciliation_entries.aggregate(import_pipeline).to_list(length=None):
        yr = doc.get("_id")
        if isinstance(yr, int) and 1990 <= yr <= 2100:
            years.add(yr)
    return sorted(years, reverse=True)


def _upload_station_sort_key(doc: dict) -> tuple:
    idx = doc.get("first_idx")
    if isinstance(idx, (int, float)):
        return (0, int(idx), "")
    return (1, 0, str(doc.get("first_id") or ""))


async def get_recon_upload_stations(upload_id: str, table: str) -> List[dict]:
    """Distinct stations present in one upload, in file order."""
    if not upload_id:
        return []
    db = get_db()
    pipeline = [
        {"$match": {
            "entity": ENTITY,
            "table": table,
            "upload_id": upload_id,
            "station": {"$exists": True, "$nin": [None, ""]},
        }},
        {"$group": {
            "_id": "$station",
            "first_idx": {"$min": "$source_index"},
            "first_id": {"$min": "$_id"},
        }},
    ]
    docs = await db.reconciliation_entries.aggregate(pipeline).to_list(length=None)
    docs.sort(key=_upload_station_sort_key)
    stations: List[dict] = []
    for doc in docs:
        slug = str(doc.get("_id") or "").strip()
        if not slug:
            continue
        stations.append({
            "slug": slug,
            "name": station_display_name(slug),
        })
    return stations


def _upload_record_query(
    upload_id: str, table: str, station: str = "", search: str = ""
) -> dict:
    query: dict = {"entity": ENTITY, "table": table, "upload_id": upload_id}
    if station.strip():
        query["station"] = station.strip()
    if search.strip():
        query["$or"] = _truck_search_clauses(search)
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
    sort = (
        [("sr_no", 1), ("import_date", 1)]
        if station.strip()
        else [("source_index", 1), ("_id", 1)]
    )
    cursor = (
        db.reconciliation_entries.find(query)
        .sort(sort)
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


async def delete_recon_upload(upload_id: str, table: str) -> int:
    db = get_db()
    result = await db.reconciliation_entries.delete_many({
        "entity": ENTITY,
        "table": table,
        "upload_id": upload_id,
    })
    return result.deleted_count


# ── Dedup + write ────────────────────────────────────────────────────────────────

async def get_existing_recon_keys(keys: List[str], table: str) -> set:
    """Return PRN|ENTRY REG keys already stored for this table only.

    Bonds and RPA share ``reconciliation_entries``; a match in the other
    feed must not block import.
    """
    if not keys:
        return set()
    db = get_db()
    docs = await db.reconciliation_entries.find(
        {
            "entity": ENTITY,
            "table": table,
            "dedup_key": {"$in": keys},
        },
        {"dedup_key": 1},
    ).to_list(length=None)
    return {d.get("dedup_key") for d in docs if d.get("dedup_key")}


async def save_reconciliation_rows(entries: List[ReconciliationEntry]) -> int:
    if not entries:
        return 0
    db = get_db()
    docs = []
    for i, e in enumerate(entries):
        doc = e.to_doc()
        doc["source_index"] = i
        docs.append(doc)
    result = await db.reconciliation_entries.insert_many(docs, ordered=False)
    await ensure_recon_stations(entries[0].table, [e.station for e in entries])
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
    return unique_station_docs(docs)


async def ensure_recon_stations(table: str, slugs: List[str]) -> None:
    """Create/activate station chips for every station found in an import."""
    for slug in unique_stations_in_order(slugs):
        await add_recon_station(station_display_name(slug), table=table)


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
