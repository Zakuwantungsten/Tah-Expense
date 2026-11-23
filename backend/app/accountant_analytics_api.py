"""Accountant Analytics dashboard routes."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .accountant_analytics_ops import (
    SEPARATE_EXPENSE_FEEDS,
    averages_from_totals,
    collapse_top_n,
    empty_currency_totals,
    feed_sum_pipeline,
    master_currency_group,
    normalize_period,
    pareto_for_currency,
    period_day_count,
    period_label,
    period_window,
)
from .accountant_master_ops import master_period_clause
from .accountant_overview_ops import safe_double, sum_amount_if_currency
from .accountant_diesel_ops import (
    DIESEL_CASH_ITEMS_SETTING,
    diesel_cash_verified_match,
    normalize_diesel_cash_item_names,
)
from .accountant_trends_ops import (
    DIESEL_STATION_BY_KEY,
    DIESEL_STATION_TRENDS,
    ENTITY_FAMILIES,
    ENTITY_FAMILY_KEYS,
    PHASE3_DIESEL_KEYS,
    PHASE4_DIESEL_CASH_KEY,
    PHASE4_DIESEL_CASH_KEYS,
    PHASE5_SEPARATE_DIMS,
    PHASE5_SEPARATE_KEYS,
    PHASE6_USD_LEDGER_DIMS,
    PHASE6_USD_LEDGER_KEYS,
    PHASE7_INSURANCE_DIMS,
    PHASE7_INSURANCE_KEYS,
    PHASE8_OUTLIER_DIMS,
    PHASE8_OUTLIER_KEYS,
    SEPARATE_FEED_BY_KEY,
    TREND_SOURCE_PHASE,
    category_name_clause,
    category_names_clause,
    currency_bucket_key,
    empty_receipt_breakdown,
    ENTITY_COMPARE_COLORS,
    ENTITY_COMPARE_MAX,
    TREND_COMPARE_COLORS,
    TREND_COMPARE_MAX,
    build_data_table_month_trend,
    build_data_table_rows,
    build_meetings_month_trend,
    build_meetings_people_rows,
    build_truck_fleet_table,
    meetings_person_label,
    normalize_meetings_dimension,
    receipt_needed_and_received,
    entity_cache_get,
    entity_cache_key,
    entity_cache_set,
    entity_set_label,
    _truck_norm_key,
    item_key_from_name,
    liters_by_month_from_docs,
    liters_from_description_docs,
    merge_liters_into_month_series,
    mom_deltas,
    month_deltas,
    money_triple,
    native_money,
    parse_item_source_key,
    payee_clause,
    payees_clause,
    truck_number_clause,
    polish_dimension_rows,
    static_trend_source_catalog,
)
from .dependencies import require_roles
from .feed_ops import (
    diesel_computed_amount,
    not_trashed_clause,
    parking_congo_deposit_clause,
)
from .mongo_async import aggregate_list

analytics = APIRouter(prefix="/analytics", tags=["accountant"])

Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _parse_period(
    year: int,
    period: str | None,
    month: int | None,
) -> tuple[str, int | None]:
    try:
        return normalize_period(year, period, month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _currency_group_fields() -> dict[str, Any]:
    return {
        "tzs": sum_amount_if_currency("TZS", "TSH", "TZ", absolute=True),
        "usd": sum_amount_if_currency("USD", absolute=True),
        "zmw": sum_amount_if_currency("ZMW", "ZMB", "ZK", absolute=True),
        "count": {"$sum": 1},
    }


def _master_base_match(
    start,
    end,
    *,
    category_name: str | None = None,
    category_names: list[str] | None = None,
    payee: str | None = None,
    payees: list[str] | None = None,
    truck: str | None = None,
) -> dict[str, Any]:
    match: dict[str, Any] = {
        "verified": True,
        "deletion_requested": {"$ne": True},
        "trashed": {"$ne": True},
        **master_period_clause(start, end),
    }
    if category_names:
        cat = category_names_clause(category_names)
    else:
        cat = category_name_clause(category_name or "")
    if cat:
        match.update(cat)
    if payees:
        pay = payees_clause(payees)
    else:
        pay = payee_clause(payee or "")
    if pay:
        match.update(pay)
    truck_match = truck_number_clause(truck or "")
    if truck_match:
        match.update(truck_match)
    return match


async def _master_totals(
    db: Any,
    start,
    end,
    *,
    category_name: str | None = None,
    category_names: list[str] | None = None,
    payee: str | None = None,
    payees: list[str] | None = None,
    truck: str | None = None,
) -> dict[str, Any]:
    docs = await aggregate_list(
        db.transactions,
        [
            {
                "$match": _master_base_match(
                    start,
                    end,
                    category_name=category_name,
                    category_names=category_names,
                    payee=payee,
                    payees=payees,
                    truck=truck,
                )
            },
            {"$group": master_currency_group()},
        ],
        1,
    )
    if not docs:
        return {**empty_currency_totals(), "count": 0}
    row = docs[0]
    return {
        "tzs": float(row.get("tzs") or 0.0),
        "usd": float(row.get("usd") or 0.0),
        "zmw": float(row.get("zmw") or 0.0),
        "count": int(row.get("count") or 0),
    }


async def _master_month_series_range(
    db: Any,
    start,
    end,
    *,
    category_name: str | None = None,
    category_names: list[str] | None = None,
    payee: str | None = None,
    payees: list[str] | None = None,
    truck: str | None = None,
) -> list[dict[str, Any]]:
    """Month buckets for Master spend inside ``start``–``end`` (calendar months 1–12)."""
    docs = await aggregate_list(
        db.transactions,
        [
            {
                "$match": _master_base_match(
                    start,
                    end,
                    category_name=category_name,
                    category_names=category_names,
                    payee=payee,
                    payees=payees,
                    truck=truck,
                )
            },
            {
                "$group": {
                    "_id": {"$month": "$date"},
                    **_currency_group_fields(),
                }
            },
        ],
        12,
    )
    by_month = {
        int(doc["_id"]): {
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
        if doc.get("_id") is not None
    }
    return [
        {
            "month": m,
            **by_month.get(m, {**empty_currency_totals(), "count": 0}),
        }
        for m in range(1, 13)
    ]


async def _master_month_series(
    db: Any,
    year: int,
    *,
    category_name: str | None = None,
    category_names: list[str] | None = None,
    payee: str | None = None,
    payees: list[str] | None = None,
    truck: str | None = None,
) -> list[dict[str, Any]]:
    start, end = period_window(year, "year")
    return await _master_month_series_range(
        db,
        start,
        end,
        category_name=category_name,
        category_names=category_names,
        payee=payee,
        payees=payees,
        truck=truck,
    )


async def _master_category_rows(
    db: Any,
    start,
    end,
    *,
    category_name: str | None = None,
    category_names: list[str] | None = None,
    payee: str | None = None,
    payees: list[str] | None = None,
    truck: str | None = None,
) -> list[dict[str, Any]]:
    docs = await aggregate_list(
        db.transactions,
        [
            {
                "$match": _master_base_match(
                    start,
                    end,
                    category_name=category_name,
                    category_names=category_names,
                    payee=payee,
                    payees=payees,
                    truck=truck,
                )
            },
            {
                "$group": {
                    "_id": {"$ifNull": ["$category_name", "Uncategorised"]},
                    **_currency_group_fields(),
                }
            },
        ],
        None,
    )
    return [
        {
            "name": doc.get("_id") or "Uncategorised",
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
            "source": "master",
        }
        for doc in docs
    ]


async def _master_payee_rows(
    db: Any,
    start,
    end,
    *,
    payees: list[str] | None = None,
    payee: str | None = None,
    category_names: list[str] | None = None,
    truck: str | None = None,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Break spend down by payee (Entities multi-payee mix)."""
    docs = await aggregate_list(
        db.transactions,
        [
            {
                "$match": _master_base_match(
                    start,
                    end,
                    category_names=category_names,
                    payee=payee,
                    payees=payees,
                    truck=truck,
                )
            },
            {
                "$group": {
                    "_id": {
                        "$let": {
                            "vars": {
                                "p": {
                                    "$trim": {
                                        "input": {"$ifNull": ["$payee", ""]}
                                    }
                                }
                            },
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$p", ""]},
                                    "No payee",
                                    "$$p",
                                ]
                            },
                        }
                    },
                    **_currency_group_fields(),
                }
            },
        ],
        None,
    )
    rows = [
        {
            "name": doc.get("_id") or "No payee",
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
            "source": "master",
        }
        for doc in docs
    ]
    return collapse_top_n(rows, top_n=top_n)


