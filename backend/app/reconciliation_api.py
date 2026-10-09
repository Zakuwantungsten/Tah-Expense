"""Reconciliation HTTP routes (Phase 6) — bonds & RPA schedule."""

from __future__ import annotations

from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request, status

from .dependencies import require_roles
from .errors import ApiError
from .reconciliation_ops import (
    ENTITY,
    all_records_query,
    all_records_sort,
    build_query,
    default_bonds_station_docs,
    entry_document_from_row,
    rows_sort,
    station_display_name,
    station_slug,
    totals_from_aggregate,
    totals_group_stage,
    unique_station_docs,
    unique_stations_in_order,
    upload_record_query,
    upload_records_sort,
    upload_station_sort_key,
    upload_stations_pipeline,
    uploads_pipeline,
    validate_table,
    years_import_pipeline,
    years_t1_pipeline,
)
from .schemas import (
    ReconExistingKeysRequest,
    ReconRowStatusPatch,
    ReconRowsCreateRequest,
    ReconStationCreateRequest,
    ReconStationsEnsureRequest,
)
from .security import utcnow
from .serialization import json_safe, object_id
from .mongo_async import aggregate_list

reconciliation = APIRouter(prefix="/reconciliation", tags=["reconciliation"])
Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _table(table: str) -> str:
    try:
        return validate_table(table)
    except ValueError as exc:
        raise ApiError(422, "invalid_table", str(exc)) from None


def _valid_id(value: str) -> ObjectId:
    try:
        return object_id(value)
    except ValueError:
        raise ApiError(422, "invalid_id", "Resource id is invalid") from None


def _actor_oid(user: dict[str, Any]) -> ObjectId | None:
    raw = user.get("_id")
    if isinstance(raw, ObjectId):
        return raw
    try:
        return object_id(str(raw)) if raw is not None else None
    except ValueError:
        return None


def _naive(dt: Any) -> Any:
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


async def _add_station(
    db: Any,
    table: str,
    name: str,
    *,
    border_post: str = "",
) -> dict[str, Any]:
    station_key = station_slug(name)
    if not station_key:
        raise ApiError(422, "invalid_station", "Station name is invalid")
    existing = await db.recon_stations.find_one(
        {"entity": ENTITY, "table": table, "slug": station_key}
    )
    if existing:
        await db.recon_stations.update_one(
            {"_id": existing["_id"]}, {"$set": {"active": True}}
        )
        existing = dict(existing)
        existing["active"] = True
        return existing
    count = await db.recon_stations.count_documents(
        {"entity": ENTITY, "table": table}
    )
    doc = {
        "entity": ENTITY,
        "table": table,
        "slug": station_key,
        "name": name.strip(),
        "border_post": (border_post or "").strip(),
        "active": True,
        "sort_order": count,
    }
    result = await db.recon_stations.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def _ensure_stations(db: Any, table: str, slugs: list[str]) -> None:
    """Create/activate station chips for every station found in an import."""
    for slug in unique_stations_in_order(slugs):
        await _add_station(db, table, station_display_name(slug))


