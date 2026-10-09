"""Fuel overview HTTP routes (Phase 9.2)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from .accountant_diesel_ops import (
    DIESEL_CASH_CATEGORIES,
    DIESEL_CASH_ITEMS_SETTING,
    diesel_cash_verified_match,
    normalize_diesel_cash_item_names,
    truck_exact_clause,
)
from .accountant_truck_overview_ops import (
    TRUCK_OVERVIEW_DIESEL_FEEDS,
    diesel_overview_query,
    diesel_rows_from_docs,
    empty_overview_summary,
    filter_fuel_overview_rows,
    finalize_fuel_overview_rows,
    fuel_cash_rows_from_docs,
    fuel_column_filter_cache,
    parse_fuel_column_filters_json,
    parse_source_param,
    summarize_overview_rows,
    with_date_range,
)
from .dependencies import require_roles
from .serialization import json_safe

fuel_overview = APIRouter(prefix="/fuel-overview", tags=["accountant"])

Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


async def _diesel_item_names(db: Any) -> list[str]:
    try:
        doc = await db.system_settings.find_one({"key": DIESEL_CASH_ITEMS_SETTING})
    except Exception:
        doc = None
    if doc is None or "value" not in doc:
        return list(DIESEL_CASH_CATEGORIES)
    return normalize_diesel_cash_item_names(doc.get("value"))


async def _fuel_cash_rows(
    db: Any,
    truck: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[dict[str, Any]]:
    diesel_items = await _diesel_item_names(db)
    query = with_date_range(
        {
            **diesel_cash_verified_match(diesel_items),
            "truck_number": truck_exact_clause(truck),
        },
        "date",
        date_from,
        date_to,
    )
    docs = await (
        db.transactions.find(query)
        .sort([("date", -1), ("created_at", -1)])
        .to_list(length=None)
    )
    return fuel_cash_rows_from_docs(docs)


async def _fuel_station_rows(
    db: Any,
    truck: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feed_type, label in TRUCK_OVERVIEW_DIESEL_FEEDS:
        query = diesel_overview_query(feed_type, truck, date_from, date_to)
        docs = await (
            db.imported_feeds.find(query)
            .sort(
                [
                    ("transaction_date", -1),
                    ("date", -1),
                    ("import_date", -1),
                ]
            )
            .to_list(length=None)
        )
        rows.extend(diesel_rows_from_docs(feed_type, label, docs))
    return rows


async def _load_fuel_rows(
    db: Any,
    truck: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[dict[str, Any]]:
    cash, stations = await asyncio.gather(
        _fuel_cash_rows(db, truck, date_from, date_to),
        _fuel_station_rows(db, truck, date_from, date_to),
    )
    return cash + stations


async def _prepared_rows(
    request: Request,
    *,
    truck: str,
    source: str,
    date_from: datetime | None,
    date_to: datetime | None,
    currency: str,
    column_filters_raw: str,
    sort_field: str,
    sort_asc: bool,
) -> list[dict[str, Any]]:
    plate = (truck or "").strip()
    if not plate:
        return []
    df = _naive(date_from)
    dt = _naive(date_to)
    rows = await _load_fuel_rows(_database(request), plate, df, dt)
    rows = filter_fuel_overview_rows(
        rows,
        source=parse_source_param(source),
        date_from=df,
        date_to=dt,
        currency=currency,
        sort_rows=False,
    )
    return finalize_fuel_overview_rows(
        rows,
        column_filters=parse_fuel_column_filters_json(column_filters_raw) or None,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


@fuel_overview.get("/records")
async def list_fuel_overview_records(
    request: Request,
    manager: Manager,
    truck: str = Query(..., min_length=1),
    source: str = Query("all"),
    currency: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    column_filters: str = Query(""),
    sort_field: str = Query("date"),
    sort_asc: bool = Query(True),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    del manager
    rows = await _prepared_rows(
        request,
        truck=truck,
        source=source,
        date_from=date_from,
        date_to=date_to,
        currency=currency,
        column_filters_raw=column_filters,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )
    return json_safe(rows[skip : skip + limit])


@fuel_overview.get("/records/count")
async def count_fuel_overview_records(
    request: Request,
    manager: Manager,
    truck: str = Query(..., min_length=1),
    source: str = Query("all"),
    currency: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    column_filters: str = Query(""),
    sort_field: str = Query("date"),
    sort_asc: bool = Query(True),
) -> dict[str, Any]:
    del manager
    rows = await _prepared_rows(
        request,
        truck=truck,
        source=source,
        date_from=date_from,
        date_to=date_to,
        currency=currency,
        column_filters_raw=column_filters,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )
    return {"count": len(rows)}


@fuel_overview.get("/summary")
async def fuel_overview_summary(
    request: Request,
    manager: Manager,
    truck: str = Query(..., min_length=1),
    source: str = Query("all"),
    currency: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    column_filters: str = Query(""),
    sort_field: str = Query("date"),
    sort_asc: bool = Query(True),
) -> dict[str, Any]:
    del manager
    if not (truck or "").strip():
        return empty_overview_summary()
    rows = await _prepared_rows(
        request,
        truck=truck,
        source=source,
        date_from=date_from,
        date_to=date_to,
        currency=currency,
        column_filters_raw=column_filters,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )
    return json_safe(summarize_overview_rows(rows))


@fuel_overview.get("/column-filters")
async def fuel_overview_column_filters(
    request: Request,
    manager: Manager,
    truck: str = Query(..., min_length=1),
    source: str = Query("all"),
    currency: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    column_filters: str = Query(""),
) -> dict[str, Any]:
    del manager
    plate = (truck or "").strip()
    if not plate:
        return {"filters": {}}
    df = _naive(date_from)
    dt = _naive(date_to)
    rows = await _load_fuel_rows(_database(request), plate, df, dt)
    rows = filter_fuel_overview_rows(
        rows,
        source=parse_source_param(source),
        date_from=df,
        date_to=dt,
        currency=currency,
        sort_rows=False,
    )
    cache = fuel_column_filter_cache(
        rows,
        column_filters=parse_fuel_column_filters_json(column_filters) or None,
    )
    return {"filters": cache}
