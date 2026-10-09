"""Truck overview HTTP routes (Phase 9.1)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from .accountant_diesel_ops import (
    DIESEL_CASH_CATEGORIES,
    DIESEL_CASH_ITEMS_SETTING,
    normalize_diesel_cash_item_names,
    truck_exact_clause,
)
from .accountant_truck_overview_ops import (
    TRUCK_OVERVIEW_DIESEL_FEEDS,
    afritrack_rows_from_docs,
    comesa_rows_from_docs,
    diesel_overview_query,
    diesel_rows_from_docs,
    empty_overview_summary,
    filter_truck_overview_rows,
    imported_feed_overview_query,
    master_rows_from_docs,
    parking_congo_rows_from_docs,
    parse_source_param,
    rahntech_rows_from_docs,
    recon_rows_from_docs,
    separate_expense_rows_from_docs,
    summarize_overview_rows,
    third_party_rows_from_docs,
    toll_rows_from_docs,
    with_date_range,
    zambia_rows_from_docs,
)
from .dependencies import require_roles
from .serialization import json_safe

truck_overview = APIRouter(prefix="/truck-overview", tags=["accountant"])

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


async def _master_rows(
    db: Any,
    truck: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[dict[str, Any]]:
    diesel_items = await _diesel_item_names(db)
    query = with_date_range(
        {
            "verified": True,
            "deletion_requested": {"$ne": True},
            "trashed": {"$ne": True},
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
    return master_rows_from_docs(docs, diesel_items)


async def _diesel_station_rows(
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


async def _imported_feed_rows(
    db: Any,
    truck: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[dict[str, Any]]:
    toll_docs = await (
        db.imported_feeds.find(
            imported_feed_overview_query(
                "toll_plaza", "vehicle_reg", truck, date_from, date_to
            )
        )
        .sort(
            [
                ("transaction_date", -1),
                ("toll_date", -1),
                ("import_date", -1),
            ]
        )
        .to_list(length=None)
    )
    parking_docs = await (
        db.imported_feeds.find(
            imported_feed_overview_query(
                "parking_congo", "vehicle_no", truck, date_from, date_to
            )
        )
        .sort(
            [
                ("transaction_date", -1),
                ("payment_date", -1),
                ("import_date", -1),
            ]
        )
        .to_list(length=None)
    )
    zambia_docs = await (
        db.imported_feeds.find(
            with_date_range(
                {
                    "feed_type": "zambia_parking",
                    "plate_num": truck_exact_clause(truck),
                },
                "transaction_date",
                date_from,
                date_to,
            )
        )
        .sort([("transaction_date", -1), ("import_date", -1)])
        .to_list(length=None)
    )
    return (
        toll_rows_from_docs(toll_docs)
        + parking_congo_rows_from_docs(parking_docs)
        + zambia_rows_from_docs(zambia_docs)
    )


async def _separate_rows(
    db: Any,
    truck: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[dict[str, Any]]:
    congo_docs = await (
        db.separate_expenses.find(
            with_date_range(
                {
                    "expense_type": "congo_expenses",
                    "truck_no": truck_exact_clause(truck),
                },
                "expense_date",
                date_from,
                date_to,
            )
        )
        .sort([("expense_date", -1), ("created_at", -1)])
        .to_list(length=None)
    )
    kimvi_docs = await (
        db.separate_expenses.find(
            with_date_range(
                {
                    "expense_type": "ahmed_kimvi",
                    "truck_no": truck_exact_clause(truck),
                },
                "expense_date",
                date_from,
                date_to,
            )
        )
        .sort([("expense_date", -1), ("created_at", -1)])
        .to_list(length=None)
    )
    return separate_expense_rows_from_docs(
        congo_docs, expense_type="congo_expenses", source_label="Congo Expenses"
    ) + separate_expense_rows_from_docs(
        kimvi_docs, expense_type="ahmed_kimvi", source_label="Ahmed Kimvi"
    )


async def _extra_rows(
    db: Any,
    truck: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[dict[str, Any]]:
    afritrack_docs = await (
        db.imported_feeds.find(
            with_date_range(
                {"feed_type": "afritrack", "truck": truck_exact_clause(truck)},
                "import_date",
                date_from,
                date_to,
            )
        )
        .sort([("import_date", -1), ("row_index", 1)])
        .to_list(length=None)
    )
    rahn_docs = await (
        db.imported_feeds.find(
            imported_feed_overview_query(
                "rahntech", "truck_number", truck, date_from, date_to
            )
        )
        .sort(
            [
                ("transaction_date", -1),
                ("sales_date", -1),
                ("import_date", -1),
            ]
        )
        .to_list(length=None)
    )
    comesa_docs = await (
        db.imported_feeds.find(
            with_date_range(
                {
                    "feed_type": "comesa",
                    "truck_reg": truck_exact_clause(truck),
                },
                "import_date",
                date_from,
                date_to,
            )
        )
        .sort([("month", -1), ("import_date", -1)])
        .to_list(length=None)
    )
    third_party_docs = await (
        db.imported_feeds.find(
            with_date_range(
                {
                    "feed_type": "third_party",
                    "reg_no": truck_exact_clause(truck),
                },
                "import_date",
                date_from,
                date_to,
            )
        )
        .sort([("month", -1), ("import_date", -1)])
        .to_list(length=None)
    )
    recon_docs = await (
        db.reconciliation_entries.find(
            with_date_range(
                {
                    "entity": "sm_burhani",
                    "truck_and_trailer": truck_exact_clause(truck),
                },
                "t1_date",
                date_from,
                date_to,
            )
        )
        .sort([("t1_date", -1), ("import_date", -1)])
        .to_list(length=None)
    )
    return (
        afritrack_rows_from_docs(afritrack_docs)
        + rahntech_rows_from_docs(rahn_docs)
        + comesa_rows_from_docs(comesa_docs)
        + third_party_rows_from_docs(third_party_docs)
        + recon_rows_from_docs(recon_docs)
    )


async def _load_rows(
    db: Any,
    truck: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[dict[str, Any]]:
    master, diesel, imported, separate, extra = await asyncio.gather(
        _master_rows(db, truck, date_from, date_to),
        _diesel_station_rows(db, truck, date_from, date_to),
        _imported_feed_rows(db, truck, date_from, date_to),
        _separate_rows(db, truck, date_from, date_to),
        _extra_rows(db, truck, date_from, date_to),
    )
    return master + diesel + imported + separate + extra


async def _filtered_rows(
    request: Request,
    *,
    truck: str,
    source: str,
    date_from: datetime | None,
    date_to: datetime | None,
    currency: str,
) -> list[dict[str, Any]]:
    plate = (truck or "").strip()
    if not plate:
        return []
    df = _naive(date_from)
    dt = _naive(date_to)
    rows = await _load_rows(_database(request), plate, df, dt)
    return filter_truck_overview_rows(
        rows,
        source=parse_source_param(source),
        date_from=df,
        date_to=dt,
        currency=currency,
    )


@truck_overview.get("/records")
async def list_truck_overview_records(
    request: Request,
    manager: Manager,
    truck: str = Query(..., min_length=1),
    source: str = Query("all"),
    currency: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    del manager
    rows = await _filtered_rows(
        request,
        truck=truck,
        source=source,
        date_from=date_from,
        date_to=date_to,
        currency=currency,
    )
    return json_safe(rows[skip : skip + limit])


@truck_overview.get("/records/count")
async def count_truck_overview_records(
    request: Request,
    manager: Manager,
    truck: str = Query(..., min_length=1),
    source: str = Query("all"),
    currency: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
) -> dict[str, Any]:
    del manager
    rows = await _filtered_rows(
        request,
        truck=truck,
        source=source,
        date_from=date_from,
        date_to=date_to,
        currency=currency,
    )
    return {"count": len(rows)}


@truck_overview.get("/summary")
async def truck_overview_summary(
    request: Request,
    manager: Manager,
    truck: str = Query(..., min_length=1),
    source: str = Query("all"),
    currency: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
) -> dict[str, Any]:
    del manager
    if not (truck or "").strip():
        return empty_overview_summary()
    rows = await _filtered_rows(
        request,
        truck=truck,
        source=source,
        date_from=date_from,
        date_to=date_to,
        currency=currency,
    )
    return json_safe(summarize_overview_rows(rows))
