"""Reconciliation (bonds / RPA) query helpers (Phase 6.1)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

ENTITY = "sm_burhani"
ALL_STATION_SLUG = "all"
VALID_TABLES = frozenset({"bonds", "rpa_schedule"})

_DEFAULT_STATIONS = [
    ("nakonde", "Nakonde", "Tanzania – Zambia border"),
    ("kasumbalesa", "Kasumbalesa", "Zambia – DRC border"),
    ("sakania", "Sakania", "Zambia – DRC border"),
]

_EMPTY_TOTALS = {
    "count": 0,
    "invoiced": 0.0,
    "confirmed": 0.0,
    "variance": 0.0,
    "disputed": 0,
}


def validate_table(table: str) -> str:
    value = (table or "").strip()
    if value not in VALID_TABLES:
        raise ValueError(f"Invalid reconciliation table '{table}'")
    return value


def station_slug(name: str) -> str:
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


def unique_station_docs(docs: list[dict]) -> list[dict]:
    """Keep the first row for each slug so chips are not drawn twice."""
    seen: set[str] = set()
    out: list[dict] = []
    for doc in docs:
        slug = str(doc.get("slug") or "").strip()
        if not slug or slug == ALL_STATION_SLUG or slug in seen:
            continue
        seen.add(slug)
        out.append(doc)
    return out


def unique_stations_in_order(slugs: Any) -> list[str]:
    """First-seen station slugs, skipping blanks and the All chip."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in slugs or ():
        s = str(raw or "").strip()
        if not s or s == ALL_STATION_SLUG or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def safe_double(field: str) -> dict[str, Any]:
    return {"$ifNull": [f"${field}", 0.0]}


def _plate_flex_body(compact: str) -> str:
    return r"\s*".join(re.escape(ch) for ch in compact)


def truck_field_search_regex(query: str) -> str | None:
    """Plate-shaped search regex, or ``None`` so callers use text search.

    Mirrors desktop ``import_truck_check.truck_field_search_regex`` for the
    common plate / digit forms used in SM Burhani truck cells.
    """
    raw = str(query or "").strip()
    if not raw:
        return None
    plate = re.match(r"^T\s*\d+\s*[A-Z]{2,3}$", raw, re.I)
    if plate:
        compact = re.sub(r"\s+", "", raw.upper())
        body = _plate_flex_body(compact)
        return rf"^{body}(?=\s*/|$)"
    digits = re.match(r"^T?\s*(\d+)$", raw, re.I)
    if digits:
        body = _plate_flex_body(digits.group(1))
        return rf"^T?\s*{body}(?=\s*[A-Z]|\s*/|$)"
    return None


def truck_search_clauses(search: str) -> list[dict[str, Any]]:
    """Match SM Burhani truck cells; plate queries do not scan other columns."""
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
        {"border": rx},
        {"consignment": rx},
        {"truck_and_trailer": rx},
    ]


def build_query(table: str, station: str = "", search: str = "") -> dict[str, Any]:
    query: dict[str, Any] = {"entity": ENTITY, "table": table}
    station = station_query_value(station)
    if station:
        query["station"] = station
    if search.strip():
        query["$or"] = truck_search_clauses(search)
    return query


def all_records_query(
    table: str,
    station: str = "",
    search: str = "",
    year: int = 0,
    month: int = 0,
) -> dict[str, Any]:
    query = build_query(table, station, search)
    if year > 0:
        if month >= 1:
            start = datetime(year, month, 1)
            end = (
                datetime(year + 1, 1, 1)
                if month == 12
                else datetime(year, month + 1, 1)
            )
        else:
            start = datetime(year, 1, 1)
            end = datetime(year + 1, 1, 1)
        query["$and"] = list(query.get("$and") or [])
        query["$and"].append(
            {
                "$or": [
                    {"t1_date": {"$gte": start, "$lt": end}},
                    {
                        "$and": [
                            {
                                "$or": [
                                    {"t1_date": {"$exists": False}},
                                    {"t1_date": None},
                                ]
                            },
                            {"import_date": {"$gte": start, "$lt": end}},
                        ]
                    },
                ]
            }
        )
    return query


def upload_record_query(
    upload_id: str, table: str, station: str = "", search: str = ""
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "entity": ENTITY,
        "table": table,
        "upload_id": upload_id,
    }
    station = station_query_value(station)
    if station:
        query["station"] = station
    if search.strip():
        query["$or"] = truck_search_clauses(search)
    return query


def totals_group_stage() -> dict[str, Any]:
    return {
        "$group": {
            "_id": None,
            "count": {"$sum": 1},
            "charge_total": {"$sum": safe_double("charge")},
            "confirmed_total": {
                "$sum": {"$cond": ["$confirmed", safe_double("charge"), 0]}
            },
            "disputed_count": {"$sum": {"$cond": ["$disputed", 1, 0]}},
        }
    }