async def _master_description_rows(
    db: Any,
    start,
    end,
    *,
    category_name: str | None = None,
    category_names: list[str] | None = None,
    payee: str | None = None,
    payees: list[str] | None = None,
    truck: str | None = None,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Break spend down by description (one or many ITEMS / payee filters)."""
    docs = await aggregate_list(
        db.transactions,
        [
            {
                "$match": _master_base_match(
                    start,
                    end,
                    category_name=category_name,
                    category_names=category_names,
                    payee=payee,
                    payees=payees,
                    truck=truck,
                )
            },
            {
                "$group": {
                    "_id": {
                        "$let": {
                            "vars": {
                                "d": {
                                    "$trim": {
                                        "input": {"$ifNull": ["$description", ""]}
                                    }
                                }
                            },
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$d", ""]},
                                    "No description",
                                    "$$d",
                                ]
                            },
                        }
                    },
                    **_currency_group_fields(),
                }
            },
        ],
        None,
    )
    rows = [
        {
            "name": doc.get("_id") or "No description",
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
            "source": "item",
        }
        for doc in docs
    ]
    return collapse_top_n(rows, top_n=top_n)


async def _supplier_rows(
    db: Any,
    start,
    end,
) -> list[dict[str, Any]]:
    try:
        cats = await db.categories.find(
            {"is_supplier": True, "active": {"$ne": False}},
            {"name": 1},
        ).to_list(length=None)
    except Exception:
        cats = []
    supplier_names = [
        str(d.get("name") or "").strip().lower()
        for d in cats
        if str(d.get("name") or "").strip()
    ]
    if not supplier_names:
        return []
    docs = await aggregate_list(
        db.transactions,
        [
            {
                "$match": {
                    "verified": True,
                    "deletion_requested": {"$ne": True},
                    "trashed": {"$ne": True},
                    "category_name": {"$type": "string", "$ne": ""},
                    **master_period_clause(start, end),
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
                    "_id": {"$ifNull": ["$category_name", "Supplier"]},
                    **_currency_group_fields(),
                }
            },
        ],
        None,
    )
    return [
        {
            "name": doc.get("_id") or "Supplier",
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
    ]


async def _feed_stats(db: Any, spec, start, end) -> tuple[float, int]:
    coll = getattr(db, spec.collection)
    docs = await aggregate_list(
        coll, feed_sum_pipeline(spec, start, end), 1
    )
    if not docs:
        return 0.0, 0
    return float(docs[0].get("total") or 0.0), int(docs[0].get("count") or 0)


async def _separate_feed_totals(
    db: Any,
    start,
    end,
) -> list[dict[str, Any]]:
    stats = await asyncio.gather(
        *[_feed_stats(db, spec, start, end) for spec in SEPARATE_EXPENSE_FEEDS]
    )
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "currency": spec.currency,
            "amount": float(total),
            "count": int(count),
        }
        for spec, (total, count) in zip(SEPARATE_EXPENSE_FEEDS, stats)
    ]


def _feeds_as_category_rows(feeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feed in feeds:
        cur = str(feed.get("currency") or "").upper()
        amt = float(feed.get("amount") or 0.0)
        row = {
            "name": str(feed.get("label") or feed.get("key") or "Feed"),
            "tzs": 0.0,
            "usd": 0.0,
            "zmw": 0.0,
            "count": int(feed.get("count") or 0),
            "source": "separate",
        }
        if cur == "TZS":
            row["tzs"] = amt
        elif cur == "USD":
            row["usd"] = amt
        elif cur == "ZMW":
            row["zmw"] = amt
        rows.append(row)
    return rows


def _merge_currency(
    master: dict[str, float],
    feeds: list[dict[str, Any]],
) -> dict[str, float]:
    out = {
        "tzs": float(master.get("tzs") or 0.0),
        "usd": float(master.get("usd") or 0.0),
        "zmw": float(master.get("zmw") or 0.0),
    }
    for row in feeds:
        cur = str(row.get("currency") or "").upper()
        amt = float(row.get("amount") or 0.0)
        if cur == "TZS":
            out["tzs"] += amt
        elif cur == "USD":
            out["usd"] += amt
        elif cur == "ZMW":
            out["zmw"] += amt
    return out


def _source_mix(
    master: dict[str, Any],
    feeds: list[dict[str, Any]],
) -> dict[str, Any]:
    separate = empty_currency_totals()
    separate_count = 0
    for feed in feeds:
        cur = str(feed.get("currency") or "").upper()
        amt = float(feed.get("amount") or 0.0)
        separate_count += int(feed.get("count") or 0)
        if cur == "TZS":
            separate["tzs"] += amt
        elif cur == "USD":
            separate["usd"] += amt
        elif cur == "ZMW":
            separate["zmw"] += amt
    return {
        "master": {
            "tzs": float(master.get("tzs") or 0.0),
            "usd": float(master.get("usd") or 0.0),
            "zmw": float(master.get("zmw") or 0.0),
            "count": int(master.get("count") or 0),
        },
        "separate": {
            **separate,
            "count": separate_count,
        },
    }


def _pareto_bundle(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tzs": pareto_for_currency(rows, "tzs"),
        "usd": pareto_for_currency(rows, "usd"),
        "zmw": pareto_for_currency(rows, "zmw"),
    }


async def _compute_dashboard(
    db: Any,
    year: int,
    kind: str,
    month: int | None,
) -> dict[str, Any]:
    start, end = period_window(year, kind, month)  # type: ignore[arg-type]
    days = period_day_count(year, kind, month)  # type: ignore[arg-type]
    label = period_label(year, kind, month)  # type: ignore[arg-type]

    (
        master,
        master_cats,
        suppliers_raw,
        feeds,
        month_series,
        prior_month_series,
    ) = await asyncio.gather(
        _master_totals(db, start, end),
        _master_category_rows(db, start, end),
        _supplier_rows(db, start, end),
        _separate_feed_totals(db, start, end),
        _master_month_series(db, year),
        _master_month_series(db, year - 1),
    )

    all_category_rows = master_cats + _feeds_as_category_rows(feeds)
    categories = collapse_top_n(all_category_rows, top_n=10)
    suppliers = collapse_top_n(suppliers_raw, top_n=12)
    combined = _merge_currency(master, feeds)
    avgs = averages_from_totals(combined, days)

    return {
        "year": year,
        "period": kind,
        "month": month,
        "period_label": label,
        "days": days,
        "kpis": {
            "avg_per_day": avgs,
            "totals": combined,
            "master_totals": {
                "tzs": float(master.get("tzs") or 0.0),
                "usd": float(master.get("usd") or 0.0),
                "zmw": float(master.get("zmw") or 0.0),
            },
            "master_count": int(master.get("count") or 0),
        },
        "categories": categories,
        "suppliers": suppliers,
        "pareto": _pareto_bundle(all_category_rows),
        "source_mix": _source_mix(master, feeds),
        "month_trend": month_series,
        "prior_month_trend": prior_month_series,
        "separate_feeds": feeds,
    }


@analytics.get("")
async def analytics_dashboard(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
    period: str = Query("year", pattern="^(year|month)$"),
    month: int | None = Query(None, ge=1, le=12),
) -> dict:
    """Analytics KPIs + charts for a calendar year or a single month."""
    kind, resolved_month = _parse_period(year, period, month)
    return await _compute_dashboard(
        _database(request), year, kind, resolved_month
    )


# ── Trends deep-dive (Phase 0 shell + Phase 1 Master) ─────────────────────────


async def _sidebar_item_sources(db: Any) -> list[dict[str, Any]]:
    try:
        docs = await db.categories.find(
            {
                "is_supplier": {"$ne": True},
                "show_in_sidebar": True,
                "active": {"$ne": False},
            },
            {"name": 1, "sidebar_label": 1},
        ).to_list(length=None)
    except Exception:
        docs = []
    out: list[dict[str, Any]] = []
    for doc in docs:
        name = str(doc.get("name") or "").strip()
        if not name:
            continue
        key = item_key_from_name(name)
        if not key:
            continue
        label = str(doc.get("sidebar_label") or name).strip() or name
        out.append(
            {
                "key": f"item:{key}",
                "label": label,
                "phase": 2,
                "ready": True,
                "kind": "item",
                "category_name": name,
            }
        )
    out.sort(key=lambda r: str(r.get("label") or "").lower())
    return out


async def _master_truck_rows(
    db: Any,
    start,
    end,
    *,
    top_n: int = 12,
    category_name: str | None = None,
    category_names: list[str] | None = None,
    payee: str | None = None,
    payees: list[str] | None = None,
    truck: str | None = None,
) -> list[dict[str, Any]]:
    docs = await aggregate_list(
        db.transactions,
        [
            {
                "$match": _master_base_match(
                    start,
                    end,
                    category_name=category_name,
                    category_names=category_names,
                    payee=payee,
                    payees=payees,
                    truck=truck,
                )
            },
            {
                "$group": {
                    "_id": {"$ifNull": ["$truck_number", "—"]},
                    **_currency_group_fields(),
                }
            },
        ],
        None,
    )
    rows = [
        {
            "name": (str(doc.get("_id") or "").strip() or "—"),
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
    ]
    return collapse_top_n(rows, top_n=top_n)


async def _master_receipts(
    db: Any,
    start,
    end,
    *,
    category_name: str | None = None,
    category_names: list[str] | None = None,
    payee: str | None = None,
    payees: list[str] | None = None,
    truck: str | None = None,
) -> dict[str, int]:
    docs = await aggregate_list(
        db.transactions,
        [
            {
                "$match": _master_base_match(
                    start,
                    end,
                    category_name=category_name,
                    category_names=category_names,
                    payee=payee,
                    payees=payees,
                    truck=truck,
                )
            },
            {"$group": {"_id": "$receipt_status", "count": {"$sum": 1}}},
        ],
        None,
    )
    out = empty_receipt_breakdown()
    for doc in docs:
        status = str(doc.get("_id") or "pending").lower()
        count = int(doc.get("count") or 0)
        if status == "received":
            out["received"] += count
        elif status in ("missing", "no_receipt"):
            out["missing"] += count
        else:
            out["pending"] += count
    out["total"] = out["received"] + out["pending"] + out["missing"]
    return out


def _delta_bundle(
    current: list[dict[str, Any]],
    prior: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "yoy": {
            "tzs": month_deltas(current, prior, currency_key="tzs"),
            "usd": month_deltas(current, prior, currency_key="usd"),
            "zmw": month_deltas(current, prior, currency_key="zmw"),
        },
        "mom": {
            "tzs": mom_deltas(current, currency_key="tzs"),
            "usd": mom_deltas(current, currency_key="usd"),
            "zmw": mom_deltas(current, currency_key="zmw"),
        },
    }


async def _resolve_item_category(
    db: Any,
    source_key: str,
) -> tuple[str, str] | None:
    """Map ``item:{slug}`` → (source_key, category display name)."""
    slug = parse_item_source_key(source_key)
    if not slug:
        return None
    items = await _sidebar_item_sources(db)
    for item in items:
        if str(item.get("key") or "") == f"item:{slug}":
            name = str(item.get("category_name") or item.get("label") or "").strip()
            if name:
                return str(item["key"]), name
    # Fallback: scan categories collection in case sidebar filter changed.
    try:
        docs = await db.categories.find(
            {"is_supplier": {"$ne": True}, "active": {"$ne": False}},
            {"name": 1, "sidebar_label": 1},
        ).to_list(length=None)
    except Exception:
        docs = []
    for doc in docs:
        name = str(doc.get("name") or "").strip()
        if not name:
            continue
        if item_key_from_name(name) == slug:
            return f"item:{slug}", name
    return None


async def _compute_master_trends(
    db: Any,
    year: int,
    kind: str,
    month: int | None,
    *,
    source_key: str = "master",
    label: str = "Master Expenses",
    category_name: str | None = None,
    category_names: list[str] | None = None,
    payee: str | None = None,
    payees: list[str] | None = None,
    truck: str | None = None,
    entities: list[dict[str, Any]] | None = None,
    kind_name: str | None = None,
    phase: int | None = None,
    mix_label: str | None = None,
    trucks_label: str | None = None,
) -> dict[str, Any]:
    start, end = period_window(year, kind, month)  # type: ignore[arg-type]
    days = period_day_count(year, kind, month)  # type: ignore[arg-type]
    # Prior window: prior calendar year same period shape
    prior_year = year - 1
    prior_start, prior_end = period_window(prior_year, kind, month)  # type: ignore[arg-type]
    names = [n for n in (category_names or []) if str(n or "").strip()]
    if category_name and category_name.strip() and category_name.strip() not in {
        n.strip() for n in names
    }:
        names = [category_name.strip(), *names]
    payee_names = [n for n in (payees or []) if str(n or "").strip()]
    if (payee or "").strip() and (payee or "").strip() not in {
        n.strip() for n in payee_names
    }:
        payee_names = [(payee or "").strip(), *payee_names]
    truck_name = (truck or "").strip()
    is_truck = bool(truck_name) and truck_name not in {"—", "-"}
    is_filtered = bool(names) or bool(payee_names) or is_truck
    multi_items = len(names) > 1
    multi_payees = len(payee_names) > 1
    resolved_trucks_label = trucks_label or "Spend by Truck"
    secondary_coro = None

    if multi_items:
        mix_coro = _master_category_rows(
            db,
            start,
            end,
            category_names=names,
            payees=payee_names or None,
            truck=truck_name or None,
        )
        resolved_mix = mix_label or "Spend by Item"
        resolved_phase = phase if phase is not None else 2
        resolved_kind = kind_name or "item_set"
    elif multi_payees:
        mix_coro = _master_payee_rows(
            db,
            start,
            end,
            payees=payee_names,
            category_names=names or None,
            truck=truck_name or None,
            top_n=10,
        )
        resolved_mix = mix_label or "Spend by Payee"
        resolved_phase = phase if phase is not None else 2
        resolved_kind = kind_name or "payee_set"
    elif is_truck and not names and not payee_names:
        # Single-truck deep-dive: category mix + top descriptions.
        mix_coro = _master_category_rows(
            db,
            start,
            end,
            truck=truck_name,
        )
        secondary_coro = _master_description_rows(
            db,
            start,
            end,
            truck=truck_name,
            top_n=12,
        )
        resolved_mix = mix_label or "Spend by Category"
        resolved_trucks_label = trucks_label or "Top descriptions"
        resolved_phase = phase if phase is not None else 2
        resolved_kind = kind_name or "truck"
    elif is_filtered:
        mix_coro = _master_description_rows(
            db,
            start,
            end,
            category_names=names or None,
            category_name=None if names else category_name,
            payees=payee_names or None,
            truck=truck_name or None,
            top_n=10,
        )
        resolved_mix = mix_label or "Spend by Description"
        resolved_phase = phase if phase is not None else 2
        resolved_kind = kind_name or ("payee" if payee_names else "item")
    else:
        mix_coro = _master_category_rows(db, start, end)
        resolved_mix = mix_label or "Spend by Category"
        resolved_phase = phase if phase is not None else 1
        resolved_kind = kind_name or "master"

    filter_kwargs = {
        "category_name": None if names else category_name,
        "category_names": names or None,
        "payee": None,
        "payees": payee_names or None,
        "truck": truck_name or None,
    }
    secondary = (
        secondary_coro
        if secondary_coro is not None
        else _master_truck_rows(db, start, end, top_n=12, **filter_kwargs)
    )
    (
        totals,
        prior_totals,
        mix_raw,
        trucks,
        receipts,
        month_series,
        prior_month_series,
    ) = await asyncio.gather(
        _master_totals(db, start, end, **filter_kwargs),
        _master_totals(db, prior_start, prior_end, **filter_kwargs),
        mix_coro,
        secondary,
        _master_receipts(db, start, end, **filter_kwargs),
        _master_month_series(db, year, **filter_kwargs),
        _master_month_series(db, prior_year, **filter_kwargs),
    )
    categories = (
        mix_raw
        if (is_filtered and not multi_items and not multi_payees and not is_truck)
        else collapse_top_n(mix_raw, top_n=10)
    )
    money = money_triple(totals)
    prior_money = money_triple(prior_totals)
    avgs = averages_from_totals(money, days)
    count = int(totals.get("count") or 0)
    return {
        "source": source_key,
        "label": label,
        "kind": resolved_kind,
        "phase": resolved_phase,
        "ready": True,
        "sparse": count == 0,
        "category_name": category_name or (names[0] if len(names) == 1 else None),
        "category_names": names,
        "payee": payee_names[0] if len(payee_names) == 1 else None,
        "payees": payee_names,
        "truck": truck_name or None,
        "entities": list(entities or []),
        "mix_label": resolved_mix,
        "trucks_label": resolved_trucks_label,
        "year": year,
        "period": kind,
        "month": month,
        "period_label": period_label(year, kind, month),  # type: ignore[arg-type]
        "days": days,
        "kpis": {
            "totals": money,
            "prior_totals": prior_money,
            "avg_per_day": avgs,
            "count": count,
            "prior_count": int(prior_totals.get("count") or 0),
        },
        "categories": categories,
        "trucks": trucks,
        "receipts": receipts,
        "month_trend": month_series,
        "prior_month_trend": prior_month_series,
        "deltas": _delta_bundle(month_series, prior_month_series),
    }


def _diesel_match(feed_type: str, start, end) -> dict[str, Any]:
    return {
        "feed_type": feed_type,
        "transaction_date": {"$gte": start, "$lte": end},
        **not_trashed_clause(),
    }


def _diesel_currency_sum_fields(native: str) -> dict[str, Any]:
    """Map diesel line totals into tzs/usd/zmw buckets."""
    amount = diesel_computed_amount()
    if native == "USD":
        # Lake Zambia may store USD or ZMW on the row.
        return {
            "tzs": {"$sum": 0},
            "usd": {
                "$sum": {
                    "$cond": [
                        {
                            "$in": [
                                {"$toUpper": {"$ifNull": ["$currency", "USD"]}},
                                ["USD", ""],
                            ]
                        },
                        amount,
                        0,
                    ]
                }
            },
            "zmw": {
                "$sum": {
                    "$cond": [
                        {
                            "$in": [
                                {"$toUpper": {"$ifNull": ["$currency", ""]}},
                                ["ZMW", "ZMB", "ZK"],
                            ]
                        },
                        amount,
                        0,
                    ]
                }
            },
        }
    key = "tzs" if native == "TZS" else ("usd" if native == "USD" else "zmw")
    fields = {"tzs": {"$sum": 0}, "usd": {"$sum": 0}, "zmw": {"$sum": 0}}
    fields[key] = {"$sum": amount}
    return fields


async def _diesel_period_totals(
    db: Any,
    feed_type: str,
    native: str,
    start,
    end,
) -> dict[str, Any]:
    docs = await aggregate_list(
        db.imported_feeds,
        [
            {"$match": _diesel_match(feed_type, start, end)},
            {
                "$group": {
                    "_id": None,
                    **_diesel_currency_sum_fields(native),
                    "liters": {"$sum": safe_double("ltrs")},
                    "count": {"$sum": 1},
                }
            },
        ],
        1,
    )
    if not docs:
        return {**empty_currency_totals(), "liters": 0.0, "count": 0}
    row = docs[0]
    return {
        "tzs": float(row.get("tzs") or 0.0),
        "usd": float(row.get("usd") or 0.0),
        "zmw": float(row.get("zmw") or 0.0),
        "liters": float(row.get("liters") or 0.0),
        "count": int(row.get("count") or 0),
    }


async def _diesel_month_series_range(
    db: Any,
    feed_type: str,
    native: str,
    start,
    end,
) -> list[dict[str, Any]]:
    """Month buckets for one diesel station inside ``start``–``end``."""
    docs = await aggregate_list(
        db.imported_feeds,
        [
            {"$match": _diesel_match(feed_type, start, end)},
            {
                "$group": {
                    "_id": {"$month": "$transaction_date"},
                    **_diesel_currency_sum_fields(native),
                    "liters": {"$sum": safe_double("ltrs")},
                    "count": {"$sum": 1},
                }
            },
        ],
        12,
    )
    by_month = {
        int(doc["_id"]): {
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "liters": float(doc.get("liters") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
        if doc.get("_id") is not None
    }
    return [
        {
            "month": m,
            **by_month.get(
                m, {**empty_currency_totals(), "liters": 0.0, "count": 0}
            ),
        }
        for m in range(1, 13)
    ]


async def _diesel_month_series(
    db: Any,
    feed_type: str,
    native: str,
    year: int,
) -> list[dict[str, Any]]:
    start, end = period_window(year, "year")
    return await _diesel_month_series_range(db, feed_type, native, start, end)


async def _diesel_dimension_rows(
    db: Any,
    feed_type: str,
    native: str,
    start,
    end,
    *,
    field: str,
    top_n: int = 12,
    empty_label: str = "—",
) -> list[dict[str, Any]]:
    docs = await aggregate_list(
        db.imported_feeds,
        [
            {"$match": _diesel_match(feed_type, start, end)},
            {
                "$group": {
                    "_id": {
                        "$let": {
                            "vars": {
                                "v": {
                                    "$trim": {
                                        "input": {
                                            "$toString": {
                                                "$ifNull": [f"${field}", ""]
                                            }
                                        }
                                    }
                                }
                            },
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$v", ""]},
                                    empty_label,
                                    "$$v",
                                ]
                            },
                        }
                    },
                    **_diesel_currency_sum_fields(native),
                    "liters": {"$sum": safe_double("ltrs")},
                    "count": {"$sum": 1},
                }
            },
        ],
        None,
    )
    rows = [
        {
            "name": doc.get("_id") or empty_label,
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "liters": float(doc.get("liters") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
    ]
    return collapse_top_n(rows, top_n=top_n)


async def _load_diesel_cash_item_names(db: Any) -> list[str]:
    try:
        doc = await db.system_settings.find_one({"key": DIESEL_CASH_ITEMS_SETTING})
    except Exception:
        doc = None
    if not doc:
        return normalize_diesel_cash_item_names(None)
    return normalize_diesel_cash_item_names(doc.get("value"))


def _diesel_cash_match(names: list[str], start, end) -> dict[str, Any]:
    match = diesel_cash_verified_match(names)
    match.update(master_period_clause(start, end))
    return match


async def _diesel_cash_period_totals(
    db: Any,
    names: list[str],
    start,
    end,
) -> dict[str, Any]:
    docs = await aggregate_list(
        db.transactions,
        [
            {"$match": _diesel_cash_match(names, start, end)},
            {"$group": master_currency_group()},
        ],
        1,
    )
    if not docs:
        return {**empty_currency_totals(), "count": 0}
    row = docs[0]
    return {
        "tzs": float(row.get("tzs") or 0.0),
        "usd": float(row.get("usd") or 0.0),
        "zmw": float(row.get("zmw") or 0.0),
        "count": int(row.get("count") or 0),
    }


async def _diesel_cash_month_series(
    db: Any,
    names: list[str],
    year: int,
) -> list[dict[str, Any]]:
    start, end = period_window(year, "year")
    docs = await aggregate_list(
        db.transactions,
        [
            {"$match": _diesel_cash_match(names, start, end)},
            {
                "$group": {
                    "_id": {"$month": "$date"},
                    **_currency_group_fields(),
                }
            },
        ],
        12,
    )
    by_month = {
        int(doc["_id"]): {
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
        if doc.get("_id") is not None
    }
    return [
        {
            "month": m,
            **by_month.get(m, {**empty_currency_totals(), "count": 0}),
        }
        for m in range(1, 13)
    ]


async def _diesel_cash_dimension_rows(
    db: Any,
    names: list[str],
    start,
    end,
    *,
    field: str,
    top_n: int = 12,
    empty_label: str = "—",
) -> list[dict[str, Any]]:
    docs = await aggregate_list(
        db.transactions,
        [
            {"$match": _diesel_cash_match(names, start, end)},
            {
                "$group": {
                    "_id": {
                        "$let": {
                            "vars": {
                                "v": {
                                    "$trim": {
                                        "input": {
                                            "$toString": {
                                                "$ifNull": [f"${field}", ""]
                                            }
                                        }
                                    }
                                }
                            },
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$v", ""]},
                                    empty_label,
                                    "$$v",
                                ]
                            },
                        }
                    },
                    **_currency_group_fields(),
                }
            },
        ],
        None,
    )
    rows = [
        {
            "name": doc.get("_id") or empty_label,
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
    ]
    return collapse_top_n(rows, top_n=top_n)


async def _diesel_cash_item_rows(
    db: Any,
    names: list[str],
    start,
    end,
    *,
    top_n: int = 12,
) -> list[dict[str, Any]]:
    """Prefer category_name, fall back to item when category is blank."""
    docs = await aggregate_list(
        db.transactions,
        [
            {"$match": _diesel_cash_match(names, start, end)},
            {
                "$group": {
                    "_id": {
                        "$let": {
                            "vars": {
                                "cat": {
                                    "$trim": {
                                        "input": {
                                            "$toString": {
                                                "$ifNull": ["$category_name", ""]
                                            }
                                        }
                                    }
                                },
                                "item": {
                                    "$trim": {
                                        "input": {
                                            "$toString": {
                                                "$ifNull": ["$item", ""]
                                            }
                                        }
                                    }
                                },
                            },
                            "in": {
                                "$cond": [
                                    {"$ne": ["$$cat", ""]},
                                    "$$cat",
                                    {
                                        "$cond": [
                                            {"$ne": ["$$item", ""]},
                                            "$$item",
                                            "Diesel Cash",
                                        ]
                                    },
                                ]
                            },
                        }
                    },
                    **_currency_group_fields(),
                }
            },
        ],
        None,
    )
    rows = [
        {
            "name": doc.get("_id") or "Diesel Cash",
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
    ]
    return collapse_top_n(rows, top_n=top_n)


async def _diesel_cash_receipts(
    db: Any,
    names: list[str],
    start,
    end,
) -> dict[str, int]:
    docs = await aggregate_list(
        db.transactions,
        [
            {"$match": _diesel_cash_match(names, start, end)},
            {"$group": {"_id": "$receipt_status", "count": {"$sum": 1}}},
        ],
        None,
    )
    out = empty_receipt_breakdown()
    for doc in docs:
        status = str(doc.get("_id") or "pending").lower()
        count = int(doc.get("count") or 0)
        if status == "received":
            out["received"] += count
        elif status in ("missing", "no_receipt"):
            out["missing"] += count
        else:
            out["pending"] += count
    out["total"] = out["received"] + out["pending"] + out["missing"]
    return out


async def _diesel_cash_liters_docs(
    db: Any,
    names: list[str],
    start,
    end,
) -> list[dict[str, Any]]:
    cursor = db.transactions.find(
        _diesel_cash_match(names, start, end),
        projection={"date": 1, "description": 1},
    )
    return await cursor.to_list(length=None)


async def _compute_diesel_cash_trends(
    db: Any,
    year: int,
    kind: str,
    month: int | None,
) -> dict[str, Any]:
    names = await _load_diesel_cash_item_names(db)
    start, end = period_window(year, kind, month)  # type: ignore[arg-type]
    days = period_day_count(year, kind, month)  # type: ignore[arg-type]
    prior_year = year - 1
    prior_start, prior_end = period_window(prior_year, kind, month)  # type: ignore[arg-type]
    year_start, year_end = period_window(year, "year")
    prior_year_start, prior_year_end = period_window(prior_year, "year")

    (
        totals,
        prior_totals,
        items,
        trucks,
        receipts,
        month_series,
        prior_month_series,
        period_liters_docs,
        prior_liters_docs,
        year_liters_docs,
        prior_year_liters_docs,
    ) = await asyncio.gather(
        _diesel_cash_period_totals(db, names, start, end),
        _diesel_cash_period_totals(db, names, prior_start, prior_end),
        _diesel_cash_item_rows(db, names, start, end, top_n=12),
        _diesel_cash_dimension_rows(
            db, names, start, end, field="truck_number", top_n=12
        ),
        _diesel_cash_receipts(db, names, start, end),
        _diesel_cash_month_series(db, names, year),
        _diesel_cash_month_series(db, names, prior_year),
        _diesel_cash_liters_docs(db, names, start, end),
        _diesel_cash_liters_docs(db, names, prior_start, prior_end),
        _diesel_cash_liters_docs(db, names, year_start, year_end),
        _diesel_cash_liters_docs(db, names, prior_year_start, prior_year_end),
    )
    liters = liters_from_description_docs(period_liters_docs)
    prior_liters = liters_from_description_docs(prior_liters_docs)
    month_series = merge_liters_into_month_series(
        month_series, liters_by_month_from_docs(year_liters_docs, year)
    )
    prior_month_series = merge_liters_into_month_series(
        prior_month_series,
        liters_by_month_from_docs(prior_year_liters_docs, prior_year),
    )
    money = money_triple(totals)
    prior_money = money_triple(prior_totals)
    avgs = averages_from_totals(money, days)
    return {
        "source": PHASE4_DIESEL_CASH_KEY,
        "label": "Diesel Cash",
        "kind": "fuel",
        "phase": 4,
        "ready": True,
        "native_currency": "TZS",
        "mix_label": "Spend by Item",
        "year": year,
        "period": kind,
        "month": month,
        "period_label": period_label(year, kind, month),  # type: ignore[arg-type]
        "days": days,
        "kpis": {
            "totals": money,
            "prior_totals": prior_money,
            "avg_per_day": avgs,
            "liters": liters,
            "prior_liters": prior_liters,
            "avg_liters_per_day": liters / max(1, days),
            "count": int(totals.get("count") or 0),
            "prior_count": int(prior_totals.get("count") or 0),
        },
        "categories": items,
        "stations": [],
        "trucks": trucks,
        "destinations": [],
        "receipts": receipts,
        "month_trend": month_series,
        "prior_month_trend": prior_month_series,
        "deltas": _delta_bundle(month_series, prior_month_series),
    }


def _separate_match(spec: Any, start, end, *, exclude_deposits: bool) -> dict[str, Any]:
    match = dict(spec.match)
    match[spec.date_field] = {"$gte": start, "$lte": end}
    if exclude_deposits:
        match["$nor"] = [parking_congo_deposit_clause()]
    return match


def _separate_currency_sum_fields(spec: Any) -> dict[str, Any]:
    key = currency_bucket_key(spec.currency)
    fields: dict[str, Any] = {
        "tzs": {"$sum": 0},
        "usd": {"$sum": 0},
        "zmw": {"$sum": 0},
        "count": {"$sum": 1},
    }
    fields[key] = {"$sum": spec.amount_expr}
    return fields


async def _separate_period_totals(
    db: Any,
    spec: Any,
    start,
    end,
    *,
    exclude_deposits: bool,
) -> dict[str, Any]:
    coll = getattr(db, spec.collection)
    docs = await aggregate_list(
        coll,
        [
            {
                "$match": _separate_match(
                    spec, start, end, exclude_deposits=exclude_deposits
                )
            },
            {"$group": {"_id": None, **_separate_currency_sum_fields(spec)}},
        ],
        1,
    )
    if not docs:
        return {**empty_currency_totals(), "count": 0}
    row = docs[0]
    return {
        "tzs": float(row.get("tzs") or 0.0),
        "usd": float(row.get("usd") or 0.0),
        "zmw": float(row.get("zmw") or 0.0),
        "count": int(row.get("count") or 0),
    }


async def _separate_month_series_range(
    db: Any,
    spec: Any,
    start,
    end,
    *,
    exclude_deposits: bool,
) -> list[dict[str, Any]]:
    """Month buckets for one separate feed inside ``start``–``end``."""
    coll = getattr(db, spec.collection)
    docs = await aggregate_list(
        coll,
        [
            {
                "$match": _separate_match(
                    spec, start, end, exclude_deposits=exclude_deposits
                )
            },
            {
                "$group": {
                    "_id": {"$month": f"${spec.date_field}"},
                    **_separate_currency_sum_fields(spec),
                }
            },
        ],
        12,
    )
    by_month = {
        int(doc["_id"]): {
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
        if doc.get("_id") is not None
    }
    return [
        {"month": m, **by_month.get(m, {**empty_currency_totals(), "count": 0})}
        for m in range(1, 13)
    ]


async def _separate_month_series(
    db: Any,
    spec: Any,
    year: int,
    *,
    exclude_deposits: bool,
) -> list[dict[str, Any]]:
    start, end = period_window(year, "year")
    return await _separate_month_series_range(
        db, spec, start, end, exclude_deposits=exclude_deposits
    )


async def _separate_dimension_rows(
    db: Any,
    spec: Any,
    start,
    end,
    *,
    field: str,
    exclude_deposits: bool,
    top_n: int = 12,
    empty_label: str = "—",
) -> list[dict[str, Any]]:
    coll = getattr(db, spec.collection)
    docs = await aggregate_list(
        coll,
        [
            {
                "$match": _separate_match(
                    spec, start, end, exclude_deposits=exclude_deposits
                )
            },
            {
                "$group": {
                    "_id": {
                        "$let": {
                            "vars": {
                                "v": {
                                    "$trim": {
                                        "input": {
                                            "$toString": {
                                                "$ifNull": [f"${field}", ""]
                                            }
                                        }
                                    }
                                }
                            },
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$v", ""]},
                                    empty_label,
                                    "$$v",
                                ]
                            },
                        }
                    },
                    **_separate_currency_sum_fields(spec),
                }
            },
        ],
        None,
    )
    rows = [
        {
            "name": doc.get("_id") or empty_label,
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
    ]
    return collapse_top_n(rows, top_n=top_n)


async def _afritrack_variance_rows(
    db: Any,
    spec: Any,
    start,
    end,
) -> list[dict[str, Any]]:
    """Bucket invoice spend by variance sign (over / under / flat)."""
    coll = getattr(db, spec.collection)
    var = safe_double("variance")
    docs = await aggregate_list(
        coll,
        [
            {"$match": _separate_match(spec, start, end, exclude_deposits=False)},
            {
                "$group": {
                    "_id": {
                        "$switch": {
                            "branches": [
                                {
                                    "case": {"$gt": [var, 0]},
                                    "then": "Over-invoice",
                                },
                                {
                                    "case": {"$lt": [var, 0]},
                                    "then": "Under-invoice",
                                },
                            ],
                            "default": "Flat",
                        }
                    },
                    **_separate_currency_sum_fields(spec),
                }
            },
        ],
        None,
    )
    return [
        {
            "name": doc.get("_id") or "Flat",
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
        if float(doc.get("usd") or 0.0)
        or float(doc.get("tzs") or 0.0)
        or float(doc.get("zmw") or 0.0)
    ]


async def _compute_separate_trends(
    db: Any,
    source_key: str,
    year: int,
    kind: str,
    month: int | None,
    *,
    dims: Any | None = None,
    phase: int = 5,
    kind_name: str = "separate",
) -> dict[str, Any]:
    spec = SEPARATE_FEED_BY_KEY[source_key]
    dims = dims or PHASE5_SEPARATE_DIMS[source_key]
    start, end = period_window(year, kind, month)  # type: ignore[arg-type]
    days = period_day_count(year, kind, month)  # type: ignore[arg-type]
    prior_year = year - 1
    prior_start, prior_end = period_window(prior_year, kind, month)  # type: ignore[arg-type]
    exclude = dims.exclude_deposits
    use_variance_slice = source_key == "afritrack"

    coros: list[Any] = [
        _separate_period_totals(db, spec, start, end, exclude_deposits=exclude),
        _separate_period_totals(
            db, spec, prior_start, prior_end, exclude_deposits=exclude
        ),
        _separate_dimension_rows(
            db,
            spec,
            start,
            end,
            field=dims.mix_field,
            exclude_deposits=exclude,
            top_n=12,
        ),
        _separate_dimension_rows(
            db,
            spec,
            start,
            end,
            field=dims.truck_field,
            exclude_deposits=exclude,
            top_n=12,
        ),
        (
            _afritrack_variance_rows(db, spec, start, end)
            if use_variance_slice
            else _separate_dimension_rows(
                db,
                spec,
                start,
                end,
                field=dims.slice_field,
                exclude_deposits=exclude,
                top_n=10,
            )
        ),
        _separate_month_series(db, spec, year, exclude_deposits=exclude),
        _separate_month_series(db, spec, prior_year, exclude_deposits=exclude),
    ]
    (
        totals,
        prior_totals,
        mix_rows,
        truck_rows,
        slice_rows,
        month_series,
        prior_month_series,
    ) = await asyncio.gather(*coros)
    money = money_triple(totals)
    prior_money = money_triple(prior_totals)
    avgs = averages_from_totals(money, days)
    count = int(totals.get("count") or 0)
    if source_key == "sm_burhani":
        mix_rows = polish_dimension_rows(source_key, dims.mix_field, mix_rows)
        truck_rows = polish_dimension_rows(
            source_key, dims.truck_field, truck_rows
        )
        slice_rows = polish_dimension_rows(
            source_key, dims.slice_field, slice_rows
        )
    return {
        "source": source_key,
        "label": spec.label,
        "kind": kind_name,
        "phase": phase,
        "ready": True,
        "sparse": count == 0,
        "native_currency": spec.currency,
        "mix_label": dims.mix_label,
        "trucks_label": dims.truck_label,
        "slice_label": dims.slice_label,
        "year": year,
        "period": kind,
        "month": month,
        "period_label": period_label(year, kind, month),  # type: ignore[arg-type]
        "days": days,
        "kpis": {
            "totals": money,
            "prior_totals": prior_money,
            "avg_per_day": avgs,
            "count": count,
            "prior_count": int(prior_totals.get("count") or 0),
        },
        "categories": mix_rows,
        "trucks": truck_rows,
        "destinations": slice_rows,
        "receipts": empty_receipt_breakdown(),
        "month_trend": month_series,
        "prior_month_trend": prior_month_series,
        "deltas": _delta_bundle(month_series, prior_month_series),
    }


def _usd_ledger_amount() -> dict[str, Any]:
    return {"$ifNull": ["$amount_usd", 0]}


def _usd_ledger_sum_fields() -> dict[str, Any]:
    """Signed ledger: money_out (>0), money_in (<0), net, count."""
    amt = _usd_ledger_amount()
    return {
        "money_out": {
            "$sum": {"$cond": [{"$gt": [amt, 0]}, amt, 0]}
        },
        "money_in": {
            "$sum": {"$cond": [{"$lt": [amt, 0]}, amt, 0]}
        },
        "net": {"$sum": amt},
        "count": {"$sum": 1},
    }


def _usd_ledger_burn_sum_fields() -> dict[str, Any]:
    """Dimension spend = called-out only (positive USD)."""
    amt = _usd_ledger_amount()
    return {
        "tzs": {"$sum": 0},
        "usd": {"$sum": {"$cond": [{"$gt": [amt, 0]}, amt, 0]}},
        "zmw": {"$sum": 0},
        "count": {"$sum": 1},
    }


async def _usd_ledger_period_totals(
    db: Any,
    spec: Any,
    start,
    end,
) -> dict[str, Any]:
    coll = getattr(db, spec.collection)
    docs = await aggregate_list(
        coll,
        [
            {"$match": _separate_match(spec, start, end, exclude_deposits=False)},
            {"$group": {"_id": None, **_usd_ledger_sum_fields()}},
        ],
        1,
    )
    if not docs:
        return {
            "money_out": 0.0,
            "money_in": 0.0,
            "net": 0.0,
            "count": 0,
        }
    row = docs[0]
    return {
        "money_out": float(row.get("money_out") or 0.0),
        "money_in": float(row.get("money_in") or 0.0),
        "net": float(row.get("net") or 0.0),
        "count": int(row.get("count") or 0),
    }


async def _usd_ledger_month_series(
    db: Any,
    spec: Any,
    year: int,
) -> list[dict[str, Any]]:
    start, end = period_window(year, "year")
    coll = getattr(db, spec.collection)
    docs = await aggregate_list(
        coll,
        [
            {"$match": _separate_match(spec, start, end, exclude_deposits=False)},
            {
                "$group": {
                    "_id": {"$month": f"${spec.date_field}"},
                    **_usd_ledger_sum_fields(),
                }
            },
        ],
        12,
    )
    by_month = {
        int(doc["_id"]): {
            "money_out": float(doc.get("money_out") or 0.0),
            "money_in": float(doc.get("money_in") or 0.0),
            "net": float(doc.get("net") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
        if doc.get("_id") is not None
    }
    rows: list[dict[str, Any]] = []
    for m in range(1, 13):
        slot = by_month.get(
            m, {"money_out": 0.0, "money_in": 0.0, "net": 0.0, "count": 0}
        )
        # Trend / deltas use called-out burn in the USD bucket.
        rows.append(
            {
                "month": m,
                "tzs": 0.0,
                "usd": float(slot["money_out"]),
                "zmw": 0.0,
                "money_out": float(slot["money_out"]),
                "money_in": float(slot["money_in"]),
                "net": float(slot["net"]),
                "count": int(slot["count"]),
            }
        )
    return rows


async def _usd_ledger_dimension_rows(
    db: Any,
    spec: Any,
    start,
    end,
    *,
    field: str,
    top_n: int = 12,
    empty_label: str = "—",
) -> list[dict[str, Any]]:
    coll = getattr(db, spec.collection)
    docs = await aggregate_list(
        coll,
        [
            {"$match": _separate_match(spec, start, end, exclude_deposits=False)},
            {
                "$group": {
                    "_id": {
                        "$let": {
                            "vars": {
                                "v": {
                                    "$trim": {
                                        "input": {
                                            "$toString": {
                                                "$ifNull": [f"${field}", ""]
                                            }
                                        }
                                    }
                                }
                            },
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$v", ""]},
                                    empty_label,
                                    "$$v",
                                ]
                            },
                        }
                    },
                    **_usd_ledger_burn_sum_fields(),
                }
            },
        ],
        None,
    )
    rows = [
        {
            "name": doc.get("_id") or empty_label,
            "tzs": float(doc.get("tzs") or 0.0),
            "usd": float(doc.get("usd") or 0.0),
            "zmw": float(doc.get("zmw") or 0.0),
            "count": int(doc.get("count") or 0),
        }
        for doc in docs
    ]
    return collapse_top_n(rows, top_n=top_n)


async def _compute_usd_ledger_trends(
    db: Any,
    source_key: str,
    year: int,
    kind: str,
    month: int | None,
) -> dict[str, Any]:
    spec = SEPARATE_FEED_BY_KEY[source_key]
    dims = PHASE6_USD_LEDGER_DIMS
    start, end = period_window(year, kind, month)  # type: ignore[arg-type]
    days = period_day_count(year, kind, month)  # type: ignore[arg-type]
    prior_year = year - 1
    prior_start, prior_end = period_window(prior_year, kind, month)  # type: ignore[arg-type]

    (
        totals,
        prior_totals,
        mix_rows,
        truck_rows,
        month_series,
        prior_month_series,
    ) = await asyncio.gather(
        _usd_ledger_period_totals(db, spec, start, end),
        _usd_ledger_period_totals(db, spec, prior_start, prior_end),
        _usd_ledger_dimension_rows(
            db, spec, start, end, field=dims.mix_field, top_n=12
        ),
        _usd_ledger_dimension_rows(
            db, spec, start, end, field=dims.truck_field, top_n=12
        ),
        _usd_ledger_month_series(db, spec, year),
        _usd_ledger_month_series(db, spec, prior_year),
    )
    money_out = float(totals.get("money_out") or 0.0)
    money_in = float(totals.get("money_in") or 0.0)  # negative or 0
    money_in_abs = abs(money_in)
    prior_out = float(prior_totals.get("money_out") or 0.0)
    prior_in = float(prior_totals.get("money_in") or 0.0)
    net = float(totals.get("net") or 0.0)
    money = native_money("USD", money_out)
    prior_money = native_money("USD", prior_out)
    avgs = averages_from_totals(money, days)
    slice_rows = []
    if money_in_abs > 0:
        slice_rows.append(
            {"name": "Money In", "tzs": 0.0, "usd": money_in_abs, "zmw": 0.0, "count": 0}
        )
    if money_out > 0:
        slice_rows.append(
            {
                "name": "Called Out",
                "tzs": 0.0,
                "usd": money_out,
                "zmw": 0.0,
                "count": 0,
            }
        )
    return {
        "source": source_key,
        "label": spec.label,
        "kind": "ledger",
        "phase": 6,
        "ready": True,
        "native_currency": "USD",
        "mix_label": dims.mix_label,
        "trucks_label": dims.truck_label,
        "slice_label": dims.slice_label,
        "year": year,
        "period": kind,
        "month": month,
        "period_label": period_label(year, kind, month),  # type: ignore[arg-type]
        "days": days,
        "kpis": {
            "totals": money,
            "prior_totals": prior_money,
            "avg_per_day": avgs,
            "money_out": money_out,
            "money_in": money_in,
            "money_in_abs": money_in_abs,
            "prior_money_out": prior_out,
            "prior_money_in": prior_in,
            "balance": net,
            "count": int(totals.get("count") or 0),
            "prior_count": int(prior_totals.get("count") or 0),
        },
        "categories": mix_rows,
        "trucks": truck_rows,
        "destinations": slice_rows,
        "receipts": empty_receipt_breakdown(),
        "month_trend": month_series,
        "prior_month_trend": prior_month_series,
        "deltas": _delta_bundle(month_series, prior_month_series),
    }


async def _compute_diesel_trends(
    db: Any,
    source_key: str,
    year: int,
    kind: str,
    month: int | None,
) -> dict[str, Any]:
    label, feed_type, native = DIESEL_STATION_BY_KEY[source_key]
    start, end = period_window(year, kind, month)  # type: ignore[arg-type]
    days = period_day_count(year, kind, month)  # type: ignore[arg-type]
    prior_year = year - 1
    prior_start, prior_end = period_window(prior_year, kind, month)  # type: ignore[arg-type]

    (
        totals,
        prior_totals,
        stations,
        trucks,
        destinations,
        month_series,
        prior_month_series,
    ) = await asyncio.gather(
        _diesel_period_totals(db, feed_type, native, start, end),
        _diesel_period_totals(db, feed_type, native, prior_start, prior_end),
        _diesel_dimension_rows(
            db, feed_type, native, start, end, field="diesel_at", top_n=12
        ),
        _diesel_dimension_rows(
            db, feed_type, native, start, end, field="truck_no", top_n=12
        ),
        _diesel_dimension_rows(
            db, feed_type, native, start, end, field="destinations", top_n=10
        ),
        _diesel_month_series(db, feed_type, native, year),
        _diesel_month_series(db, feed_type, native, prior_year),
    )
    money = money_triple(totals)
    prior_money = money_triple(prior_totals)
    liters = float(totals.get("liters") or 0.0)
    prior_liters = float(prior_totals.get("liters") or 0.0)
    avgs = averages_from_totals(money, days)
    return {
        "source": source_key,
        "label": label,
        "kind": "fuel",
        "phase": 3,
        "ready": True,
        "native_currency": native,
        "feed_type": feed_type,
        "mix_label": "Spend by Station",
        "year": year,
        "period": kind,
        "month": month,
        "period_label": period_label(year, kind, month),  # type: ignore[arg-type]
        "days": days,
        "kpis": {
            "totals": money,
            "prior_totals": prior_money,
            "avg_per_day": avgs,
            "liters": liters,
            "prior_liters": prior_liters,
            "avg_liters_per_day": liters / max(1, days),
            "count": int(totals.get("count") or 0),
            "prior_count": int(prior_totals.get("count") or 0),
        },
        "categories": stations,  # stations as mix slices for treemap/HBar
        "stations": stations,
        "trucks": trucks,
        "destinations": destinations,
        "receipts": empty_receipt_breakdown(),
        "month_trend": month_series,
        "prior_month_trend": prior_month_series,
        "deltas": _delta_bundle(month_series, prior_month_series),
    }


async def _supplier_entity_sources(db: Any) -> list[dict[str, Any]]:
    try:
        docs = await db.categories.find(
            {"is_supplier": True, "active": {"$ne": False}},
            {"name": 1, "sidebar_label": 1},
        ).to_list(length=None)
    except Exception:
        docs = []
    out: list[dict[str, Any]] = []
    for doc in docs:
        name = str(doc.get("name") or "").strip()
        if not name:
            continue
        key = item_key_from_name(name)
        if not key:
            continue
        label = str(doc.get("sidebar_label") or name).strip() or name
        out.append(
            {
                "key": f"supplier:{key}",
                "label": label,
                "ready": True,
                "kind": "supplier",
                "category_name": name,
            }
        )
    out.sort(key=lambda r: str(r.get("label") or "").lower())
    return out


async def _payee_entity_sources(db: Any) -> list[dict[str, Any]]:
    docs = await aggregate_list(
        db.transactions,
        [
            {
                "$match": {
                    "verified": True,
                    "deletion_requested": {"$ne": True},
                    "trashed": {"$ne": True},
                    "payee": {"$type": "string", "$ne": ""},
                }
            },
            {
                "$group": {
                    "_id": {"$trim": {"input": {"$ifNull": ["$payee", ""]}}},
                    "count": {"$sum": 1},
                }
            },
            {"$match": {"_id": {"$ne": ""}}},
            {"$sort": {"count": -1}},
        ],
        300,
    )
    out: list[dict[str, Any]] = []
    for doc in docs:
        name = str(doc.get("_id") or "").strip()
        if not name:
            continue
        key = item_key_from_name(name) or name.casefold().replace(" ", "_")
        out.append(
            {
                "key": f"payee:{key}",
                "label": name,
                "ready": True,
                "kind": "payee",
                "payee": name,
                "count": int(doc.get("count") or 0),
            }
        )
    out.sort(key=lambda r: str(r.get("label") or "").lower())
    return out


async def _entity_families_catalog(db: Any) -> list[dict[str, Any]]:
    items, suppliers, payees = await asyncio.gather(
        _sidebar_item_sources(db),
        _supplier_entity_sources(db),
        _payee_entity_sources(db),
    )
    # Normalize item keys already use item: — mark family for Entities UI.
    for row in items:
        row["family"] = "items"
    for row in suppliers:
        row["family"] = "suppliers"
    for row in payees:
        row["family"] = "payees"
    by_family = {
        "items": items,
        "suppliers": suppliers,
        "payees": payees,
    }
    return [
        {
            "key": key,
            "label": label,
            "ready": True,
            "entities": by_family[key],
        }
        for key, label in ENTITY_FAMILIES
    ]


def _parse_entity_keys(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = []
    for chunk in str(raw).replace(";", ",").split(","):
        key = chunk.strip()
        if key:
            parts.append(key)
    return parts


def _entity_meta_rows(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in selected:
        meta = {
            "key": str(row.get("key") or ""),
            "label": str(row.get("label") or ""),
            "kind": str(row.get("kind") or ""),
        }
        if row.get("category_name"):
            meta["category_name"] = str(row.get("category_name") or "")
        if row.get("payee"):
            meta["payee"] = str(row.get("payee") or "")
        out.append(meta)
    return out


async def _resolve_entity_selection(
    db: Any,
    family: str,
    keys: list[str],
) -> tuple[str, str, list[str] | None, list[str] | None, list[dict[str, Any]]]:
    """Return (source_key, label, category_names|None, payees|None, entities)."""
    fam = (family or "items").strip().lower()
    if fam not in ENTITY_FAMILY_KEYS:
        raise HTTPException(status_code=422, detail=f"Unknown entity family '{family}'")
    if not keys:
        raise HTTPException(status_code=422, detail="Select at least one entity")

    if fam == "payees":
        catalog = await _payee_entity_sources(db)
        by_key = {str(p["key"]): p for p in catalog}
        by_name = {
            str(p.get("payee") or "").casefold(): p for p in catalog
        }
        selected = []
        for key in keys:
            row = by_key.get(key)
            if row is None and key.startswith("payee:"):
                slug = key.split(":", 1)[1]
                for p in catalog:
                    if item_key_from_name(str(p.get("payee") or "")) == slug:
                        row = p
                        break
            if row is None:
                row = by_name.get(key.casefold())
            if row is None:
                raise HTTPException(status_code=404, detail=f"Unknown payee '{key}'")
            selected.append(row)
        payee_names = [
            str(r.get("payee") or r.get("label") or "").strip()
            for r in selected
            if str(r.get("payee") or r.get("label") or "").strip()
        ]
        if not payee_names:
            raise HTTPException(status_code=404, detail="No matching payees")
        labels = [str(r.get("label") or r.get("payee") or "") for r in selected]
        label = entity_set_label(fam, labels)
        source_key = (
            str(selected[0]["key"])
            if len(selected) == 1
            else ",".join(str(r["key"]) for r in selected)
        )
        return source_key, label, None, payee_names, _entity_meta_rows(selected)

    # items / suppliers — category names
    catalog = (
        await _sidebar_item_sources(db)
        if fam == "items"
        else await _supplier_entity_sources(db)
    )
    by_key = {str(r["key"]): r for r in catalog}
    selected = []
    for key in keys:
        row = by_key.get(key)
        if row is None:
            prefix = "item:" if fam == "items" else "supplier:"
            want = key if key.startswith(prefix) else f"{prefix}{key}"
            row = by_key.get(want)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown {fam[:-1]} '{key}'",
            )
        selected.append(row)
    names = [
        str(r.get("category_name") or r.get("label") or "").strip()
        for r in selected
        if str(r.get("category_name") or r.get("label") or "").strip()
    ]
    if not names:
        raise HTTPException(status_code=404, detail="No matching categories")
    labels = [str(r.get("label") or r.get("category_name") or "") for r in selected]
    label = entity_set_label(fam, labels)
    source_key = (
        str(selected[0]["key"])
        if len(selected) == 1
        else ",".join(str(r["key"]) for r in selected)
    )
    return source_key, label, names, None, _entity_meta_rows(selected)


@analytics.get("/entities/sources")
async def analytics_entity_sources(
    request: Request,
    _manager: Manager,
) -> dict:
    """Entities (earth tab) catalog: Items, Payees, Suppliers."""
    cache_k = entity_cache_key("entities_sources")
    cached = entity_cache_get(cache_k)
    if cached is not None:
        return cached
    families = await _entity_families_catalog(_database(request))
    payload = {"families": families}
    entity_cache_set(cache_k, payload)
    return payload


async def _compute_entity_compare_slice(
    db: Any,
    *,
    key: str,
    label: str,
    year: int,
    kind: str,
    month: int | None,
    category_names: list[str] | None,
    payees: list[str] | None,
    color: str,
) -> dict[str, Any]:
    """Lean per-entity totals + month series for Compare mode."""
    start, end = period_window(year, kind, month)  # type: ignore[arg-type]
    prior_year = year - 1
    prior_start, prior_end = period_window(prior_year, kind, month)  # type: ignore[arg-type]
    days = period_day_count(year, kind, month)  # type: ignore[arg-type]
    filter_kwargs = {
        "category_names": category_names or None,
        "payees": payees or None,
    }
    totals, prior_totals, month_series, prior_month_series = await asyncio.gather(
        _master_totals(db, start, end, **filter_kwargs),
        _master_totals(db, prior_start, prior_end, **filter_kwargs),
        _master_month_series(db, year, **filter_kwargs),
        _master_month_series(db, prior_year, **filter_kwargs),
    )
    money = money_triple(totals)
    prior_money = money_triple(prior_totals)
    return {
        "key": key,
        "label": label,
        "color": color,
        "totals": money,
        "prior_totals": prior_money,
        "avg_per_day": averages_from_totals(money, days),
        "count": int(totals.get("count") or 0),
        "prior_count": int(prior_totals.get("count") or 0),
        "month_trend": month_series,
        "prior_month_trend": prior_month_series,
    }


@analytics.get("/entities")
async def analytics_entities(
    request: Request,
    _manager: Manager,
    family: str = Query("items", pattern="^(items|payees|suppliers)$"),
    keys: str = Query(..., description="Comma-separated entity keys"),
    year: int = Query(..., ge=2000, le=2100),
    period: str = Query("year", pattern="^(year|month)$"),
    month: int | None = Query(None, ge=1, le=12),
) -> dict:
    """Deep-dive one or many Master-side entities (Items / Payees / Suppliers)."""
    kind, resolved_month = _parse_period(year, period, month)
    key_list = _parse_entity_keys(keys)
    cache_k = entity_cache_key(
        "entities", family, ",".join(key_list), year, kind, resolved_month
    )
    cached = entity_cache_get(cache_k)
    if cached is not None:
        return cached
    db = _database(request)
    source_key, label, category_names, payees, entities = await _resolve_entity_selection(
        db, family, key_list
    )
    n_cat = len(category_names or [])
    n_pay = len(payees or [])
    kind_name = {
        "items": "item" if n_cat <= 1 else "item_set",
        "suppliers": "supplier" if n_cat <= 1 else "supplier_set",
        "payees": "payee" if n_pay <= 1 else "payee_set",
    }[family]
    mix_label = {
        "items": (
            "Spend by Item" if n_cat > 1 else "Spend by Description"
        ),
        "suppliers": (
            "Spend by Supplier" if n_cat > 1 else "Spend by Description"
        ),
        "payees": (
            "Spend by Payee" if n_pay > 1 else "Spend by Description"
        ),
    }[family]
    payload = await _compute_master_trends(
        db,
        year,
        kind,
        resolved_month,
        source_key=source_key,
        label=label,
        category_names=category_names,
        payees=payees,
        entities=entities,
        kind_name=kind_name,
        phase=2 if max(n_cat, n_pay) > 1 else 1,
        mix_label=mix_label,
    )
    entity_cache_set(cache_k, payload)
    return payload


@analytics.get("/entities/compare")
async def analytics_entities_compare(
    request: Request,
    _manager: Manager,
    family: str = Query("items", pattern="^(items|payees|suppliers)$"),
    keys: str = Query(..., description="Comma-separated entity keys (2–4)"),
    year: int = Query(..., ge=2000, le=2100),
    period: str = Query("year", pattern="^(year|month)$"),
    month: int | None = Query(None, ge=1, le=12),
) -> dict:
    """Side-by-side compare for 2–4 Entities (lean totals + month series)."""
    kind, resolved_month = _parse_period(year, period, month)
    key_list = _parse_entity_keys(keys)
    if len(key_list) < 2:
        raise HTTPException(
            status_code=422,
            detail="Compare needs at least 2 entities",
        )
    if len(key_list) > ENTITY_COMPARE_MAX:
        key_list = key_list[:ENTITY_COMPARE_MAX]
    cache_k = entity_cache_key(
        "entities_compare", family, ",".join(key_list), year, kind, resolved_month
    )
    cached = entity_cache_get(cache_k)
    if cached is not None:
        return cached

    db = _database(request)
    slices = []
    labels = []
    for i, key in enumerate(key_list):
        source_key, label, category_names, payees, entities = (
            await _resolve_entity_selection(db, family, [key])
        )
        color = ENTITY_COMPARE_COLORS[i % len(ENTITY_COMPARE_COLORS)]
        labels.append(label)
        slices.append(
            _compute_entity_compare_slice(
                db,
                key=source_key,
                label=label,
                year=year,
                kind=kind,
                month=resolved_month,
                category_names=category_names,
                payees=payees,
                color=color,
            )
        )
    series = await asyncio.gather(*slices)
    # Rank by TZS total descending for the bar chart.
    ranked = sorted(
        series,
        key=lambda r: float((r.get("totals") or {}).get("tzs") or 0.0),
        reverse=True,
    )
    payload = {
        "mode": "compare",
        "family": family,
        "label": entity_set_label(family, labels),
        "ready": True,
        "sparse": all(int(r.get("count") or 0) == 0 for r in series),
        "year": year,
        "period": kind,
        "month": resolved_month,
        "period_label": period_label(year, kind, resolved_month),  # type: ignore[arg-type]
        "entities": [
            {"key": r["key"], "label": r["label"], "color": r["color"]}
            for r in series
        ],
        "series": list(series),
        "ranked": [
            {
                "key": r["key"],
                "label": r["label"],
                "color": r["color"],
                "tzs": float((r.get("totals") or {}).get("tzs") or 0.0),
                "usd": float((r.get("totals") or {}).get("usd") or 0.0),
                "zmw": float((r.get("totals") or {}).get("zmw") or 0.0),
                "count": int(r.get("count") or 0),
            }
            for r in ranked
        ],
    }
    entity_cache_set(cache_k, payload)
    return payload


async def _master_truck_fleet_rows(
    db: Any,
    start,
    end,
) -> list[dict[str, Any]]:
    """All Master trucks in window with TZS/USD/ZMW + entry counts (no top-N).

    Skips missing/blank ``truck_number`` — those are not fleet vehicles and the
    detail endpoint rejects placeholder plates.
    """
    match = {
        **_master_base_match(start, end),
        "truck_number": {
            "$exists": True,
            "$nin": [None, "", "—", "-", "–", "Unknown", "N/A", "n/a", "none", "null"],
        },
    }
    docs = await aggregate_list(
        db.transactions,
        [
            {"$match": match},
            {
                "$group": {
                    "_id": "$truck_number",
                    **_currency_group_fields(),
                }
            },
        ],
        None,
    )
    rows: list[dict[str, Any]] = []
    for doc in docs:
        plate = str(doc.get("_id") or "").strip()
        if not plate or plate in {"—", "-", "–"}:
            continue
        rows.append(
            {
                "truck": plate,
                "tzs": float(doc.get("tzs") or 0.0),
                "usd": float(doc.get("usd") or 0.0),
                "zmw": float(doc.get("zmw") or 0.0),
                "count": int(doc.get("count") or 0),
            }
        )
    return rows


async def _fleet_kind_lookup(db: Any) -> dict[str, str]:
    """Active registry plates → kind (truck > trailer > motor_vehicle)."""
    kinds: dict[str, str] = {}
    for coll, kind in (
        ("trucks", "truck"),
        ("trailers", "trailer"),
        ("motor_vehicles", "motor_vehicle"),
    ):
        try:
            docs = await db[coll].find(
                {"active": {"$ne": False}},
                {"number": 1},
            ).to_list(length=None)
        except Exception:
            docs = []
        for doc in docs or []:
            num = str((doc or {}).get("number") or "").strip()
            key = _truck_norm_key(num)
            if key and key not in kinds:
                kinds[key] = kind
    return kinds


def _prior_fleet_window(
    year: int,
    kind: str,
    month: int | None,
) -> tuple[int, str, int | None]:
    """Prior comparable window for fleet MoM (prior month or prior year)."""
    if kind == "month":
        m = int(month or 1)
        if m <= 1:
            return year - 1, "month", 12
        return year, "month", m - 1
    return year - 1, "year", None


@analytics.get("/trucks")
async def analytics_trucks_fleet(
    request: Request,
    _manager: Manager,
    year: int = Query(..., ge=2000, le=2100),
    period: str = Query("year", pattern="^(year|month)$"),
    month: int | None = Query(None, ge=1, le=12),
) -> dict:
    """Analytics → Trucks fleet table (multi-currency Master spend)."""
    kind, resolved_month = _parse_period(year, period, month)
    # v4 key — exclude blank/placeholder truck_number buckets from fleet.
    cache_k = entity_cache_key("trucks_fleet_v4", year, kind, resolved_month)
    cached = entity_cache_get(cache_k)
    if cached is not None:
        return cached
    start, end = period_window(year, kind, resolved_month)  # type: ignore[arg-type]
    p_year, p_kind, p_month = _prior_fleet_window(year, kind, resolved_month)
    prior_start, prior_end = period_window(p_year, p_kind, p_month)  # type: ignore[arg-type]
    db = _database(request)
    raw, prior_raw, kind_lookup = await asyncio.gather(
        _master_truck_fleet_rows(db, start, end),
        _master_truck_fleet_rows(db, prior_start, prior_end),
        _fleet_kind_lookup(db),
    )
    table = build_truck_fleet_table(
        raw, prior_rows=prior_raw, kind_lookup=kind_lookup
    )
    mom_label = (
        f"vs {period_label(p_year, p_kind, p_month)}"  # type: ignore[arg-type]
    )
    counts = {"truck": 0, "trailer": 0, "motor_vehicle": 0, "unknown": 0}
    for row in table["trucks"]:
        vk = str(row.get("vehicle_kind") or "unknown")
        counts[vk] = counts.get(vk, 0) + 1
    payload = {
        "year": year,
        "period": kind,
        "month": resolved_month,
        "period_label": period_label(year, kind, resolved_month),  # type: ignore[arg-type]
        "ready": True,
        "trucks": table["trucks"],
        "totals": table["totals"],
        "truck_count": table["truck_count"],
        "share_basis": table.get("share_basis") or "tzs",
        "mom_label": mom_label,
        "prior_period_label": period_label(p_year, p_kind, p_month),  # type: ignore[arg-type]
        "kind_counts": counts,
    }
    entity_cache_set(cache_k, payload)
    return payload


def _data_table_window(
    date_from: date | None,
    date_to: date | None,
) -> tuple[datetime, datetime, date, date]:
    """Resolve From/To (default YTD → today); swap if inverted."""
    today = date.today()
    start_d = date_from or date(today.year, 1, 1)
    end_d = date_to or today
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    start = datetime(start_d.year, start_d.month, start_d.day, 0, 0, 0)
    end = datetime(end_d.year, end_d.month, end_d.day, 23, 59, 59)
    return start, end, start_d, end_d


def _tag_year_month_buckets(
    year: int, series: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach ``year`` to month-series rows for data-table merge."""
    out: list[dict[str, Any]] = []
    for row in series or []:
        try:
            month = int(row.get("month"))
        except (TypeError, ValueError):
            continue
        if month < 1 or month > 12:
            continue
        out.append(
            {
                "year": year,
                "month": month,
                "tzs": float(row.get("tzs") or 0.0),
                "usd": float(row.get("usd") or 0.0),
                "zmw": float(row.get("zmw") or 0.0),
            }
        )
    return out


async def _data_table_month_buckets(
    db: Any, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Per-source month currency buckets across years spanned by From–To."""
    start_d = start.date() if isinstance(start, datetime) else start
    end_d = end.date() if isinstance(end, datetime) else end
    buckets: list[dict[str, Any]] = []

    async def _year_buckets(year: int) -> list[dict[str, Any]]:
        w0 = max(start, datetime(year, 1, 1, 0, 0, 0))
        w1 = min(end, datetime(year, 12, 31, 23, 59, 59))
        if w1 < w0:
            return []

        async def _master() -> list[dict[str, Any]]:
            series = await _master_month_series_range(db, w0, w1)
            return _tag_year_month_buckets(year, series)

        async def _separate(spec: Any) -> list[dict[str, Any]]:
            dims = PHASE5_SEPARATE_DIMS.get(spec.key)
            exclude = bool(dims and dims.exclude_deposits)
            series = await _separate_month_series_range(
                db, spec, w0, w1, exclude_deposits=exclude
            )
            return _tag_year_month_buckets(year, series)

        async def _station(
            _key: str, _label: str, feed_type: str, native: str
        ) -> list[dict[str, Any]]:
            series = await _diesel_month_series_range(
                db, feed_type, native, w0, w1
            )
            return _tag_year_month_buckets(year, series)

        coros: list[Any] = [_master()]
        coros.extend(_separate(spec) for spec in SEPARATE_EXPENSE_FEEDS)
        coros.extend(
            _station(key, label, feed_type, native)
            for key, label, feed_type, native in DIESEL_STATION_TRENDS
        )
        parts = await asyncio.gather(*coros)
        merged: list[dict[str, Any]] = []
        for part in parts:
            merged.extend(part)
        return merged

    year_parts = await asyncio.gather(
        *[_year_buckets(y) for y in range(start_d.year, end_d.year + 1)]
    )
    for part in year_parts:
        buckets.extend(part)
    return buckets


@analytics.get("/data-table")
async def analytics_data_table(
    request: Request,
    _manager: Manager,
    date_from: date | None = Query(None, description="Inclusive start (default: Jan 1)"),
    date_to: date | None = Query(None, description="Inclusive end (default: today)"),
) -> dict:
    """Analytics → Data table: one monetary row per source for From–To."""
    start, end, start_d, end_d = _data_table_window(date_from, date_to)
    cache_k = entity_cache_key(
        "data_table_v4", start_d.isoformat(), end_d.isoformat()
    )
    cached = entity_cache_get(cache_k)
    if cached is not None:
        return cached

    db = _database(request)

    async def _master_row() -> dict[str, Any]:
        totals = await _master_totals(db, start, end)
        return {
            "key": "master",
            "label": "Master Expenses",
            "group": "Master",
            "tzs": float(totals.get("tzs") or 0.0),
            "usd": float(totals.get("usd") or 0.0),
            "zmw": float(totals.get("zmw") or 0.0),
            "count": int(totals.get("count") or 0),
        }

    async def _separate_row(spec: Any) -> dict[str, Any]:
        dims = PHASE5_SEPARATE_DIMS.get(spec.key)
        exclude = bool(dims and dims.exclude_deposits)
        totals = await _separate_period_totals(
            db, spec, start, end, exclude_deposits=exclude
        )
        return {
            "key": spec.key,
            "label": spec.label,
            "group": "Separate Expenses",
            "tzs": float(totals.get("tzs") or 0.0),
            "usd": float(totals.get("usd") or 0.0),
            "zmw": float(totals.get("zmw") or 0.0),
            "count": int(totals.get("count") or 0),
        }

    async def _station_row(key: str, label: str, feed_type: str, native: str) -> dict[str, Any]:
        totals = await _diesel_period_totals(db, feed_type, native, start, end)
        return {
            "key": key,
            "label": label,
            "group": "Fuel stations",
            "tzs": float(totals.get("tzs") or 0.0),
            "usd": float(totals.get("usd") or 0.0),
            "zmw": float(totals.get("zmw") or 0.0),
            "count": int(totals.get("count") or 0),
        }

    row_coros: list[Any] = [_master_row()]
    row_coros.extend(_separate_row(spec) for spec in SEPARATE_EXPENSE_FEEDS)
    row_coros.extend(
        _station_row(key, label, feed_type, native)
        for key, label, feed_type, native in DIESEL_STATION_TRENDS
    )
    # Spend Trend is always a full calendar year (Jan–Dec), keyed off To date.
    chart_year = end_d.year
    year_start = datetime(chart_year, 1, 1, 0, 0, 0)
    year_end = datetime(chart_year, 12, 31, 23, 59, 59)
    from .fx_rates import get_rates_to_tzs

    raw_rows, month_buckets, fx = await asyncio.gather(
        asyncio.gather(*row_coros),
        _data_table_month_buckets(db, year_start, year_end),
        asyncio.to_thread(get_rates_to_tzs),
    )
    table = build_data_table_rows(list(raw_rows))
    month_trend = build_data_table_month_trend(
        month_buckets,
        date(chart_year, 1, 1),
        date(chart_year, 12, 31),
    )
    rates = dict(fx.get("rates") or {}) if isinstance(fx, dict) else {}
    payload = {
        "ready": True,
        "date_from": start_d.isoformat(),
        "date_to": end_d.isoformat(),
        "period_label": f"{start_d.isoformat()} → {end_d.isoformat()}",
        "sources": table["sources"],
        "totals": table["totals"],
        "source_count": table["source_count"],
        "chart_year": chart_year,
        "month_trend": month_trend,
        "rates_to_tzs": {
            "TZS": float(rates.get("TZS") or 1.0),
            "USD": float(rates.get("USD") or 0.0),
            "ZMW": float(rates.get("ZMW") or 0.0),
        },
        "fx_source": str(fx.get("source") or "fallback") if isinstance(fx, dict) else "fallback",
    }
    entity_cache_set(cache_k, payload)
    return payload


async def _meetings_person_period_rows(
    db: Any,
    start: datetime,
    end: datetime,
    *,
    dimension: str,
    person: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate Master spend + receipt counters per ownership/approver."""
    field = normalize_meetings_dimension(dimension)
    match = _master_base_match(start, end)
    needle = (person or "").strip()
    if needle and needle.casefold() not in {"all", "*"}:
        match[field] = {
            "$regex": f"^{re.escape(needle)}$",
            "$options": "i",
        }
    docs = await aggregate_list(
        db.transactions,
        [
            {"$match": match},
            {
                "$group": {
                    "_id": {"$ifNull": [f"${field}", ""]},
                    **_currency_group_fields(),
                    "receipt_statuses": {"$push": "$receipt_status"},
                }
            },
        ],
        None,
    )
    rows: list[dict[str, Any]] = []
    for doc in docs:
        needed = 0
        received = 0
        for status in list(doc.get("receipt_statuses") or []):
            need, got = receipt_needed_and_received(status)
            if need:
                needed += 1
            if got:
                received += 1
        rows.append(
            {
                "name": meetings_person_label(doc.get("_id")),
                "tzs": float(doc.get("tzs") or 0.0),
                "usd": float(doc.get("usd") or 0.0),
                "zmw": float(doc.get("zmw") or 0.0),
                "count": int(doc.get("count") or 0),
                "receipts_needed": needed,
                "receipts_received": received,
            }
        )
    return rows


async def _meetings_person_month_buckets(
    db: Any,
    start: datetime,
    end: datetime,
    *,
    dimension: str,
    person: str | None = None,
) -> list[dict[str, Any]]:
    """Per-person monthly TZS buckets for Meetings Spend Trend."""
    field = normalize_meetings_dimension(dimension)
    match = _master_base_match(start, end)
    needle = (person or "").strip()
    if needle and needle.casefold() not in {"all", "*"}:
        match[field] = {
            "$regex": f"^{re.escape(needle)}$",
            "$options": "i",
        }
    docs = await aggregate_list(
        db.transactions,
        [
            {"$match": match},
            {
                "$group": {
                    "_id": {
                        "person": {"$ifNull": [f"${field}", ""]},
                        "year": {"$year": "$date"},
                        "month": {"$month": "$date"},
                    },
                    **_currency_group_fields(),
                }
            },
        ],
        None,
    )
    out: list[dict[str, Any]] = []
    for doc in docs:
        gid = doc.get("_id") or {}
        if not isinstance(gid, dict):
            continue
        try:
            year = int(gid.get("year"))
            month = int(gid.get("month"))
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "name": meetings_person_label(gid.get("person")),
                "year": year,
                "month": month,
                "tzs": float(doc.get("tzs") or 0.0),
            }
        )
    return out


