"""Accountant Overview dashboard routes (Phase 4.5)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from .accountant_diesel_ops import (
    DIESEL_CASH_CATEGORIES,
    DIESEL_CASH_ITEMS_SETTING,
    diesel_cash_verified_match,
    normalize_diesel_cash_item_names,
)
from .accountant_master_ops import master_period_clause
from .accountant_ops import VERIFIED_SORT, build_verified_query
from .accountant_overview_ops import (
    DIESEL_FEED_SOURCE_GROUP,
    accumulate_diesel_cash_fuel_liters,
    add_fuel_liters_day,
    empty_fuel_liters_daily,
    safe_double,
    serialize_fuel_liters_series,
    sum_amount_if_currency,
    year_window,
)
from .dependencies import require_roles
from .serialization import json_safe

overview = APIRouter(prefix="/overview", tags=["accountant"])

Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]


def _database(request: Request) -> Any:
    return request.app.state.database.db


async def _load_diesel_item_names(db: Any) -> list[str]:
    try:
        doc = await db.system_settings.find_one({"key": DIESEL_CASH_ITEMS_SETTING})
    except Exception:
        doc = None
    if doc is None or "value" not in doc:
        return list(DIESEL_CASH_CATEGORIES)
    return normalize_diesel_cash_item_names(doc.get("value"))


async def _supplier_payments_totals(db: Any, year: int) -> dict[str, Any]:
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
        return {"count": 0, "tzs": 0.0, "usd": 0.0}
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
            {
                "$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "tzs_total": sum_amount_if_currency("TZS", "TSH", "TZ"),
                    "usd_total": sum_amount_if_currency("USD"),
                }
            },
        ]
    ).to_list(1)
    if not result:
        return {"count": 0, "tzs": 0.0, "usd": 0.0}
    row = result[0]
    return {
        "count": int(row.get("count") or 0),
        "tzs": float(row.get("tzs_total") or 0.0),
        "usd": float(row.get("usd_total") or 0.0),
    }


async def _master_month_totals(db: Any, year: int) -> dict[int, dict[str, Any]]:
    start = datetime(year, 1, 1)
    end = datetime(year, 12, 31, 23, 59, 59)
    docs = await db.transactions.aggregate(
        [
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
    ).to_list(12)
    return {
        int(doc["_id"]): {
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
        if doc.get("_id") is not None
    }


async def _month_sum_map(
    cursor_result: list[dict],
    value_key: str = "total",
) -> dict[int, float]:
    out = {m: 0.0 for m in range(1, 13)}
    for doc in cursor_result:
        month = doc.get("_id")
        if isinstance(month, int) and 1 <= month <= 12:
            out[month] = float(doc.get(value_key) or 0.0)
    return out


async def _overview_zmw_feed_month_totals(db: Any, year: int) -> dict[int, float]:
    start, end = year_window(year)
    toll_docs, zambia_docs = await asyncio.gather(
        db.imported_feeds.aggregate(
            [
                {
                    "$match": {
                        "feed_type": "toll_plaza",
                        "transaction_date": {"$gte": start, "$lt": end},
                    }
                },
                {
                    "$group": {
                        "_id": {"$month": "$transaction_date"},
                        "total": {"$sum": safe_double("tender_amount")},
                    }
                },
            ]
        ).to_list(length=None),
        db.imported_feeds.aggregate(
            [
                {
                    "$match": {
                        "feed_type": "zambia_parking",
                        "transaction_date": {"$gte": start, "$lt": end},
                    }
                },
                {
                    "$group": {
                        "_id": {"$month": "$transaction_date"},
                        "total": {"$sum": safe_double("debit")},
                    }
                },
            ]
        ).to_list(length=None),
    )
    toll = await _month_sum_map(toll_docs)
    zambia = await _month_sum_map(zambia_docs)
    return {m: toll[m] + zambia[m] for m in range(1, 13)}


async def _overview_usd_feed_month_totals(db: Any, year: int) -> dict[int, float]:
    start, end = year_window(year)
    congo_docs, kimvi_docs, parking_docs, afritrack_docs = await asyncio.gather(
        db.separate_expenses.aggregate(
            [
                {
                    "$match": {
                        "expense_type": "congo_expenses",
                        "expense_date": {"$gte": start, "$lt": end},
                    }
                },
                {
                    "$group": {
                        "_id": {"$month": "$expense_date"},
                        "total": {
                            "$sum": {
                                "$cond": [
                                    {
                                        "$gt": [
                                            {"$ifNull": ["$amount_usd", 0]},
                                            0,
                                        ]
                                    },
                                    {"$ifNull": ["$amount_usd", 0]},
                                    0,
                                ]
                            }
                        },
                    }
                },
            ]
        ).to_list(length=None),
        db.separate_expenses.aggregate(
            [
                {
                    "$match": {
                        "expense_type": "ahmed_kimvi",
                        "expense_date": {"$gte": start, "$lt": end},
                    }
                },
                {
                    "$group": {
                        "_id": {"$month": "$expense_date"},
                        "total": {
                            "$sum": {
                                "$cond": [
                                    {
                                        "$gt": [
                                            {"$ifNull": ["$amount_usd", 0]},
                                            0,
                                        ]
                                    },
                                    {"$ifNull": ["$amount_usd", 0]},
                                    0,
                                ]
                            }
                        },
                    }
                },
            ]
        ).to_list(length=None),
        db.imported_feeds.aggregate(
            [
                {
                    "$match": {
                        "feed_type": "parking_congo",
                        "transaction_date": {"$gte": start, "$lt": end},
                    }
                },
                {
                    "$group": {
                        "_id": {"$month": "$transaction_date"},
                        "total": {"$sum": safe_double("amount")},
                    }
                },
            ]
        ).to_list(length=None),
        db.imported_feeds.aggregate(
            [
                {
                    "$match": {
                        "feed_type": "afritrack",
                        "import_date": {"$gte": start, "$lt": end},
                    }
                },
                {
                    "$group": {
                        "_id": {"$month": "$import_date"},
                        "total": {"$sum": safe_double("total_invoice")},
                    }
                },
            ]
        ).to_list(length=None),
    )
    congo = await _month_sum_map(congo_docs)
    kimvi = await _month_sum_map(kimvi_docs)
    parking = await _month_sum_map(parking_docs)
    afritrack = await _month_sum_map(afritrack_docs)
    return {
        m: congo[m] + kimvi[m] + parking[m] + afritrack[m] for m in range(1, 13)
    }


async def _compute_kpis(db: Any, year: int) -> dict[str, Any]:
    today = date.today()
    month_start = datetime(today.year, today.month, 1)
    year_start = datetime(year, 1, 1)
    year_end = datetime(year, 12, 31, 23, 59, 59)

    pending_count, ytd_res, month_res = await asyncio.gather(
        db.transactions.count_documents(
            {"verified": False, "rejected": {"$ne": True}}
        ),
        db.transactions.aggregate(
            [
                {
                    "$match": {
                        "verified": True,
                        "date": {"$gte": year_start, "$lte": year_end},
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "count": {"$sum": 1},
                        "tzs_total": sum_amount_if_currency("TZS", "TSH", "TZ"),
                        "usd_total": sum_amount_if_currency("USD"),
                        "zmw_total": sum_amount_if_currency("ZMW", "ZMB", "ZK"),
                    }
                },
            ]
        ).to_list(1),
        db.transactions.aggregate(
            [
                {"$match": {"date": {"$gte": month_start}}},
                {
                    "$group": {
                        "_id": None,
                        "verified": {"$sum": {"$cond": ["$verified", 1, 0]}},
                        "total": {"$sum": 1},
                    }
                },
            ]
        ).to_list(1),
    )
    ytd = ytd_res[0] if ytd_res else {
        "count": 0,
        "tzs_total": 0.0,
        "usd_total": 0.0,
        "zmw_total": 0.0,
    }
    month = month_res[0] if month_res else {"verified": 0, "total": 0}
    return {
        "pending_count": int(pending_count),
        "master_count": int(ytd.get("count") or 0),
        "verified_this_month": int(month.get("verified") or 0),
        "submitted_this_month": int(month.get("total") or 0),
        "total_tzs_ytd": float(ytd.get("tzs_total") or 0.0),
        "total_usd_ytd": float(ytd.get("usd_total") or 0.0),
        "total_zmw_ytd": float(ytd.get("zmw_total") or 0.0),
    }


async def _compute_categories(db: Any, year: int, top_n: int) -> list[dict[str, Any]]:
    year_start = datetime(year, 1, 1)
    year_end = datetime(year, 12, 31, 23, 59, 59)
    docs = await db.transactions.aggregate(
        [
            {
                "$match": {
                    "verified": True,
                    "date": {"$gte": year_start, "$lte": year_end},
                }
            },
            {
                "$group": {
                    "_id": {"$ifNull": ["$category_name", "Uncategorised"]},
                    "tzs": sum_amount_if_currency(
                        "TZS", "TSH", "TZ", absolute=True
                    ),
                    "usd": sum_amount_if_currency("USD", absolute=True),
                    "zmw": sum_amount_if_currency(
                        "ZMW", "ZMB", "ZK", absolute=True
                    ),
                }
            },
            {"$sort": {"tzs": -1, "usd": -1, "zmw": -1}},
        ]
    ).to_list(None)
    if not docs:
        return []

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
    slices: list[dict[str, Any]] = []
    for doc in top:
        slices.append(
            {
                "name": doc["_id"] or "Uncategorised",
                "tzs": float(doc.get("tzs") or 0.0),
                "usd": float(doc.get("usd") or 0.0),
                "zmw": float(doc.get("zmw") or 0.0),
            }
        )
    if rest:
        slices.append(
            {
                "name": "Other",
                "tzs": sum(float(d.get("tzs") or 0) for d in rest),
                "usd": sum(float(d.get("usd") or 0) for d in rest),
                "zmw": sum(float(d.get("zmw") or 0) for d in rest),
            }
        )
    return slices


async def _compute_receipts(db: Any, year: int) -> dict[str, Any]:
    year_start = datetime(year, 1, 1)
    year_end = datetime(year, 12, 31, 23, 59, 59)
    docs = await db.transactions.aggregate(
        [
            {
                "$match": {
                    "verified": True,
                    "date": {"$gte": year_start, "$lte": year_end},
                }
            },
            {"$group": {"_id": "$receipt_status", "count": {"$sum": 1}}},
        ]
    ).to_list(None)
    received = pending = missing = 0
    for doc in docs:
        status = (doc.get("_id") or "pending").lower()
        count = int(doc.get("count") or 0)
        if status == "received":
            received += count
        elif status in ("missing", "no_receipt"):
            missing += count
        else:
            pending += count
    return {
        "received": received,
        "pending": pending,
        "missing": missing,
        "total": received + pending + missing,
    }


async def _compute_fuel_liters(db: Any, year: int) -> dict[str, Any]:
    store = empty_fuel_liters_daily()
    start = datetime(year, 1, 1)
    end = datetime(year, 12, 31, 23, 59, 59)
    diesel_match = diesel_cash_verified_match(await _load_diesel_item_names(db))
    diesel_match["date"] = {"$gte": start, "$lte": end}
    feed_types = list(DIESEL_FEED_SOURCE_GROUP.keys())
    station_pipeline = [
        {
            "$match": {
                "feed_type": {"$in": feed_types},
                "transaction_date": {"$gte": start, "$lte": end},
            }
        },
        {
            "$group": {
                "_id": {
                    "feed": "$feed_type",
                    "y": {"$year": "$transaction_date"},
                    "m": {"$month": "$transaction_date"},
                    "d": {"$dayOfMonth": "$transaction_date"},
                },
                "liters": {"$sum": safe_double("ltrs")},
            }
        },
    ]
    cash_docs, station_docs = await asyncio.gather(
        db.transactions.find(
            diesel_match, projection={"date": 1, "description": 1}
        ).to_list(length=None),
        db.imported_feeds.aggregate(station_pipeline).to_list(length=None),
    )
    accumulate_diesel_cash_fuel_liters(cash_docs, store)
    for doc in station_docs:
        ident = doc.get("_id") or {}
        source = DIESEL_FEED_SOURCE_GROUP.get(str(ident.get("feed") or ""), "")
        try:
            day = date(int(ident["y"]), int(ident["m"]), int(ident["d"]))
        except (KeyError, TypeError, ValueError):
            continue
        add_fuel_liters_day(store, source, day, doc.get("liters"))
    return {"year": year, "series": serialize_fuel_liters_series(store)}


async def _compute_month_totals(db: Any, year: int) -> dict[str, Any]:
    master, feed_usd, feed_zmw = await asyncio.gather(
        _master_month_totals(db, year),
        _overview_usd_feed_month_totals(db, year),
        _overview_zmw_feed_month_totals(db, year),
    )
    months: dict[str, dict[str, Any]] = {}
    for month in range(1, 13):
        row = master.get(month, {"tzs": 0.0, "usd": 0.0, "zmw": 0.0, "count": 0})
        months[str(month)] = {
            "tzs": float(row.get("tzs") or 0.0),
            "usd": float(row.get("usd") or 0.0) + float(feed_usd.get(month) or 0.0),
            "zmw": float(row.get("zmw") or 0.0) + float(feed_zmw.get(month) or 0.0),
            "count": int(row.get("count") or 0),
        }
    return {
        "months": months,
        "feed_usd_ytd": float(sum(feed_usd.values())),
        "feed_zmw_ytd": float(sum(feed_zmw.values())),
    }


@overview.get("/kpis")
async def overview_kpis(
    request: Request,
    _manager: Manager,
    year: int | None = Query(None, ge=2000, le=2100),
) -> dict:
    _year = year if year is not None else date.today().year
    return await _compute_kpis(_database(request), _year)


@overview.get("/categories")
async def overview_categories(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
    top_n: int = Query(4, ge=1, le=20),
) -> dict:
    return {"items": await _compute_categories(_database(request), year, top_n)}


@overview.get("/receipts")
async def overview_receipts(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    return await _compute_receipts(_database(request), year)


@overview.get("/fuel-liters")
async def overview_fuel_liters(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    return await _compute_fuel_liters(_database(request), year)


@overview.get("/month-totals")
async def overview_month_totals(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    return await _compute_month_totals(_database(request), year)


@overview.get("")
async def overview_dashboard(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    db = _database(request)
    kpis, month_payload, categories, fuel_liters, recent, supplier_totals = (
        await asyncio.gather(
            _compute_kpis(db, year),
            _compute_month_totals(db, year),
            _compute_categories(db, year, 4),
            _compute_fuel_liters(db, year),
            db.transactions.find(build_verified_query(year=year))
            .sort(list(VERIFIED_SORT))
            .limit(8)
            .to_list(length=8),
            _supplier_payments_totals(db, year),
        )
    )
    kpis = dict(kpis)
    kpis["total_usd_ytd"] = (
        float(kpis.get("total_usd_ytd") or 0.0)
        + float(month_payload.get("feed_usd_ytd") or 0.0)
    )
    kpis["total_zmw_ytd"] = (
        float(kpis.get("total_zmw_ytd") or 0.0)
        + float(month_payload.get("feed_zmw_ytd") or 0.0)
    )
    kpis["supplier_tzs_ytd"] = float(supplier_totals.get("tzs") or 0.0)
    kpis["supplier_usd_ytd"] = float(supplier_totals.get("usd") or 0.0)
    return {
        "kpis": kpis,
        "month_totals": month_payload.get("months", {}),
        "categories": categories,
        "fuel_liters": fuel_liters,
        "recent": json_safe(recent),
    }
