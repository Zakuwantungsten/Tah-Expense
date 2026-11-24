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
from .accountant_analytics_ops import (
    SEPARATE_EXPENSE_FEEDS,
    feed_period_match,
    feed_sum_pipeline,
    master_currency_group,
    period_window,
)
from .accountant_overview_ops import (
    DIESEL_FEED_SOURCE_GROUP,
    DIESEL_STATION_FEED_TYPES,
    FUEL_LITERS_SERIES,
    accumulate_currency_amount,
    accumulate_diesel_cash_fuel_liters,
    add_currency_buckets,
    add_fuel_liters_day,
    build_fuel_spend_rows,
    build_source_pie_slices,
    build_source_rows,
    empty_currency_bucket,
    empty_fuel_liters_daily,
    safe_double,
    serialize_fuel_liters_series,
    subtract_currency_buckets,
    sum_amount_if_currency,
    year_window,
)
from .dependencies import require_roles
from .feed_ops import diesel_computed_amount, not_trashed_clause
from .fx_rates import get_rates_to_tzs
from .serialization import json_safe
from .mongo_async import aggregate_list

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
    result = await aggregate_list(db.transactions, 
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
    , 1)
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
    docs = await aggregate_list(db.transactions, 
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
    , 12)
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


def _empty_feed_month_currency() -> dict[str, dict[int, float]]:
    return {
        "tzs": {m: 0.0 for m in range(1, 13)},
        "usd": {m: 0.0 for m in range(1, 13)},
        "zmw": {m: 0.0 for m in range(1, 13)},
    }


def _currency_bucket_key(currency: str | None) -> str | None:
    cur = str(currency or "").strip().upper()
    if cur in {"TZS", "TSH", "TZ"}:
        return "tzs"
    if cur == "USD":
        return "usd"
    if cur in {"ZMW", "ZMB", "ZK"}:
        return "zmw"
    return None


def _feed_month_expr(spec: Any) -> dict[str, Any]:
    """Calendar month from BSON date, with legacy display-string fallback."""
    primary = f"${spec.date_field}"
    if not getattr(spec, "legacy_date_field", None):
        return {"$month": primary}
    return {
        "$month": {
            "$ifNull": [
                primary,
                {
                    "$dateFromString": {
                        "dateString": f"${spec.legacy_date_field}",
                        "format": "%d %b %Y",
                        "onError": None,
                        "onNull": None,
                    }
                },
            ]
        }
    }


async def _overview_separate_feed_month_totals(
    db: Any, year: int
) -> dict[str, dict[int, float]]:
    """Every SEPARATE EXPENSES tab → native currency month buckets (Sources parity)."""
    start, end = period_window(year, "year")
    out = _empty_feed_month_currency()

    async def _one(spec: Any) -> None:
        coll = getattr(db, spec.collection, None)
        if coll is None:
            return
        cur_key = _currency_bucket_key(spec.currency)
        if cur_key is None:
            return
        docs = await aggregate_list(
            coll,
            [
                {"$match": feed_period_match(spec, start, end)},
                {
                    "$group": {
                        "_id": _feed_month_expr(spec),
                        "total": {"$sum": spec.amount_expr},
                    }
                },
            ],
            length=None,
        )
        months = await _month_sum_map(docs)
        for month, total in months.items():
            out[cur_key][month] += abs(float(total or 0.0))

    await asyncio.gather(*[_one(spec) for spec in SEPARATE_EXPENSE_FEEDS])
    return out


async def _overview_diesel_station_month_totals(
    db: Any, year: int
) -> dict[str, dict[int, float]]:
    """Imported diesel stations only (Diesel Cash already lives in Master)."""
    start, end = year_window(year)
    out = _empty_feed_month_currency()
    docs = await aggregate_list(
        db.imported_feeds,
        [
            {
                "$match": {
                    "feed_type": {"$in": list(DIESEL_STATION_FEED_TYPES)},
                    "transaction_date": {"$gte": start, "$lt": end},
                    **not_trashed_clause(),
                }
            },
            {
                "$group": {
                    "_id": {
                        "month": {"$month": "$transaction_date"},
                        "currency": {
                            "$toUpper": {"$ifNull": ["$currency", ""]}
                        },
                    },
                    "total": {"$sum": diesel_computed_amount()},
                }
            },
        ],
        length=None,
    )
    for doc in docs:
        meta = doc.get("_id")
        if not isinstance(meta, dict):
            continue
        month = meta.get("month")
        if not isinstance(month, int) or not (1 <= month <= 12):
            continue
        cur_key = _currency_bucket_key(str(meta.get("currency") or ""))
        if cur_key is None:
            # Unknown currency codes fall through to TZS (same as accumulate_currency_amount).
            cur_key = "tzs"
        out[cur_key][month] += abs(float(doc.get("total") or 0.0))
    return out