@analytics.get("/meetings")
async def analytics_meetings(
    request: Request,
    _manager: Manager,
    date_from: date | None = Query(None, description="Inclusive start (default: Jan 1)"),
    date_to: date | None = Query(None, description="Inclusive end (default: today)"),
    dimension: str = Query(
        "approver",
        description="Group by Master field: approver (App by) or ownership",
    ),
    person: str | None = Query(None, description="Optional person name filter"),
) -> dict:
    """Analytics → Meetings: Master spend / compliance by App by or Ownership."""
    dim = normalize_meetings_dimension(dimension)
    start, end, start_d, end_d = _data_table_window(date_from, date_to)
    person_key = (person or "").strip()
    cache_k = entity_cache_key(
        "meetings_v1",
        start_d.isoformat(),
        end_d.isoformat(),
        dim,
        person_key.casefold() or "*",
    )
    cached = entity_cache_get(cache_k)
    if cached is not None:
        return cached

    from .fx_rates import get_rates_to_tzs

    chart_year = end_d.year
    year_start = datetime(chart_year, 1, 1, 0, 0, 0)
    year_end = datetime(chart_year, 12, 31, 23, 59, 59)
    db = _database(request)
    period_rows, month_buckets, fx = await asyncio.gather(
        _meetings_person_period_rows(
            db, start, end, dimension=dim, person=person_key or None
        ),
        _meetings_person_month_buckets(
            db, year_start, year_end, dimension=dim, person=person_key or None
        ),
        asyncio.to_thread(get_rates_to_tzs),
    )
    table = build_meetings_people_rows(period_rows)
    month_trend = build_meetings_month_trend(
        month_buckets,
        chart_people=list(table.get("chart_people") or []),
        chart_year=chart_year,
    )
    rates = dict(fx.get("rates") or {}) if isinstance(fx, dict) else {}
    payload = {
        "ready": True,
        "date_from": start_d.isoformat(),
        "date_to": end_d.isoformat(),
        "period_label": f"{start_d.isoformat()} → {end_d.isoformat()}",
        "dimension": dim,
        "dimension_label": (
            "App by" if dim == "approver" else "Ownership"
        ),
        "person": person_key,
        "people": table["people"],
        "totals": table["totals"],
        "person_count": table["person_count"],
        "chart_people": table["chart_people"],
        "chart_year": chart_year,
        "month_trend": month_trend,
        "rates_to_tzs": {
            "TZS": float(rates.get("TZS") or 1.0),
            "USD": float(rates.get("USD") or 0.0),
            "ZMW": float(rates.get("ZMW") or 0.0),
        },
        "fx_source": str(fx.get("source") or "fallback")
        if isinstance(fx, dict)
        else "fallback",
    }
    entity_cache_set(cache_k, payload)
    return payload


