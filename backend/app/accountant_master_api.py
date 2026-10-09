"""Accountant Master Expenses routes (Phase 4.1–4.2)."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from .accountant_master_ops import (
    MASTER_COL_FIELDS,
    MASTER_EDIT_FILTER,
    build_master_query,
    exclude_supplier_payments,
    fmt_master_amount,
    ledger_sort_clauses,
    master_period_clause,
    master_short_name,
    master_year_bounds,
    merge_and_clauses,
    parse_column_filters_json,
    parse_master_filter_amount,
    parse_master_filter_date,
    prepare_master_updates,
    sum_amount_if_currency,
)
from .dependencies import require_roles
from .errors import ApiError
from .schemas import BulkMasterUpdateRequest, MasterPatchRequest
from .security import utcnow
from .serialization import json_safe, object_id

master = APIRouter(prefix="/master", tags=["accountant"])

Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _parse_optional_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _normalize_user_id(value: Any) -> ObjectId | None:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return value
    try:
        return object_id(str(value))
    except ValueError:
        return None


async def _supplier_names_lower(db: Any) -> list[str]:
    try:
        docs = await db.categories.find(
            {"is_supplier": True, "active": {"$ne": False}},
            {"name": 1},
        ).to_list(length=None)
    except Exception:
        return []
    names: list[str] = []
    for doc in docs:
        name = str(doc.get("name") or "").strip().lower()
        if name:
            names.append(name)
    return names


async def _cashier_names_map(db: Any, cashier_ids: list[ObjectId]) -> dict[ObjectId, str]:
    if not cashier_ids:
        return {}
    docs = await db.users.find(
        {"_id": {"$in": list(set(cashier_ids))}},
        {"_id": 1, "full_name": 1, "username": 1},
    ).to_list(length=None)
    out: dict[ObjectId, str] = {}
    for doc in docs:
        name = (doc.get("full_name") or "").strip()
        if not name:
            name = (doc.get("username") or "").strip()
        out[doc["_id"]] = name or "Unknown cashier"
    return out


async def _resolve_cashier_ids(
    db: Any, short_names: list[str], base_query: dict[str, Any]
) -> list[ObjectId]:
    wanted = {s.strip() for s in short_names if s and s.strip() and s.strip() != "—"}
    if not wanted:
        return []
    ids = await db.transactions.distinct("cashier_id", base_query)
    oids = [oid for oid in (_normalize_user_id(i) for i in ids) if oid is not None]
    if not oids:
        return []
    names = await _cashier_names_map(db, oids)
    matched: list[ObjectId] = []
    for cid, full in names.items():
        label = master_short_name(full)
        if label in wanted or (full or "").strip() in wanted:
            matched.append(cid)
    return matched


async def _master_query(
    request: Request,
    *,
    year: int | None,
    month: int,
    search: str,
    truck: str,
    category: str,
    receipt: str,
    description: str,
    date_from: datetime | None,
    date_to: datetime | None,
    column_filters_raw: str,
) -> dict[str, Any]:
    db = _database(request)
    filters = parse_column_filters_json(column_filters_raw)
    cashier_names = list(filters.pop("cashier", []) or [])
    suppliers = await _supplier_names_lower(db)
    base = build_master_query(
        year=year,
        month=month,
        search=search,
        truck=truck,
        category=category,
        receipt=receipt,
        description=description,
        date_from=_parse_optional_dt(date_from),
        date_to=_parse_optional_dt(date_to),
        column_filters=filters or None,
    )
    base = exclude_supplier_payments(base, suppliers)
    if not cashier_names:
        return base
    ids = await _resolve_cashier_ids(db, cashier_names, base)
    if not ids:
        return {**base, "cashier_id": {"$in": []}}
    resolved = build_master_query(
        year=year,
        month=month,
        search=search,
        truck=truck,
        category=category,
        receipt=receipt,
        description=description,
        date_from=_parse_optional_dt(date_from),
        date_to=_parse_optional_dt(date_to),
        column_filters=filters or None,
        cashier_ids=ids,
    )
    return exclude_supplier_payments(resolved, suppliers)


def _common_filters(
    year: int | None,
    month: int,
    search: str,
    truck: str,
    category: str,
    receipt: str,
    description: str,
    date_from: datetime | None,
    date_to: datetime | None,
    column_filters: str,
) -> dict[str, Any]:
    return {
        "year": year,
        "month": month,
        "search": search,
        "truck": truck,
        "category": category,
        "receipt": receipt,
        "description": description,
        "date_from": date_from,
        "date_to": date_to,
        "column_filters_raw": column_filters,
    }


@master.get("")
async def list_master(
    request: Request,
    _manager: Manager,
    year: int | None = Query(None, ge=2000, le=2100),
    month: int = Query(0, ge=0, le=12),
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: str = "",
    sort_field: str = "date",
    sort_asc: bool = False,
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict:
    query = await _master_query(
        request, **_common_filters(
            year, month, search, truck, category, receipt, description,
            date_from, date_to, column_filters,
        )
    )
    sort = ledger_sort_clauses(sort_field, sort_asc)
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort(sort)
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@master.get("/count")
async def count_master(
    request: Request,
    _manager: Manager,
    year: int | None = Query(None, ge=2000, le=2100),
    month: int = Query(0, ge=0, le=12),
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: str = "",
) -> dict:
    query = await _master_query(
        request, **_common_filters(
            year, month, search, truck, category, receipt, description,
            date_from, date_to, column_filters,
        )
    )
    total = await _database(request).transactions.count_documents(query)
    return {"count": int(total)}


@master.get("/totals")
async def master_totals(
    request: Request,
    _manager: Manager,
    year: int | None = Query(None, ge=2000, le=2100),
    month: int = Query(0, ge=0, le=12),
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: str = "",
) -> dict:
    query = await _master_query(
        request, **_common_filters(
            year, month, search, truck, category, receipt, description,
            date_from, date_to, column_filters,
        )
    )
    result = await _database(request).transactions.aggregate(
        [
            {"$match": query},
            {
                "$group": {
                    "_id": None,
                    "tzs_total": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$in": [
                                        {"$toUpper": "$currency"},
                                        ["TZS", "TSH", "TZ"],
                                    ]
                                },
                                "$amount",
                                0,
                            ]
                        }
                    },
                    "usd_total": {
                        "$sum": {
                            "$cond": [
                                {"$eq": [{"$toUpper": "$currency"}, "USD"]},
                                "$amount",
                                0,
                            ]
                        }
                    },
                }
            },
        ]
    ).to_list(1)
    if result:
        return {
            "tzs": float(result[0].get("tzs_total") or 0),
            "usd": float(result[0].get("usd_total") or 0),
        }
    return {"tzs": 0.0, "usd": 0.0}


@master.get("/month-totals")
async def master_month_totals(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    start = datetime(year, 1, 1)
    end = datetime(year, 12, 31, 23, 59, 59)
    pipeline = [
        {
            "$match": {
                "verified": True,
                "deletion_requested": {"$ne": True},
                "trashed": {"$ne": True},
                **master_period_clause(start, end),
            }
        },
        {
            "$group": {
                "_id": {"$month": "$date"},
                "tzs": sum_amount_if_currency("TZS", "TSH", "TZ"),
                "usd": sum_amount_if_currency("USD"),
                "zmw": sum_amount_if_currency("ZMW", "ZMB", "ZK"),
                "count": {"$sum": 1},
            }
        },
    ]
    docs = await _database(request).transactions.aggregate(pipeline).to_list(12)
    months = {
        str(int(doc["_id"])): {
            "tzs": float(doc.get("tzs") or 0),
            "usd": float(doc.get("usd") or 0),
            "zmw": float(doc.get("zmw") or 0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
        if doc.get("_id") is not None
    }
    return {"year": year, "months": months}


@master.get("/years")
async def master_years(request: Request, _manager: Manager) -> dict:
    pipeline = [
        {
            "$match": {
                "verified": True,
                "deletion_requested": {"$ne": True},
                "trashed": {"$ne": True},
            }
        },
        {
            "$project": {
                "years": {
                    "$setUnion": [
                        {
                            "$cond": [
                                {"$ne": [{"$ifNull": ["$date", None]}, None]},
                                [{"$year": "$date"}],
                                [],
                            ]
                        },
                        {
                            "$cond": [
                                {
                                    "$ne": [
                                        {"$ifNull": ["$import_primary_date", None]},
                                        None,
                                    ]
                                },
                                [{"$year": "$import_primary_date"}],
                                [],
                            ]
                        },
                    ]
                }
            }
        },
        {"$unwind": "$years"},
        {"$group": {"_id": "$years"}},
        {"$sort": {"_id": -1}},
    ]
    docs = await _database(request).transactions.aggregate(pipeline).to_list(50)
    years = [int(d["_id"]) for d in docs if d.get("_id") is not None]
    if not years:
        y = date.today().year
        return {"items": [y - 1, y, y + 1]}
    lo = min(years) - 1
    hi = max(max(years), date.today().year) + 1
    return {"items": list(range(hi, lo - 1, -1))}


@master.get("/meta/trucks")
async def master_trucks(
    request: Request,
    _manager: Manager,
    year: int | None = Query(None, ge=2000, le=2100),
) -> dict:
    start, end, _ = master_year_bounds(year, 0)
    query = {
        "verified": True,
        **master_period_clause(start, end),
    }
    vals = await _database(request).transactions.distinct("truck_number", query)
    return {"items": sorted(v for v in vals if v)}


@master.get("/meta/categories")
async def master_categories(
    request: Request,
    _manager: Manager,
    year: int | None = Query(None, ge=2000, le=2100),
) -> dict:
    start, end, _ = master_year_bounds(year, 0)
    query = {
        "verified": True,
        **master_period_clause(start, end),
    }
    vals = await _database(request).transactions.distinct("category_name", query)
    return {"items": sorted(v for v in vals if v)}


@master.get("/meta/column-values")
async def master_column_values(
    request: Request,
    _manager: Manager,
    field: str = Query(..., min_length=1),
    year: int | None = Query(None, ge=2000, le=2100),
    month: int = Query(0, ge=0, le=12),
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: str = "",
) -> dict:
    if field not in MASTER_COL_FIELDS:
        raise ApiError(422, "invalid_field", f"Unknown column field '{field}'")

    filters = parse_column_filters_json(column_filters)
    filters = {
        k: v for k, v in filters.items() if k != field and k in MASTER_COL_FIELDS
    }
    column_filters_raw = json.dumps(filters) if filters else ""
    query = await _master_query(
        request,
        **_common_filters(
            year,
            month,
            search,
            truck,
            category,
            receipt,
            description,
            date_from,
            date_to,
            column_filters_raw,
        ),
    )
    db = _database(request)
    _year = year or date.today().year

    if field == "date":
        vals = await db.transactions.distinct("date", query)
        out: set[str] = set()
        for v in vals:
            if v is None:
                continue
            try:
                if isinstance(v, datetime):
                    out.add(v.strftime("%d %b"))
                elif isinstance(v, date):
                    out.add(v.strftime("%d %b"))
            except Exception:
                continue
        items = sorted(
            out,
            key=lambda s: (parse_master_filter_date(s, _year) or datetime.max, s),
        )
        return {"items": items}

    if field == "item":
        items_raw = await db.transactions.distinct("item", query)
        cats = await db.transactions.distinct("category_name", query)
        names = {*(v for v in items_raw if v), *(v for v in cats if v)}
        return {"items": sorted(names, key=str.lower)}

    if field == "receipt_status":
        raw = await db.transactions.distinct("receipt_status", query)
        labels: set[str] = set()
        for st in raw:
            key = (st or "pending").strip().lower().replace(" ", "_")
            if key in ("missing", "no_receipt", "no", "n/a", "none"):
                labels.add("No Receipt")
            elif key == "received":
                labels.add("Received")
            else:
                labels.add("Pending")
        return {"items": sorted(labels)}

    if field in ("tzs", "usd"):
        cur_match = (
            {"$in": [{"$toUpper": "$currency"}, ["TZS", "TSH", "TZ"]]}
            if field == "tzs"
            else {"$eq": [{"$toUpper": "$currency"}, "USD"]}
        )
        decimals = 0 if field == "tzs" else 2
        docs = await db.transactions.aggregate(
            [
                {"$match": query},
                {"$match": {"$expr": cur_match}},
                {"$group": {"_id": "$amount"}},
                {"$limit": 5000},
            ]
        ).to_list(5000)
        return {
            "items": sorted(
                {
                    fmt_master_amount(float(d["_id"]), decimals=decimals)
                    for d in docs
                    if d.get("_id") is not None
                },
                key=lambda s: parse_master_filter_amount(s) or 0.0,
            )
        }

    if field == "ref_float":
        vals = await db.transactions.distinct("ref_float", query)
        out_refs = {(v or "").strip().upper() for v in vals if (v or "").strip()}
        legacy_q = merge_and_clauses(
            dict(query),
            [
                {"notes_flag": True},
                {
                    "$or": [
                        {"ref_float": {"$exists": False}},
                        {"ref_float": ""},
                        {"ref_float": None},
                    ]
                },
            ],
        )
        if await db.transactions.count_documents(legacy_q, limit=1):
            out_refs.add("REFUND TO FLOAT")
        return {"items": sorted(out_refs, key=str.lower)}

    if field == "cashier":
        ids = await db.transactions.distinct("cashier_id", query)
        oids = [oid for oid in (_normalize_user_id(i) for i in ids) if oid is not None]
        names = await _cashier_names_map(db, oids) if oids else {}
        labels = {master_short_name(n) for n in names.values() if n}
        return {"items": sorted(labels, key=str.lower)}

    db_field = {
        "description": "description",
        "truck_number": "truck_number",
        "memo": "memo",
        "ownership": "ownership",
        "approver": "approver",
    }.get(field)
    if not db_field:
        return {"items": []}
    vals = await db.transactions.distinct(db_field, query)
    return {"items": sorted((v for v in vals if v), key=str.lower)}


def _valid_id(value: str) -> ObjectId:
    try:
        return object_id(value)
    except ValueError:
        raise ApiError(422, "invalid_id", "Resource id is invalid") from None


def _actor_oid(user: dict[str, Any]) -> ObjectId:
    value = user.get("_id")
    if isinstance(value, ObjectId):
        return value
    try:
        return object_id(str(value))
    except ValueError:
        raise ApiError(500, "invalid_actor", "Authenticated user id is invalid") from None


async def _audit(
    request: Request,
    *,
    action: str,
    actor: dict[str, Any],
    entity_ids: list[ObjectId],
    details: dict[str, Any] | None = None,
) -> None:
    try:
        await _database(request).operation_events.insert_one(
            {
                "ts": utcnow(),
                "action": action,
                "actor_id": str(actor.get("_id") or ""),
                "actor_username": actor.get("username"),
                "actor_role": actor.get("role"),
                "entity_type": "transaction",
                "entity_ids": [str(oid) for oid in entity_ids],
                "outcome": "ok",
                "error": None,
                "request_id": getattr(request.state, "request_id", None)
                or uuid.uuid4().hex,
                "client": "api",
                "details": details or {},
            }
        )
    except Exception:
        pass


@master.post("/bulk-update")
async def bulk_update_master(
    body: BulkMasterUpdateRequest, request: Request, manager: Manager
) -> dict:
    ids = [_valid_id(str(raw).strip()) for raw in body.ids if str(raw).strip()]
    if not ids:
        return {"updated": 0}
    prepared = prepare_master_updates(dict(body.updates or {}))
    if not prepared:
        return {"updated": 0}
    prepared["last_edited_at"] = utcnow().replace(tzinfo=None)
    prepared["last_edited_by"] = _actor_oid(manager)
    result = await _database(request).transactions.update_many(
        {"_id": {"$in": ids}, **MASTER_EDIT_FILTER},
        {"$set": prepared},
    )
    n = int(result.matched_count)
    if n:
        await _audit(
            request,
            action="txn.master_update_bulk",
            actor=manager,
            entity_ids=ids,
            details={
                "count": n,
                "fields": sorted(str(k) for k in prepared.keys()),
            },
        )
    return {"updated": n}


@master.patch("/{tx_id}")
async def update_master(
    tx_id: str, body: MasterPatchRequest, request: Request, manager: Manager
) -> dict:
    oid = _valid_id(tx_id)
    prepared = prepare_master_updates(dict(body.updates or {}))
    if not prepared:
        return {"ok": False}
    prepared["last_edited_at"] = utcnow().replace(tzinfo=None)
    prepared["last_edited_by"] = _actor_oid(manager)
    result = await _database(request).transactions.update_one(
        {"_id": oid, **MASTER_EDIT_FILTER},
        {"$set": prepared},
    )
    ok = result.matched_count == 1
    if ok:
        await _audit(
            request,
            action="txn.master_update",
            actor=manager,
            entity_ids=[oid],
            details={"fields": sorted(str(k) for k in prepared.keys())},
        )
    return {"ok": ok}