async def _overview_non_master_month_totals(
    db: Any, year: int
) -> dict[str, dict[int, float]]:
    """Separate tabs + diesel stations for TZS / USD / ZMW cards and trend."""
    separate, stations = await asyncio.gather(
        _overview_separate_feed_month_totals(db, year),
        _overview_diesel_station_month_totals(db, year),
    )
    out = _empty_feed_month_currency()
    for cur in ("tzs", "usd", "zmw"):
        for month in range(1, 13):
            out[cur][month] = float(separate[cur][month]) + float(
                stations[cur][month]
            )
    return out


async def _compute_kpis(db: Any, year: int) -> dict[str, Any]:
    """Master-only KPI snapshot (counts + absolute Master YTD).

    Inclusive all-sources currency cards are filled in ``overview_dashboard``
    via ``_summary_all_sources_ytd`` (same math as Analytics → Data table).
    """
    today = date.today()
    month_start = datetime(today.year, today.month, 1)
    year_start = datetime(year, 1, 1)
    year_end = datetime(year, 12, 31, 23, 59, 59)

    pending_count, ytd_res, month_res = await asyncio.gather(
        db.transactions.count_documents(
            {"verified": False, "rejected": {"$ne": True}}
        ),
        aggregate_list(db.transactions, 
            [
                {
                    "$match": {
                        "verified": True,
                        "deletion_requested": {"$ne": True},
                        "trashed": {"$ne": True},
                        **master_period_clause(year_start, year_end),
                    }
                },
                {"$group": master_currency_group()},
            ]
        , 1),
        aggregate_list(db.transactions, 
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
        , 1),
    )
    ytd = ytd_res[0] if ytd_res else {
        "count": 0,
        "tzs": 0.0,
        "usd": 0.0,
        "zmw": 0.0,
    }
    month = month_res[0] if month_res else {"verified": 0, "total": 0}
    return {
        "pending_count": int(pending_count),
        "master_count": int(ytd.get("count") or 0),
        "verified_this_month": int(month.get("verified") or 0),
        "submitted_this_month": int(month.get("total") or 0),
        "total_tzs_ytd": float(ytd.get("tzs") or 0.0),
        "total_usd_ytd": float(ytd.get("usd") or 0.0),
        "total_zmw_ytd": float(ytd.get("zmw") or 0.0),
    }


async def _summary_all_sources_ytd(db: Any, year: int) -> dict[str, float]:
    """Native YTD across every monetary source — same as Analytics Data table."""
    # Lazy import: analytics_api does not import overview_api.
    from .accountant_analytics_api import _data_table_source_rows
    from .accountant_trends_ops import build_data_table_rows

    start, end = period_window(year, "year")
    table = build_data_table_rows(await _data_table_source_rows(db, start, end))
    totals = table["totals"]
    return {
        "tzs": float(totals.get("tzs") or 0.0),
        "usd": float(totals.get("usd") or 0.0),
        "zmw": float(totals.get("zmw") or 0.0),
    }


async def _compute_categories(db: Any, year: int, top_n: int) -> list[dict[str, Any]]:
    """Overview donut: Master + each separate tab + fuel stations (no Diesel Cash slice)."""
    start, end = period_window(year, "year")
    master, feeds, stations = await asyncio.gather(
        _master_abs_totals(db, start, end),
        _separate_feed_amount_rows(db, start, end),
        _diesel_station_buckets_by_source(db, start, end),
    )
    return build_source_pie_slices(
        master=master,
        separate_feeds=feeds,
        station_buckets=stations,
        top_n=top_n,
    )


async def _compute_receipts(db: Any, year: int) -> dict[str, Any]:
    year_start = datetime(year, 1, 1)
    year_end = datetime(year, 12, 31, 23, 59, 59)
    docs = await aggregate_list(db.transactions, 
        [
            {
                "$match": {
                    "verified": True,
                    "date": {"$gte": year_start, "$lte": year_end},
                }
            },
            {"$group": {"_id": "$receipt_status", "count": {"$sum": 1}}},
        ]
    , None)
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
        aggregate_list(db.imported_feeds, station_pipeline, length=None),
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


async def _non_master_ytd_currency(db: Any, year: int) -> dict[str, float]:
    """Year totals for separate tabs + diesel stations (same scope as Sources)."""
    start, end = period_window(year, "year")
    feeds, stations = await asyncio.gather(
        _separate_feed_amount_rows(db, start, end),
        _diesel_station_abs_totals(db, start, end),
    )
    out = empty_currency_bucket()
    for feed in feeds:
        accumulate_currency_amount(
            out,
            str(feed.get("currency") or ""),
            float(feed.get("amount") or 0.0),
        )
    return add_currency_buckets(out, stations)