@analytics.get("/trucks/detail")
async def analytics_truck_detail(
    request: Request,
    _manager: Manager,
    truck: str = Query(..., min_length=1, description="Truck plate / number"),
    year: int = Query(..., ge=2000, le=2100),
    period: str = Query("year", pattern="^(year|month)$"),
    month: int | None = Query(None, ge=1, le=12),
) -> dict:
    """Analytics → Trucks deep-dive for one Master truck."""
    kind, resolved_month = _parse_period(year, period, month)
    plate = (truck or "").strip()
    if not plate or plate in {"—", "-"}:
        raise HTTPException(status_code=422, detail="Select a truck")
    cache_k = entity_cache_key(
        "truck_detail", plate.casefold(), year, kind, resolved_month
    )
    cached = entity_cache_get(cache_k)
    if cached is not None:
        return cached
    payload = await _compute_master_trends(
        _database(request),
        year,
        kind,
        resolved_month,
        source_key=f"truck:{plate}",
        label=plate,
        truck=plate,
        kind_name="truck",
        phase=2,
        mix_label="Spend by Category",
        trucks_label="Top descriptions",
    )
    entity_cache_set(cache_k, payload)
    return payload


async def _dispatch_trends(
    db: Any,
    source: str,
    year: int,
    kind: str,
    resolved_month: int | None,
) -> dict[str, Any]:
    """Resolve one Trends source to a full deep-dive payload."""
    key = (source or "master").strip().lower()
    if key == "master":
        return await _compute_master_trends(db, year, kind, resolved_month)

    if key.startswith("item:"):
        resolved = await _resolve_item_category(db, key)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown ITEMS trends source '{source}'",
            )
        source_key, category_name = resolved
        return await _compute_master_trends(
            db,
            year,
            kind,
            resolved_month,
            source_key=source_key,
            label=category_name,
            category_name=category_name,
        )

    if key in PHASE3_DIESEL_KEYS:
        return await _compute_diesel_trends(
            db, key, year, kind, resolved_month
        )

    if key in PHASE4_DIESEL_CASH_KEYS:
        return await _compute_diesel_cash_trends(
            db, year, kind, resolved_month
        )

    if key in PHASE5_SEPARATE_KEYS:
        return await _compute_separate_trends(
            db, key, year, kind, resolved_month
        )

    if key in PHASE6_USD_LEDGER_KEYS:
        return await _compute_usd_ledger_trends(
            db, key, year, kind, resolved_month
        )

    if key in PHASE7_INSURANCE_KEYS:
        return await _compute_separate_trends(
            db,
            key,
            year,
            kind,
            resolved_month,
            dims=PHASE7_INSURANCE_DIMS[key],
            phase=7,
            kind_name="insurance",
        )

    if key in PHASE8_OUTLIER_KEYS:
        return await _compute_separate_trends(
            db,
            key,
            year,
            kind,
            resolved_month,
            dims=PHASE8_OUTLIER_DIMS[key],
            phase=8,
            kind_name="outlier",
        )

    phase = TREND_SOURCE_PHASE.get(key)
    if phase is None:
        raise HTTPException(status_code=404, detail=f"Unknown trends source '{source}'")
    return {
        "source": key,
        "label": key,
        "kind": "pending",
        "phase": phase,
        "ready": False,
        "year": year,
        "period": kind,
        "month": resolved_month,
        "period_label": period_label(year, kind, resolved_month),  # type: ignore[arg-type]
        "days": period_day_count(year, kind, resolved_month),  # type: ignore[arg-type]
        "message": f"Deep analysis for this source ships in Phase {phase}.",
    }


