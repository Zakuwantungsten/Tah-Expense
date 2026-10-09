"""Accountant category / supplier report routes (Phase 4.3)."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from .accountant_master_ops import (
    MASTER_COL_FIELDS,
    fmt_master_amount,
    master_short_name,
    merge_and_clauses,
    parse_column_filters_json,
    parse_master_filter_amount,
    parse_master_filter_date,
)
from .accountant_reports_ops import (
    build_category_report_query,
    category_report_primary_sort,
    category_report_reference_year,
    category_report_sort_clauses,
    currency_totals_group,
)
from .dependencies import require_roles
from .errors import ApiError
from .schemas import CategoriesUsageTotalsRequest
from .serialization import json_safe, object_id
from .transaction_ops import normalize_multi_filter

reports = APIRouter(prefix="/reports", tags=["accountant"])

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


def _parse_descriptions(descriptions: str) -> list[str] | None:
    vals = normalize_multi_filter(descriptions)
    return vals or None


async def _category_query(
    request: Request,
    category: str,
    *,
    description: str = "",
    descriptions: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: str = "",
) -> dict[str, Any]:
    filters = parse_column_filters_json(column_filters)
    cashier_names = list(filters.pop("cashier", []) or [])
    desc_list = _parse_descriptions(descriptions)
    df = _parse_optional_dt(date_from)
    dt = _parse_optional_dt(date_to)
    base = build_category_report_query(
        category,
        description=description,
        descriptions=desc_list,
        date_from=df,
        date_to=dt,
        column_filters=filters or None,
    )
    if not cashier_names:
        return base
    db = _database(request)
    ids = await _resolve_cashier_ids(db, cashier_names, base)
    if not ids:
        return {**base, "cashier_id": {"$in": []}}
    return build_category_report_query(
        category,
        description=description,
        descriptions=desc_list,
        date_from=df,
        date_to=dt,
        column_filters=filters or None,
        cashier_ids=ids,
    )


def _empty_totals() -> dict[str, Any]:
    return {"count": 0, "tzs": 0.0, "usd": 0.0}


async def _aggregate_currency_totals(db: Any, query: dict[str, Any]) -> dict[str, Any]:
    result = await db.transactions.aggregate(
        [{"$match": query}, {"$group": currency_totals_group()}]
    ).to_list(1)
    if not result:
        return _empty_totals()
    row = result[0]
    return {
        "count": int(row.get("count") or 0),
        "tzs": float(row.get("tzs_total") or 0.0),
        "usd": float(row.get("usd_total") or 0.0),
    }


@reports.post("/categories/usage-totals")
async def categories_usage_totals(
    request: Request,
    body: CategoriesUsageTotalsRequest,
    _manager: Manager,
) -> dict:
    names = sorted({(n or "").strip() for n in body.names if (n or "").strip()})
    if not names:
        return {"totals": {}}
    lowered = [n.lower() for n in names]
    result = await _database(request).transactions.aggregate(
        [
            {
                "$match": {
                    "verified": True,
                    "deletion_requested": {"$ne": True},
                    "trashed": {"$ne": True},
                    "category_name": {"$type": "string", "$ne": ""},
                }
            },
            {
                "$addFields": {
                    "_cat_l": {"$toLower": {"$ifNull": ["$category_name", ""]}}
                }
            },
            {"$match": {"_cat_l": {"$in": lowered}}},
            {
                "$group": {
                    **{k: v for k, v in currency_totals_group().items() if k != "_id"},
                    "_id": "$_cat_l",
                }
            },
        ]
    ).to_list(length=None)
    out: dict[str, dict[str, Any]] = {}
    for row in result:
        key = (row.get("_id") or "").strip().lower()
        if not key:
            continue
        out[key] = {
            "count": int(row.get("count") or 0),
            "tzs": float(row.get("tzs_total") or 0.0),
            "usd": float(row.get("usd_total") or 0.0),
        }
    return {"totals": out}


@reports.get("/supplier-payments/totals")
async def supplier_payments_totals(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    db = _database(request)
    try:
        docs = await db.categories.find(
            {"is_supplier": True, "active": {"$ne": False}},
            {"name": 1},
        ).to_list(length=None)
    except Exception:
        docs = []
    supplier_names = [
        str(d.get("name") or "").strip().lower()
        for d in docs
        if str(d.get("name") or "").strip()
    ]
    if not supplier_names:
        return _empty_totals()

    year_start = datetime(year, 1, 1)
    year_end = datetime(year, 12, 31, 23, 59, 59)
    result = await db.transactions.aggregate(
        [
            {
                "$match": {
                    "verified": True,
                    "deletion_requested": {"$ne": True},
                    "trashed": {"$ne": True},
                    "category_name": {"$type": "string", "$ne": ""},
                    "date": {"$gte": year_start, "$lte": year_end},
                }
            },
            {
                "$addFields": {
                    "_cat_l": {"$toLower": {"$ifNull": ["$category_name", ""]}}
                }
            },
            {"$match": {"_cat_l": {"$in": supplier_names}}},
            {"$group": currency_totals_group()},
        ]
    ).to_list(1)
    if not result:
        return _empty_totals()
    row = result[0]
    return {
        "count": int(row.get("count") or 0),
        "tzs": float(row.get("tzs_total") or 0.0),
        "usd": float(row.get("usd_total") or 0.0),
    }


@reports.get("/category/{name}/lifetime")
async def category_lifetime(
    request: Request,
    name: str,
    _manager: Manager,
) -> dict:
    cat = (name or "").strip()
    if not cat:
        return _empty_totals()
    query = await _category_query(request, cat)
    return await _aggregate_currency_totals(_database(request), query)


@reports.get("/category/{name}/totals")
async def category_totals(
    request: Request,
    name: str,
    _manager: Manager,
    description: str = "",
    descriptions: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: str = "",
) -> dict:
    cat = (name or "").strip()
    if not cat:
        return _empty_totals()
    query = await _category_query(
        request,
        cat,
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
    )
    return await _aggregate_currency_totals(_database(request), query)


@reports.get("/category/{name}/transactions")
async def category_transactions(
    request: Request,
    name: str,
    _manager: Manager,
    description: str = "",
    descriptions: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: str = "",
    sort_field: str = "date",
    sort_asc: bool = True,
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict:
    cat = (name or "").strip()
    if not cat:
        return {"items": []}
    query = await _category_query(
        request,
        cat,
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
    )
    sort = category_report_sort_clauses(sort_field, sort_asc)
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort(sort)
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs)}


@reports.get("/category/{name}/opening-balance")
async def category_opening_balance(
    request: Request,
    name: str,
    _manager: Manager,
    description: str = "",
    descriptions: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: str = "",
    sort_field: str = "date",
    sort_asc: bool = True,
    skip: int = Query(0, ge=0),
) -> dict:
    if skip <= 0:
        return {"tzs": 0.0, "usd": 0.0}
    cat = (name or "").strip()
    if not cat:
        return {"tzs": 0.0, "usd": 0.0}
    query = await _category_query(
        request,
        cat,
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
    )
    field, direction = category_report_primary_sort(sort_field, sort_asc)
    result = await _database(request).transactions.aggregate(
        [
            {"$match": query},
            {"$sort": {field: direction, "created_at": direction}},
            {"$limit": int(skip)},
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
    if not result:
        return {"tzs": 0.0, "usd": 0.0}
    row = result[0]
    return {
        "tzs": float(row.get("tzs_total") or 0.0),
        "usd": float(row.get("usd_total") or 0.0),
    }


@reports.get("/category/{name}/descriptions")
async def category_descriptions(
    request: Request,
    name: str,
    _manager: Manager,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    cat = (name or "").strip()
    if not cat:
        return {"items": []}
    query = build_category_report_query(
        cat,
        date_from=_parse_optional_dt(date_from),
        date_to=_parse_optional_dt(date_to),
    )
    vals = await _database(request).transactions.distinct("description", query)
    return {"items": sorted((v for v in vals if v), key=str.lower)}


@reports.get("/category/{name}/column-values")
async def category_column_values(
    request: Request,
    name: str,
    _manager: Manager,
    field: str = Query(..., min_length=1),
    description: str = "",
    descriptions: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: str = "",
) -> dict:
    if field not in MASTER_COL_FIELDS:
        raise ApiError(422, "invalid_field", f"Unknown column field '{field}'")

    cat = (name or "").strip()
    if not cat:
        return {"items": []}

    filters = parse_column_filters_json(column_filters)
    filters = {
        k: v for k, v in filters.items() if k != field and k in MASTER_COL_FIELDS
    }
    query = await _category_query(
        request,
        cat,
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=json.dumps(filters) if filters else "",
    )
    db = _database(request)
    df = _parse_optional_dt(date_from)
    dt = _parse_optional_dt(date_to)
    ref_year = category_report_reference_year(df, dt)
    all_years = df is None and dt is None

    if field == "date":
        vals = await db.transactions.distinct("date", query)
        out: set[str] = set()
        for v in vals:
            if v is None:
                continue
            try:
                if isinstance(v, datetime):
                    fmt = "%d %b %Y" if all_years else "%d %b"
                    out.add(v.strftime(fmt))
                elif isinstance(v, date):
                    fmt = "%d %b %Y" if all_years else "%d %b"
                    out.add(v.strftime(fmt))
            except Exception:
                continue
        return {
            "items": sorted(
                out,
                key=lambda s: (
                    parse_master_filter_date(s, ref_year) or datetime.max,
                    s,
                ),
            )
        }

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