async def _compute_month_totals(db: Any, year: int) -> dict[str, Any]:
    master, feeds, ytd = await asyncio.gather(
        _master_month_totals(db, year),
        _overview_non_master_month_totals(db, year),
        _non_master_ytd_currency(db, year),
    )
    feed_tzs = feeds["tzs"]
    feed_usd = feeds["usd"]
    feed_zmw = feeds["zmw"]
    months: dict[str, dict[str, Any]] = {}
    for month in range(1, 13):
        row = master.get(month, {"tzs": 0.0, "usd": 0.0, "zmw": 0.0, "count": 0})
        months[str(month)] = {
            "tzs": float(row.get("tzs") or 0.0) + float(feed_tzs.get(month) or 0.0),
            "usd": float(row.get("usd") or 0.0) + float(feed_usd.get(month) or 0.0),
            "zmw": float(row.get("zmw") or 0.0) + float(feed_zmw.get(month) or 0.0),
            "count": int(row.get("count") or 0),
        }
    return {
        "months": months,
        # Authoritative YTD (legacy dates + money strings) — matches Sources bars.
        "feed_tzs_ytd": float(ytd.get("tzs") or 0.0),
        "feed_usd_ytd": float(ytd.get("usd") or 0.0),
        "feed_zmw_ytd": float(ytd.get("zmw") or 0.0),
    }


def _row_currency_bucket(row: dict[str, Any] | None) -> dict[str, float]:
    if not row:
        return empty_currency_bucket()
    return {
        "tzs": float(row.get("tzs") or 0.0),
        "usd": float(row.get("usd") or 0.0),
        "zmw": float(row.get("zmw") or 0.0),
    }


async def _master_abs_totals(db: Any, start: datetime, end: datetime) -> dict[str, float]:
    docs = await aggregate_list(
        db.transactions,
        [
            {
                "$match": {
                    "verified": True,
                    "deletion_requested": {"$ne": True},
                    "trashed": {"$ne": True},
                    **master_period_clause(start, end),
                }
            },
            {"$group": master_currency_group()},
        ],
        1,
    )
    return _row_currency_bucket(docs[0] if docs else None)


async def _diesel_cash_abs_totals(
    db: Any,
    start: datetime,
    end: datetime,
) -> dict[str, float]:
    match = diesel_cash_verified_match(await _load_diesel_item_names(db))
    match["date"] = {"$gte": start, "$lte": end}
    docs = await aggregate_list(
        db.transactions,
        [{"$match": match}, {"$group": master_currency_group()}],
        1,
    )
    return _row_currency_bucket(docs[0] if docs else None)


async def _diesel_station_abs_totals(
    db: Any,
    start: datetime,
    end: datetime,
) -> dict[str, float]:
    by_source = await _diesel_station_buckets_by_source(db, start, end)
    out = empty_currency_bucket()
    for bucket in by_source.values():
        out = add_currency_buckets(out, bucket)
    return out


async def _diesel_station_buckets_by_source(
    db: Any,
    start: datetime,
    end: datetime,
) -> dict[str, dict[str, float]]:
    """Per overview fuel key (infinity / lake_zambia / …) currency buckets."""
    docs = await aggregate_list(
        db.imported_feeds,
        [
            {
                "$match": {
                    "feed_type": {"$in": list(DIESEL_STATION_FEED_TYPES)},
                    "transaction_date": {"$gte": start, "$lte": end},
                    **not_trashed_clause(),
                }
            },
            {
                "$group": {
                    "_id": {
                        "feed": "$feed_type",
                        "currency": {
                            "$toUpper": {"$ifNull": ["$currency", ""]}
                        },
                    },
                    "total": {"$sum": diesel_computed_amount()},
                }
            },
        ],
        length=None,
    )
    out: dict[str, dict[str, float]] = {
        key: empty_currency_bucket() for key, _ in FUEL_LITERS_SERIES if key != "diesel_cash"
    }
    for doc in docs:
        meta = doc.get("_id")
        if isinstance(meta, dict):
            feed = str(meta.get("feed") or "")
            currency = str(meta.get("currency") or "")
        else:
            # Some test doubles flatten compound _id; skip malformed rows.
            continue
        source = DIESEL_FEED_SOURCE_GROUP.get(feed)
        if not source or source not in out:
            continue
        accumulate_currency_amount(
            out[source],
            currency,
            float(doc.get("total") or 0.0),
        )
    return out