def _trends_compare_slice(payload: dict[str, Any], color: str) -> dict[str, Any]:
    """Lean compare row from a full Trends payload."""
    kpis = dict(payload.get("kpis") or {})
    return {
        "key": str(payload.get("source") or ""),
        "label": str(payload.get("label") or payload.get("source") or ""),
        "kind": str(payload.get("kind") or ""),
        "color": color,
        "native_currency": str(payload.get("native_currency") or ""),
        "totals": dict(kpis.get("totals") or {}),
        "prior_totals": dict(kpis.get("prior_totals") or {}),
        "avg_per_day": dict(kpis.get("avg_per_day") or {}),
        "count": int(kpis.get("count") or 0),
        "prior_count": int(kpis.get("prior_count") or 0),
        "month_trend": list(payload.get("month_trend") or []),
        "prior_month_trend": list(payload.get("prior_month_trend") or []),
    }


@analytics.get("/trends/sources")
async def analytics_trend_sources(
    request: Request,
    _manager: Manager,
) -> dict:
    """Source picker catalog for Trends (Master + Items + Separate + Fuel)."""
    cache_k = entity_cache_key("trends_sources")
    cached = entity_cache_get(cache_k)
    if cached is not None:
        return cached
    groups = static_trend_source_catalog()
    items = await _sidebar_item_sources(_database(request))
    for group in groups:
        if group.get("group") == "Items":
            group["sources"] = items
            break
    payload = {"groups": groups}
    entity_cache_set(cache_k, payload)
    return payload