@reconciliation.get("/{table}/rows")
async def list_rows(
    table: str,
    request: Request,
    _manager: Manager,
    station: str = "",
    search: str = "",
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict[str, Any]:
    table = _table(table)
    query = build_query(table, station, search)
    cursor = (
        _database(request)
        .reconciliation_entries.find(query)
        .sort(rows_sort())
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return {"items": json_safe(docs), "limit": limit, "skip": skip}


@reconciliation.get("/{table}/rows/count")
async def count_rows(
    table: str,
    request: Request,
    _manager: Manager,
    station: str = "",
    search: str = "",
) -> dict[str, Any]:
    table = _table(table)
    total = await _database(request).reconciliation_entries.count_documents(
        build_query(table, station, search)
    )
    return {"total": int(total)}


@reconciliation.get("/{table}/totals")
async def row_totals(
    table: str,
    request: Request,
    _manager: Manager,
    station: str = "",
) -> dict[str, Any]:
    table = _table(table)
    query = build_query(table, station)
    rows = await aggregate_list(
        _database(request).reconciliation_entries,
        [{"$match": query}, totals_group_stage()],
        1,
    )
    return totals_from_aggregate(rows)


@reconciliation.get("/{table}/all")
async def list_all_records(
    table: str,
    request: Request,
    _manager: Manager,
    station: str = "",
    search: str = "",
    year: int = Query(0, ge=0, le=2100),
    month: int = Query(0, ge=0, le=12),
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict[str, Any]:
    table = _table(table)
    query = all_records_query(table, station, search, year, month)
    cursor = (
        _database(request)
        .reconciliation_entries.find(query)
        .sort(all_records_sort())
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return {"items": json_safe(docs), "limit": limit, "skip": skip}


@reconciliation.get("/{table}/all/count")
async def count_all_records(
    table: str,
    request: Request,
    _manager: Manager,
    station: str = "",
    search: str = "",
    year: int = Query(0, ge=0, le=2100),
    month: int = Query(0, ge=0, le=12),
) -> dict[str, Any]:
    table = _table(table)
    total = await _database(request).reconciliation_entries.count_documents(
        all_records_query(table, station, search, year, month)
    )
    return {"total": int(total)}


@reconciliation.get("/{table}/all/totals")
async def all_record_totals(
    table: str,
    request: Request,
    _manager: Manager,
    station: str = "",
    search: str = "",
    year: int = Query(0, ge=0, le=2100),
    month: int = Query(0, ge=0, le=12),
) -> dict[str, Any]:
    table = _table(table)
    query = all_records_query(table, station, search, year, month)
    rows = await aggregate_list(
        _database(request).reconciliation_entries,
        [{"$match": query}, totals_group_stage()],
        1,
    )
    return totals_from_aggregate(rows)


@reconciliation.get("/{table}/years")
async def available_years(
    table: str,
    request: Request,
    _manager: Manager,
    station: str = "",
) -> dict[str, Any]:
    table = _table(table)
    db = _database(request)
    years: set[int] = set()
    for pipeline in (
        years_t1_pipeline(table, station),
        years_import_pipeline(table, station),
    ):
        for doc in await aggregate_list(db.reconciliation_entries, pipeline, 
            length=None
        ):
            yr = doc.get("_id")
            if isinstance(yr, int) and 1990 <= yr <= 2100:
                years.add(yr)
    return {"items": sorted(years, reverse=True)}


@reconciliation.get("/{table}/uploads")
async def list_uploads(
    table: str, request: Request, _manager: Manager
) -> dict[str, Any]:
    table = _table(table)
    docs = await aggregate_list(
        _database(request).reconciliation_entries,
        uploads_pipeline(table),
        length=None,
    )
    items = []
    for doc in docs:
        item = dict(doc)
        item["upload_id"] = item.pop("_id", None)
        item["stations"] = unique_stations_in_order(item.get("stations") or [])
        items.append(item)
    return {"items": json_safe(items)}


@reconciliation.get("/{table}/uploads/{upload_id}/stations")
async def upload_stations(
    table: str, upload_id: str, request: Request, _manager: Manager
) -> dict[str, Any]:
    table = _table(table)
    if not (upload_id or "").strip():
        return {"items": []}
    docs = await aggregate_list(
        _database(request).reconciliation_entries,
        upload_stations_pipeline(upload_id.strip(), table),
        length=None,
    )
    docs.sort(key=upload_station_sort_key)
    items = []
    for doc in docs:
        slug = str(doc.get("_id") or "").strip()
        if not slug:
            continue
        items.append({"slug": slug, "name": station_display_name(slug)})
    return {"items": items}


@reconciliation.get("/{table}/uploads/{upload_id}/records")
async def upload_records(
    table: str,
    upload_id: str,
    request: Request,
    _manager: Manager,
    station: str = "",
    search: str = "",
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict[str, Any]:
    table = _table(table)
    query = upload_record_query(upload_id, table, station, search)
    cursor = (
        _database(request)
        .reconciliation_entries.find(query)
        .sort(upload_records_sort(station))
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return {"items": json_safe(docs), "limit": limit, "skip": skip}


@reconciliation.get("/{table}/uploads/{upload_id}/records/count")
async def count_upload_records(
    table: str,
    upload_id: str,
    request: Request,
    _manager: Manager,
    station: str = "",
    search: str = "",
) -> dict[str, Any]:
    table = _table(table)
    total = await _database(request).reconciliation_entries.count_documents(
        upload_record_query(upload_id, table, station, search)
    )
    return {"total": int(total)}


@reconciliation.get("/{table}/uploads/{upload_id}/totals")
async def upload_totals(
    table: str,
    upload_id: str,
    request: Request,
    _manager: Manager,
    station: str = "",
) -> dict[str, Any]:
    table = _table(table)
    query = upload_record_query(upload_id, table, station)
    rows = await aggregate_list(
        _database(request).reconciliation_entries,
        [{"$match": query}, totals_group_stage()],
        1,
    )
    return totals_from_aggregate(rows)


@reconciliation.delete(
    "/{table}/uploads/{upload_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_upload(
    table: str, upload_id: str, request: Request, _manager: Manager
) -> dict[str, Any]:
    table = _table(table)
    if not (upload_id or "").strip():
        raise ApiError(422, "invalid_upload_id", "Upload id is required")
    result = await _database(request).reconciliation_entries.delete_many(
        {
            "entity": ENTITY,
            "table": table,
            "upload_id": upload_id.strip(),
        }
    )
    return {"deleted": int(result.deleted_count)}


@reconciliation.post("/{table}/existing-keys")
async def existing_keys(
    table: str,
    body: ReconExistingKeysRequest,
    request: Request,
    _manager: Manager,
) -> dict[str, Any]:
    table = _table(table)
    keys = [str(k).strip() for k in (body.keys or []) if str(k).strip()]
    if not keys:
        return {"keys": []}
    docs = await (
        _database(request)
        .reconciliation_entries.find(
            {
                "entity": ENTITY,
                "table": table,
                "dedup_key": {"$in": keys},
            },
            {"dedup_key": 1},
        )
        .to_list(length=None)
    )
    found = sorted(
        {str(d.get("dedup_key")) for d in docs if d.get("dedup_key")}
    )
    return {"keys": found}


@reconciliation.post("/{table}/rows", status_code=status.HTTP_201_CREATED)
async def save_rows(
    table: str,
    body: ReconRowsCreateRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    table = _table(table)
    rows = list(body.rows or [])
    if not rows:
        return {"inserted": 0}
    now = _naive(utcnow())
    actor = _actor_oid(manager)
    docs = []
    for index, row in enumerate(rows):
        payload = row.model_dump()
        payload["t1_date"] = _naive(payload.get("t1_date"))
        payload["import_date"] = _naive(payload.get("import_date"))
        docs.append(
            entry_document_from_row(
                table,
                payload,
                source_index=index,
                imported_by=actor,
                now=now,
            )
        )
    db = _database(request)
    result = await db.reconciliation_entries.insert_many(docs, ordered=False)
    await _ensure_stations(db, table, [d.get("station") or "" for d in docs])
    return {"inserted": len(result.inserted_ids)}


@reconciliation.patch("/{table}/rows/{row_id}")
async def patch_row_status(
    table: str,
    row_id: str,
    body: ReconRowStatusPatch,
    request: Request,
    _manager: Manager,
) -> dict[str, Any]:
    table = _table(table)
    oid = _valid_id(row_id)
    update: dict[str, Any] = {}
    if body.confirmed is not None:
        update["confirmed"] = body.confirmed
    if body.disputed is not None:
        update["disputed"] = body.disputed
    if body.dispute_note is not None:
        update["dispute_note"] = body.dispute_note
    if not update:
        raise ApiError(422, "empty_patch", "No status fields provided")
    result = await _database(request).reconciliation_entries.update_one(
        {"_id": oid, "entity": ENTITY, "table": table},
        {"$set": update},
    )
    if result.matched_count == 0:
        raise ApiError(404, "not_found", "Reconciliation row not found")
    return {"updated": bool(result.modified_count)}


@reconciliation.get("/{table}/stations")
async def list_stations(
    table: str, request: Request, _manager: Manager
) -> dict[str, Any]:
    """Return active stations, seeding Bonds defaults on first open."""
    table = _table(table)
    db = _database(request)
    cursor = db.recon_stations.find(
        {"entity": ENTITY, "table": table, "active": True}
    ).sort([("sort_order", 1), ("name", 1)])
    docs = await cursor.to_list(length=None)
    if not docs and table == "bonds":
        seed = default_bonds_station_docs()
        await db.recon_stations.insert_many(seed)
        docs = seed
    return {"items": json_safe(unique_station_docs(docs))}


@reconciliation.post("/{table}/stations/ensure")
async def ensure_stations(
    table: str,
    body: ReconStationsEnsureRequest,
    request: Request,
    _manager: Manager,
) -> dict[str, Any]:
    table = _table(table)
    await _ensure_stations(_database(request), table, list(body.slugs or []))
    return {"ok": True}


@reconciliation.post("/{table}/stations", status_code=status.HTTP_201_CREATED)
async def create_station(
    table: str,
    body: ReconStationCreateRequest,
    request: Request,
    _manager: Manager,
) -> dict[str, Any]:
    table = _table(table)
    doc = await _add_station(
        _database(request),
        table,
        body.name,
        border_post=body.border_post,
    )
    return json_safe(doc)