def totals_from_aggregate(rows: list[dict] | None) -> dict[str, Any]:
    if not rows:
        return dict(_EMPTY_TOTALS)
    r = rows[0]
    invoiced = float(r.get("charge_total") or 0.0)
    confirmed = float(r.get("confirmed_total") or 0.0)
    return {
        "count": int(r.get("count") or 0),
        "invoiced": invoiced,
        "confirmed": confirmed,
        "variance": invoiced - confirmed,
        "disputed": int(r.get("disputed_count") or 0),
    }


def uploads_pipeline(table: str) -> list[dict[str, Any]]:
    return [
        {
            "$match": {
                "entity": ENTITY,
                "table": table,
                "upload_id": {"$exists": True, "$ne": ""},
            }
        },
        {"$sort": {"source_index": 1, "_id": 1}},
        {
            "$group": {
                "_id": "$upload_id",
                "source_filename": {"$first": "$source_filename"},
                "import_date": {"$first": "$import_date"},
                "schedule_period": {"$first": "$schedule_period"},
                "record_count": {"$sum": 1},
                "total_charge": {"$sum": safe_double("charge")},
                "stations": {"$push": "$station"},
            }
        },
        {"$sort": {"import_date": -1}},
    ]


def years_t1_pipeline(table: str, station: str = "") -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "entity": ENTITY,
        "table": table,
        "t1_date": {"$exists": True, "$ne": None},
    }
    station = station_query_value(station)
    if station:
        query["station"] = station
    return [
        {"$match": query},
        {"$group": {"_id": {"$year": "$t1_date"}}},
    ]


def years_import_pipeline(table: str, station: str = "") -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "entity": ENTITY,
        "table": table,
        "import_date": {"$exists": True, "$ne": None},
    }
    station = station_query_value(station)
    if station:
        query["station"] = station
    return [
        {"$match": query},
        {"$group": {"_id": {"$year": "$import_date"}}},
    ]


def upload_stations_pipeline(upload_id: str, table: str) -> list[dict[str, Any]]:
    return [
        {
            "$match": {
                "entity": ENTITY,
                "table": table,
                "upload_id": upload_id,
                "station": {"$exists": True, "$nin": [None, ""]},
            }
        },
        {
            "$group": {
                "_id": "$station",
                "first_idx": {"$min": "$source_index"},
                "first_id": {"$min": "$_id"},
            }
        },
    ]


def upload_station_sort_key(doc: dict) -> tuple:
    idx = doc.get("first_idx")
    if isinstance(idx, (int, float)):
        return (0, int(idx), "")
    return (1, 0, str(doc.get("first_id") or ""))


def rows_sort() -> list[tuple[str, int]]:
    return [("sr_no", 1), ("import_date", 1)]


def all_records_sort() -> list[tuple[str, int]]:
    return [("t1_date", -1), ("import_date", -1), ("sr_no", 1)]


def upload_records_sort(station: str = "") -> list[tuple[str, int]]:
    if station_query_value(station):
        return [("sr_no", 1), ("import_date", 1)]
    return [("source_index", 1), ("_id", 1)]


def default_bonds_station_docs() -> list[dict[str, Any]]:
    return [
        {
            "entity": ENTITY,
            "table": "bonds",
            "slug": slug,
            "name": name,
            "border_post": border,
            "active": True,
            "sort_order": i,
        }
        for i, (slug, name, border) in enumerate(_DEFAULT_STATIONS)
    ]


def dedup_key(prn_number: str, entry_reg_no: str) -> str:
    return f"{(prn_number or '').strip()}|{(entry_reg_no or '').strip()}"


def entry_document_from_row(
    table: str,
    row: dict[str, Any],
    *,
    source_index: int,
    imported_by: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a Mongo document for one reconciliation row insert."""
    stamp = now or datetime.utcnow()
    prn = str(row.get("prn_number") or "")
    entry_reg = str(row.get("entry_reg_no") or "")
    doc: dict[str, Any] = {
        "entity": ENTITY,
        "table": table,
        "station": str(row.get("station") or "").strip(),
        "schedule_period": str(row.get("schedule_period") or ""),
        "sr_no": row.get("sr_no"),
        "sm_ref_no": str(row.get("sm_ref_no") or ""),
        "prn_number": prn,
        "entry_reg_no": entry_reg,
        "t1_no": str(row.get("t1_no") or ""),
        "t1_date": row.get("t1_date"),
        "importer": str(row.get("importer") or ""),
        "consignment": str(row.get("consignment") or ""),
        "truck_and_trailer": str(row.get("truck_and_trailer") or ""),
        "charge": float(row.get("charge") or 0.0),
        "asycuda_amount": row.get("asycuda_amount"),
        "exporter": str(row.get("exporter") or ""),
        "confirmed": bool(row.get("confirmed") or False),
        "disputed": bool(row.get("disputed") or False),
        "dispute_note": str(row.get("dispute_note") or ""),
        "dedup_key": dedup_key(prn, entry_reg),
        "import_date": row.get("import_date") or stamp,
        "imported_by": imported_by,
        "upload_id": str(row.get("upload_id") or ""),
        "source_filename": str(row.get("source_filename") or ""),
        "source_index": source_index,
    }
    return doc