@analytics.get("/trends")
async def analytics_trends(
    request: Request,
    _manager: Manager,
    source: str = Query("master"),
    year: int = Query(..., ge=2000, le=2100),
    period: str = Query("year", pattern="^(year|month)$"),
    month: int | None = Query(None, ge=1, le=12),
) -> dict:
    """Deep-dive Trends (Phase 1–8: full monetary source coverage)."""
    kind, resolved_month = _parse_period(year, period, month)
    key = (source or "master").strip().lower()
    cache_k = entity_cache_key("trends", key, year, kind, resolved_month)
    cached = entity_cache_get(cache_k)
    if cached is not None:
        return cached
    payload = await _dispatch_trends(
        _database(request), key, year, kind, resolved_month
    )
    entity_cache_set(cache_k, payload)
    return payload


@analytics.get("/trends/compare")
async def analytics_trends_compare(
    request: Request,
    _manager: Manager,
    sources: str = Query(..., description="Comma-separated ready source keys (2–4)"),
    year: int = Query(..., ge=2000, le=2100),
    period: str = Query("year", pattern="^(year|month)$"),
    month: int | None = Query(None, ge=1, le=12),
) -> dict:
    """Side-by-side compare for 2–4 ready Trends sources."""
    kind, resolved_month = _parse_period(year, period, month)
    key_list = _parse_entity_keys(sources)
    if len(key_list) < 2:
        raise HTTPException(
            status_code=422,
            detail="Compare needs at least 2 sources",
        )
    if len(key_list) > TREND_COMPARE_MAX:
        key_list = key_list[:TREND_COMPARE_MAX]
    cache_k = entity_cache_key(
        "trends_compare", ",".join(key_list), year, kind, resolved_month
    )
    cached = entity_cache_get(cache_k)
    if cached is not None:
        return cached

    db = _database(request)

    async def _one(i: int, src: str) -> dict[str, Any]:
        # Prefer per-source cache so Compare reuses Analyze hits.
        src_key = entity_cache_key("trends", src, year, kind, resolved_month)
        hit = entity_cache_get(src_key)
        if hit is None:
            hit = await _dispatch_trends(db, src, year, kind, resolved_month)
            entity_cache_set(src_key, hit)
        if not bool(hit.get("ready")):
            raise HTTPException(
                status_code=422,
                detail=f"Source '{src}' is not ready for compare",
            )
        color = TREND_COMPARE_COLORS[i % len(TREND_COMPARE_COLORS)]
        return _trends_compare_slice(hit, color)

    series = await asyncio.gather(
        *[_one(i, src) for i, src in enumerate(key_list)]
    )
    ranked = sorted(
        series,
        key=lambda r: float((r.get("totals") or {}).get("tzs") or 0.0),
        reverse=True,
    )
    labels = [str(r.get("label") or r.get("key") or "") for r in series]
    payload = {
        "mode": "compare",
        "label": (
            f"{labels[0]} + {labels[1]}"
            if len(labels) == 2
            else f"{len(labels)} sources · {labels[0]} +"
        ),
        "ready": True,
        "sparse": all(int(r.get("count") or 0) == 0 for r in series),
        "year": year,
        "period": kind,
        "month": resolved_month,
        "period_label": period_label(year, kind, resolved_month),  # type: ignore[arg-type]
        "sources": [
            {"key": r["key"], "label": r["label"], "color": r["color"]}
            for r in series
        ],
        "series": list(series),
        "ranked": [
            {
                "key": r["key"],
                "label": r["label"],
                "color": r["color"],
                "tzs": float((r.get("totals") or {}).get("tzs") or 0.0),
                "usd": float((r.get("totals") or {}).get("usd") or 0.0),
                "zmw": float((r.get("totals") or {}).get("zmw") or 0.0),
                "count": int(r.get("count") or 0),
            }
            for r in ranked
        ],
    }
    entity_cache_set(cache_k, payload)
    return payload
