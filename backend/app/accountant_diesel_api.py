"""Accountant Diesel Cash routes (Phase 4.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from .accountant_diesel_ops import (
    DIESEL_CASH_CATEGORIES,
    DIESEL_CASH_ITEMS_SETTING,
    MONTH_NAMES,
    build_diesel_cash_query,
    diesel_cash_sort_clauses,
    diesel_cash_verified_match,
    normalize_diesel_cash_item_names,
)
from .audit_ops import record_operation_event
from .dependencies import require_roles
from .schemas import DieselCashItemsRequest
from .serialization import json_safe
from .mongo_async import aggregate_list

diesel_cash = APIRouter(prefix="/diesel-cash", tags=["accountant"])

Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _parse_optional_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


async def _load_item_names(db: Any) -> list[str]:
    try:
        doc = await db.system_settings.find_one({"key": DIESEL_CASH_ITEMS_SETTING})
    except Exception:
        doc = None
    if doc is None or "value" not in doc:
        return list(DIESEL_CASH_CATEGORIES)
    return normalize_diesel_cash_item_names(doc.get("value"))


def _common_query_params(
    year: int | None,
    month: int,
    search: str,
    truck: str,
    receipt: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> dict[str, Any]:
    return {
        "year": year,
        "month": month,
        "search": search,
        "truck": truck,
        "receipt": receipt,
        "date_from": _parse_optional_dt(date_from),
        "date_to": _parse_optional_dt(date_to),
    }


@diesel_cash.get("/items")
async def get_diesel_cash_items(request: Request, _manager: Manager) -> dict:
    names = await _load_item_names(_database(request))
    return {"items": names}


@diesel_cash.put("/items")
async def put_diesel_cash_items(
    request: Request,
    body: DieselCashItemsRequest,
    manager: Manager,
) -> dict:
    cleaned = normalize_diesel_cash_item_names(list(body.names or []))
    db = _database(request)
    await db.system_settings.update_one(
        {"key": DIESEL_CASH_ITEMS_SETTING},
        {"$set": {"key": DIESEL_CASH_ITEMS_SETTING, "value": cleaned}},
        upsert=True,
    )
    await record_operation_event(
        db,
        action="diesel.items_update",
        actor=manager,
        request=request,
        entity_type="diesel_cash_items",
        details={"count": len(cleaned)},
    )
    return {"items": cleaned}


@diesel_cash.get("/month-summaries")
async def diesel_cash_month_summaries(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    db = _database(request)
    names = await _load_item_names(db)
    match = diesel_cash_verified_match(names)
    match["date"] = {
        "$gte": datetime(year, 1, 1),
        "$lte": datetime(year, 12, 31, 23, 59, 59),
    }
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {"$month": "$date"},
                "record_count": {"$sum": 1},
                "tzs_total": {
                    "$sum": {
                        "$cond": [{"$eq": ["$currency", "TZS"]}, "$amount", 0]
                    }
                },
                "usd_total": {
                    "$sum": {
                        "$cond": [{"$eq": ["$currency", "USD"]}, "$amount", 0]
                    }
                },
                "min_date": {"$min": "$date"},
                "max_date": {"$max": "$date"},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    docs = await aggregate_list(db.transactions, pipeline, length=12)
    summaries = []
    for doc in docs:
        month_idx = int(doc["_id"])
        summaries.append(
            {
                "month": month_idx,
                "month_name": (
                    MONTH_NAMES[month_idx]
                    if 1 <= month_idx <= 12
                    else str(month_idx)
                ),
                "record_count": int(doc.get("record_count", 0)),
                "tzs_total": float(doc.get("tzs_total", 0)),
                "usd_total": float(doc.get("usd_total", 0)),
                "min_date": doc.get("min_date"),
                "max_date": doc.get("max_date"),
            }
        )
    return {"items": json_safe(summaries)}


@diesel_cash.get("/transactions")
async def diesel_cash_transactions(
    request: Request,
    _manager: Manager,
    year: int | None = Query(None, ge=2000, le=2100),
    month: int = Query(0, ge=0, le=12),
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort_field: str = "date",
    sort_asc: bool = False,
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict:
    db = _database(request)
    names = await _load_item_names(db)
    query = build_diesel_cash_query(
        **_common_query_params(
            year, month, search, truck, receipt, date_from, date_to
        ),
        item_names=names,
    )
    sort = diesel_cash_sort_clauses(sort_field, sort_asc)
    docs = (
        await db.transactions.find(query)
        .sort(sort)
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs)}


@diesel_cash.get("/transactions/count")
async def diesel_cash_transactions_count(
    request: Request,
    _manager: Manager,
    year: int | None = Query(None, ge=2000, le=2100),
    month: int = Query(0, ge=0, le=12),
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    db = _database(request)
    names = await _load_item_names(db)
    query = build_diesel_cash_query(
        **_common_query_params(
            year, month, search, truck, receipt, date_from, date_to
        ),
        item_names=names,
    )
    total = await db.transactions.count_documents(query)
    return {"count": int(total)}


@diesel_cash.get("/totals")
async def diesel_cash_totals(
    request: Request,
    _manager: Manager,
    year: int | None = Query(None, ge=2000, le=2100),
    month: int = Query(0, ge=0, le=12),
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    db = _database(request)
    names = await _load_item_names(db)
    query = build_diesel_cash_query(
        **_common_query_params(
            year, month, search, truck, receipt, date_from, date_to
        ),
        item_names=names,
    )
    result = await aggregate_list(db.transactions, 
        [
            {"$match": query},
            {
                "$group": {
                    "_id": None,
                    "tzs_total": {
                        "$sum": {
                            "$cond": [{"$eq": ["$currency", "TZS"]}, "$amount", 0]
                        }
                    },
                    "usd_total": {
                        "$sum": {
                            "$cond": [{"$eq": ["$currency", "USD"]}, "$amount", 0]
                        }
                    },
                }
            },
        ]
    , 1)
    if result:
        return {
            "tzs": float(result[0].get("tzs_total") or 0.0),
            "usd": float(result[0].get("usd_total") or 0.0),
        }
    return {"tzs": 0.0, "usd": 0.0}


@diesel_cash.get("/entries")
async def diesel_cash_entries(
    _manager: Manager,
    station: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict:
    # Desktop stub — reserved for future station-ledger entries.
    _ = (station, limit, skip)
    return {"items": []}