async def _compute_fuel_spend(db: Any, year: int) -> dict[str, Any]:
    """Diesel Cash + each station as TZS-normalized horizontal bars."""
    start, end = period_window(year, "year")
    diesel_cash, stations, fx = await asyncio.gather(
        _diesel_cash_abs_totals(db, start, end),
        _diesel_station_buckets_by_source(db, start, end),
        asyncio.to_thread(get_rates_to_tzs),
    )
    buckets: dict[str, dict[str, float]] = {
        key: empty_currency_bucket() for key, _ in FUEL_LITERS_SERIES
    }
    buckets["diesel_cash"] = diesel_cash
    for key, bucket in stations.items():
        buckets[key] = bucket
    rates = dict(fx.get("rates") or {})
    rows = build_fuel_spend_rows(buckets, rates)
    return {
        "year": year,
        "display_currency": "TZS",
        "rate_source": str(fx.get("source") or "live"),
        "rates_to_tzs": {
            "USD": float(rates.get("USD") or 0.0),
            "ZMW": float(rates.get("ZMW") or 0.0),
            "TZS": 1.0,
        },
        "items": rows,
    }


async def _separate_feed_amount_rows(
    db: Any,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    async def _one(spec: Any) -> dict[str, Any]:
        coll = getattr(db, spec.collection, None)
        if coll is None:
            return {
                "key": spec.key,
                "label": spec.label,
                "currency": spec.currency,
                "amount": 0.0,
            }
        docs = await aggregate_list(coll, feed_sum_pipeline(spec, start, end), 1)
        total = float(docs[0].get("total") or 0.0) if docs else 0.0
        return {
            "key": spec.key,
            "label": spec.label,
            "currency": spec.currency,
            "amount": abs(total),
        }

    return list(await asyncio.gather(*[_one(spec) for spec in SEPARATE_EXPENSE_FEEDS]))


async def _compute_sources(db: Any, year: int) -> dict[str, Any]:
    start, end = period_window(year, "year")
    master, diesel_cash, diesel_stations, feeds, fx = await asyncio.gather(
        _master_abs_totals(db, start, end),
        _diesel_cash_abs_totals(db, start, end),
        _diesel_station_abs_totals(db, start, end),
        _separate_feed_amount_rows(db, start, end),
        asyncio.to_thread(get_rates_to_tzs),
    )
    core = {
        "master": subtract_currency_buckets(master, diesel_cash),
        "fuel": add_currency_buckets(diesel_cash, diesel_stations),
    }
    rates = dict(fx.get("rates") or {})
    rows = build_source_rows(
        core,
        rates,
        separate_feeds=feeds,
        rate_source=str(fx.get("source") or "live"),
    )
    return {
        "year": year,
        "display_currency": "TZS",
        "rate_source": str(fx.get("source") or "live"),
        "rates_to_tzs": {
            "USD": float(rates.get("USD") or 0.0),
            "ZMW": float(rates.get("ZMW") or 0.0),
            "TZS": 1.0,
        },
        "items": rows,
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
    top_n: int = Query(8, ge=1, le=20),
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


@overview.get("/fuel-spend")
async def overview_fuel_spend(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    return await _compute_fuel_spend(_database(request), year)


@overview.get("/month-totals")
async def overview_month_totals(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    return await _compute_month_totals(_database(request), year)


@overview.get("/sources")
async def overview_sources(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    return await _compute_sources(_database(request), year)


@overview.get("")
async def overview_dashboard(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
) -> dict:
    db = _database(request)
    (
        kpis,
        month_payload,
        categories,
        fuel_liters,
        fuel_spend,
        recent,
        supplier_totals,
        sources,
        summary_ytd,
    ) = await asyncio.gather(
        _compute_kpis(db, year),
        _compute_month_totals(db, year),
        _compute_categories(db, year, 8),
        _compute_fuel_liters(db, year),
        _compute_fuel_spend(db, year),
        db.transactions.find(build_verified_query(year=year))
        .sort(list(VERIFIED_SORT))
        .limit(8)
        .to_list(length=8),
        _supplier_payments_totals(db, year),
        _compute_sources(db, year),
        _summary_all_sources_ytd(db, year),
    )
    kpis = dict(kpis)
    # Currency cards = Analytics Data table grand totals (all monetary sources).
    kpis["total_tzs_ytd"] = float(summary_ytd.get("tzs") or 0.0)
    kpis["total_usd_ytd"] = float(summary_ytd.get("usd") or 0.0)
    kpis["total_zmw_ytd"] = float(summary_ytd.get("zmw") or 0.0)
    kpis["supplier_tzs_ytd"] = float(supplier_totals.get("tzs") or 0.0)
    kpis["supplier_usd_ytd"] = float(supplier_totals.get("usd") or 0.0)
    return {
        "kpis": kpis,
        "month_totals": month_payload.get("months", {}),
        "categories": categories,
        "fuel_liters": fuel_liters,
        "fuel_spend": fuel_spend,
        "sources": sources,
        "recent": json_safe(recent),
    }
