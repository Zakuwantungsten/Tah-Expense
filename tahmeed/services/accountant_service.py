"""Accountant service — async Motor queries for the accountant dashboard."""

import asyncio
import calendar
import hashlib
import json
import re
from datetime import datetime, date, timedelta
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple, Union
from urllib.parse import quote

from bson import ObjectId

from tahmeed.models.transaction import Transaction
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document
from tahmeed.services.diesel_amounts import apply_diesel_computed_fields, diesel_line_total
from tahmeed.services.diesel_currency import (
    LAKE_ZAMBIA_DIESEL_CURRENCIES,
    diesel_amount_by_currency_groups,
    diesel_record_currency,
)
from tahmeed.services.diesel_liters import parse_liters_from_description
from tahmeed.services.excel_dates import (
    format_excel_date,
    normalize_date_fields,
    parse_excel_date,
)
from tahmeed.services.ledger_sort import ledger_sort_clauses
from tahmeed.services.truck_format import stamp_truck_sort_key


_MASTER_SORT_ALLOWED = frozenset({
    "date", "description", "truck_number", "memo", "amount",
    "category_name", "item", "approver", "ownership", "ref_float", "receipt_status",
})

_TOLL_SORT_ALLOWED = frozenset({
    "transaction_date", "toll_date", "toll_plaza", "client_name", "card_no",
    "vehicle_reg", "vehicle_class", "tender_amount", "receipt_no", "device",
    "lane", "cashier_name", "import_date",
})

_PARKING_CONGO_SORT_ALLOWED = frozenset({
    "transaction_date", "payment_date", "ledger_id", "transaction_type",
    "amount", "running_balance", "cashier", "vehicle_no", "direction",
    "gate_in", "transaction_details", "import_date", "sn",
})

_ZAMBIA_PARKING_SORT_ALLOWED = frozenset({
    "transaction_date", "date", "transaction_type", "plate_num", "ticket_no",
    "debit", "credit", "balance", "heading_to", "import_date",
})

_DIESEL_FEED_SORT_ALLOWED = frozenset({
    "transaction_date", "date", "lpo_no", "do_sdo_no", "diesel_at",
    "ownership", "clients_name", "destinations", "truck_no", "ltrs",
    "price_per_ltr", "total_amount", "upload_label", "import_date",
})

_KIMVI_SORT_ALLOWED = frozenset({
    "expense_date", "date_str", "truck_no", "description", "amount_usd",
    "created_at", "row_index", "serial_no", "lpo_no",
})

_CONGO_SORT_ALLOWED = frozenset({
    "expense_date", "date_str", "truck_no", "description", "amount_usd",
    "created_at", "row_index", "serial_no", "lpo_no",
})

_AFRITRACK_SORT_ALLOWED = frozenset({
    "import_date", "period", "row_index", "truck", "days", "non_trans_days",
    "trans_days", "rate_per_day", "total_tahmeed", "total_invoice",
    "variance", "remarks",
})

_RAHNTECH_SORT_ALLOWED = frozenset({
    "transaction_date", "sales_date", "trip_number", "device_number",
    "truck_number", "driver_name", "do_number", "import_date", "sn",
})

_COMESA_SORT_ALLOWED = frozenset({
    "name", "card_no", "valid_from", "valid_to", "truck_reg", "premium", "month",
})

_THIRD_PARTY_SORT_ALLOWED = frozenset({
    "name", "reg_no", "premium", "vat", "total_premium", "month", "status",
})

_DIESEL_CASH_SORT_ALLOWED = frozenset({
    "date", "description", "truck_number", "memo", "amount",
    "category_name", "item", "approver", "ownership", "receipt_status",
})


def _date_range_clause(
    field: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Optional[dict]:
    """MongoDB clause for an optional inclusive From/To window on *field*."""
    if date_from is None and date_to is None:
        return None
    rng: dict = {}
    if date_from is not None:
        rng["$gte"] = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
    if date_to is not None:
        rng["$lte"] = date_to.replace(hour=23, minute=59, second=59, microsecond=0)
    return {field: rng}


def _with_date_range(
    query: dict,
    field: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Return *query* with an optional inclusive date window on *field*."""
    clause = _date_range_clause(field, date_from, date_to)
    if not clause:
        return query
    merged = dict(query)
    merged.update(clause)
    return merged


# ── transactions ──────────────────────────────────────────────────────────────

# Newest calendar day first; within a day, Daily Register / WhatsApp sequence.
UNVERIFIED_INBOX_SORT = [("date", -1), ("day_order", 1), ("created_at", 1)]


def _tx_from_api(document: dict) -> Transaction:
    return Transaction.from_doc(desktop_document(document))


def _csv_param(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip()


def _oids_from_api_items(items) -> List[ObjectId]:
    out: List[ObjectId] = []
    for raw in items or []:
        if raw is None:
            continue
        text = str(raw)
        if ObjectId.is_valid(text):
            out.append(ObjectId(text))
    return out


def _iso_dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _trash_params(
    *,
    search: str = "",
    truck: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: Optional[int] = None,
    skip: Optional[int] = None,
) -> dict:
    params: dict = {}
    if search:
        params["search"] = search
    if truck:
        params["truck"] = truck
    if date_from is not None:
        params["date_from"] = _iso_dt(date_from)
    if date_to is not None:
        params["date_to"] = _iso_dt(date_to)
    if limit is not None:
        params["limit"] = limit
    if skip is not None:
        params["skip"] = skip
    return params


def _inbox_params(
    *,
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    edited: Optional[bool] = None,
    item="",
    description="",
    limit: Optional[int] = None,
    skip: Optional[int] = None,
) -> dict:
    params: dict = {}
    if search:
        params["search"] = search
    if truck:
        params["truck"] = truck
    if cashier_id is not None:
        params["cashier_id"] = str(cashier_id)
    if date_from is not None:
        params["date_from"] = _iso_dt(date_from)
    if date_to is not None:
        params["date_to"] = _iso_dt(date_to)
    if edited is True:
        params["edited"] = "true"
    elif edited is False:
        params["edited"] = "false"
    item_csv = _csv_param(item)
    if item_csv:
        params["item"] = item_csv
    desc_csv = _csv_param(description)
    if desc_csv:
        params["description"] = desc_csv
    if limit is not None:
        params["limit"] = limit
    if skip is not None:
        params["skip"] = skip
    return params


def _master_params(
    *,
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: Optional[str] = None,
    sort_asc: Optional[bool] = None,
    limit: Optional[int] = None,
    skip: Optional[int] = None,
    field: Optional[str] = None,
) -> dict:
    params: dict = {}
    if year is not None:
        params["year"] = int(year)
    if month:
        params["month"] = int(month)
    if search:
        params["search"] = search
    if truck:
        params["truck"] = truck
    if category:
        params["category"] = category
    if receipt and str(receipt).strip().lower() != "all":
        params["receipt"] = receipt
    if description:
        params["description"] = description
    if date_from is not None:
        params["date_from"] = _iso_dt(date_from)
    if date_to is not None:
        params["date_to"] = _iso_dt(date_to)
    if column_filters:
        cleaned = {
            str(k): [str(v) for v in (vals or []) if str(v).strip()]
            for k, vals in column_filters.items()
            if vals
        }
        if cleaned:
            params["column_filters"] = json.dumps(cleaned)
    if sort_field is not None:
        params["sort_field"] = sort_field
    if sort_asc is not None:
        params["sort_asc"] = "true" if sort_asc else "false"
    if limit is not None:
        params["limit"] = limit
    if skip is not None:
        params["skip"] = skip
    if field:
        params["field"] = field
    return params


def _jsonable_master_updates(updates: dict) -> dict:
    """Serialize ObjectId / datetime values for the master PATCH/bulk JSON body."""
    out: dict = {}
    for key, value in (updates or {}).items():
        if isinstance(value, ObjectId):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, date):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _category_report_path(category: str, suffix: str) -> str:
    name = quote((category or "").strip(), safe="")
    return f"v1/accountant/reports/category/{name}/{suffix}"


def _payee_report_path(payee: str, suffix: str) -> str:
    name = quote((payee or "").strip(), safe="")
    return f"v1/accountant/reports/payee/{name}/{suffix}"


def _category_report_params(
    *,
    description: str = "",
    descriptions: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: Optional[str] = None,
    sort_asc: Optional[bool] = None,
    limit: Optional[int] = None,
    skip: Optional[int] = None,
    field: Optional[str] = None,
) -> dict:
    params: dict = {}
    if description:
        params["description"] = description
    if descriptions:
        csv = _csv_param(descriptions)
        if csv:
            params["descriptions"] = csv
    if date_from is not None:
        params["date_from"] = _iso_dt(date_from)
    if date_to is not None:
        params["date_to"] = _iso_dt(date_to)
    if column_filters:
        cleaned = {
            str(k): [str(v) for v in (vals or []) if str(v).strip()]
            for k, vals in column_filters.items()
            if vals
        }
        if cleaned:
            params["column_filters"] = json.dumps(cleaned)
    if sort_field is not None:
        params["sort_field"] = sort_field
    if sort_asc is not None:
        params["sort_asc"] = "true" if sort_asc else "false"
    if limit is not None:
        params["limit"] = limit
    if skip is not None:
        params["skip"] = skip
    if field:
        params["field"] = field
    return params


def _totals_from_payload(payload: dict | None) -> dict:
    body = payload or {}
    return {
        "count": int(body.get("count") or 0),
        "tzs": float(body.get("tzs") or 0.0),
        "usd": float(body.get("usd") or 0.0),
    }


async def get_unverified_transactions(limit: int = 50, skip: int = 0) -> List[Transaction]:
    payload = await api_client.request(
        "GET",
        "v1/accountant/inbox",
        params={"limit": limit, "skip": skip},
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def get_verified_transactions(
    year: Optional[int] = None,
    month: Optional[int] = None,
    category: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
) -> List[Transaction]:
    params: dict = {"limit": limit, "skip": skip}
    if year is not None:
        params["year"] = year
    if month is not None:
        params["month"] = month
    if category:
        params["category"] = category
    payload = await api_client.request(
        "GET", "v1/accountant/verified", params=params
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def get_transactions_by_ids(tx_ids: List[ObjectId]) -> List[Transaction]:
    if not tx_ids:
        return []
    # Chunk so the ids query string stays within typical URL length limits.
    _CHUNK = 150
    out: List[Transaction] = []
    for i in range(0, len(tx_ids), _CHUNK):
        chunk = tx_ids[i : i + _CHUNK]
        payload = await api_client.request(
            "GET",
            "v1/transactions",
            params={"ids": ",".join(str(oid) for oid in chunk)},
        )
        out.extend(_tx_from_api(doc) for doc in payload.get("items") or [])
    return out


async def approve_transaction(tx_id: ObjectId, accountant_id: ObjectId) -> bool:
    # Actor comes from the API session; accountant_id kept for call-site compatibility.
    _ = accountant_id
    payload = await api_client.request(
        "POST", f"v1/accountant/transactions/{tx_id}/approve"
    )
    return bool(payload.get("ok"))


async def reject_transaction(
    tx_id: ObjectId,
    reason: str,
    *,
    actor_id: ObjectId | None = None,
) -> bool:
    _ = actor_id
    payload = await api_client.request(
        "POST",
        f"v1/accountant/transactions/{tx_id}/reject",
        json={"reason": reason or ""},
    )
    return bool(payload.get("ok"))


def _normalize_user_id(value: ObjectId | str | None) -> ObjectId | None:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return None


async def bulk_reject_transactions(
    tx_ids: List[ObjectId],
    reason: str,
    *,
    actor_id: ObjectId | None = None,
) -> int:
    _ = actor_id
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/accountant/transactions/bulk-reject",
        json={"ids": [str(oid) for oid in tx_ids], "reason": reason or ""},
    )
    return int(payload.get("updated") or 0)


async def get_cashier_names(cashier_ids: List[ObjectId]) -> Dict[ObjectId, str]:
    if not cashier_ids:
        return {}

    id_by_original: Dict[ObjectId | str, ObjectId] = {}
    query_ids: List[ObjectId] = []
    for cid in cashier_ids:
        oid = _normalize_user_id(cid)
        if oid is None:
            continue
        id_by_original[cid] = oid
        query_ids.append(oid)

    if not query_ids:
        return {}

    unique = list({str(oid) for oid in query_ids})
    payload = await api_client.request(
        "GET",
        "v1/accountant/cashier-names",
        params={"ids": ",".join(unique)},
    )
    names_raw = payload.get("names") or {}
    names_by_oid: Dict[ObjectId, str] = {}
    for raw_id, name in names_raw.items():
        if ObjectId.is_valid(str(raw_id)):
            names_by_oid[ObjectId(str(raw_id))] = name or "Unknown cashier"

    return {
        cid: names_by_oid.get(id_by_original[cid], "Unknown cashier")
        for cid in cashier_ids
        if cid in id_by_original
    }


async def get_pending_count() -> int:
    """Sidebar / inbox badge: unverified + deletion + restore requests."""
    payload = await api_client.request("GET", "v1/accountant/counts/pending")
    return int(payload.get("count") or 0)


def _sum_amount_if_currency(*codes: str, absolute: bool = False) -> dict:
    """Mongo $sum that only includes rows whose currency matches one of *codes*."""
    amount_expr: Any = {"$abs": "$amount"} if absolute else "$amount"
    return {
        "$sum": {
            "$cond": [
                {
                    "$in": [
                        {"$toUpper": {"$ifNull": ["$currency", ""]}},
                        list(codes),
                    ]
                },
                amount_expr,
                0,
            ]
        }
    }


async def _overview_zmw_feed_month_totals(year: int) -> Dict[int, float]:
    """Per-month ZMW from Toll Plaza + Zambia Parking (same date filters as feed tabs)."""

    async def _month(m: int) -> tuple[int, float]:
        toll, zambia = await asyncio.gather(
            get_toll_plaza_all_totals(year=year, month=m),
            get_zambia_parking_all_totals(year=year, month=m),
        )
        total = float(toll.get("total_zmw", 0) or 0) + float(
            zambia.get("total_debit", 0) or 0
        )
        return m, total

    pairs = await asyncio.gather(*[_month(m) for m in range(1, 13)])
    return dict(pairs)


async def _overview_usd_feed_month_totals(year: int) -> Dict[int, float]:
    """Per-month USD from separate-expense feeds (all except Toll Plaza / Zambia ZMW).

    Congo / Kimvi contribute money-out; Parking Congo and Afritrack contribute
    their spend totals. Toll Plaza stays in the ZMW feed merge only.
    """

    async def _month(m: int) -> tuple[int, float]:
        congo, kimvi, parking, afritrack = await asyncio.gather(
            get_congo_all_totals(year=year, month=m),
            get_kimvi_all_totals(year=year, month=m),
            get_parking_congo_all_totals(year=year, month=m),
            get_afritrack_all_totals(year=year, month=m),
        )
        total = (
            float(congo.get("money_out", 0) or 0)
            + float(kimvi.get("money_out", 0) or 0)
            + float(parking.get("amount", 0) or 0)
            + float(afritrack.get("total_invoice", 0) or 0)
        )
        return m, total

    pairs = await asyncio.gather(*[_month(m) for m in range(1, 13)])
    return dict(pairs)


def _overview_int_months(raw: dict | None) -> Dict[int, dict]:
    """Convert API month-totals keys (often strings) to int keys for the UI."""
    out: Dict[int, dict] = {}
    for key, row in (raw or {}).items():
        try:
            month = int(key)
        except (TypeError, ValueError):
            continue
        out[month] = {
            "tzs": float((row or {}).get("tzs") or 0.0),
            "usd": float((row or {}).get("usd") or 0.0),
            "zmw": float((row or {}).get("zmw") or 0.0),
            "count": int((row or {}).get("count") or 0),
        }
    return out


def _deserialize_fuel_liters_series(
    raw: dict | None,
) -> Dict[str, Dict[date, float]]:
    """Convert API ISO date keys back to ``date`` keys for chart bucketing."""
    store = empty_fuel_liters_daily()
    for source, days in (raw or {}).items():
        if source not in store or not isinstance(days, dict):
            continue
        for day_key, liters in days.items():
            day: Optional[date] = None
            if isinstance(day_key, date) and not isinstance(day_key, datetime):
                day = day_key
            elif isinstance(day_key, datetime):
                day = day_key.date()
            elif isinstance(day_key, str):
                try:
                    day = date.fromisoformat(day_key[:10])
                except ValueError:
                    continue
            if day is None:
                continue
            try:
                qty = float(liters or 0.0)
            except (TypeError, ValueError):
                continue
            if qty > 0:
                store[source][day] = store[source].get(day, 0.0) + qty
    return store


async def get_overview_kpis(year: Optional[int] = None) -> dict:
    """Aggregate real counts for the Overview KPI cards (TZS / USD / ZMW).

    USD/ZMW from imported separate-expense feeds are merged by
    ``get_overview_dashboard`` so feed aggregations run once per refresh.
    """
    params: dict = {}
    if year is not None:
        params["year"] = int(year)
    payload = await api_client.request(
        "GET", "v1/accountant/overview/kpis", params=params
    )
    return {
        "pending_count": int(payload.get("pending_count") or 0),
        "master_count": int(payload.get("master_count") or 0),
        "verified_this_month": int(payload.get("verified_this_month") or 0),
        "submitted_this_month": int(payload.get("submitted_this_month") or 0),
        "total_tzs_ytd": float(payload.get("total_tzs_ytd") or 0.0),
        "total_usd_ytd": float(payload.get("total_usd_ytd") or 0.0),
        "total_zmw_ytd": float(payload.get("total_zmw_ytd") or 0.0),
    }


async def get_overview_category_breakdown(year: int, top_n: int = 4) -> List[dict]:
    """Top expense categories with per-currency verified spend for a fiscal year."""
    payload = await api_client.request(
        "GET",
        "v1/accountant/overview/categories",
        params={"year": int(year), "top_n": int(top_n)},
    )
    items = []
    for row in payload.get("items") or []:
        items.append({
            "name": row.get("name") or "Uncategorised",
            "tzs": float(row.get("tzs") or 0.0),
            "usd": float(row.get("usd") or 0.0),
            "zmw": float(row.get("zmw") or 0.0),
        })
    return items


async def get_overview_receipt_breakdown(year: int) -> dict:
    """Receipt status counts for verified transactions in a fiscal year."""
    payload = await api_client.request(
        "GET",
        "v1/accountant/overview/receipts",
        params={"year": int(year)},
    )
    return {
        "received": int(payload.get("received") or 0),
        "pending": int(payload.get("pending") or 0),
        "missing": int(payload.get("missing") or 0),
        "total": int(payload.get("total") or 0),
    }


# ── Overview fuel litres (stations + Diesel Cash) ────────────────────────────

FUEL_LITERS_PERIODS = ("month", "3m", "year")
FUEL_LITERS_SERIES: Tuple[Tuple[str, str], ...] = (
    ("diesel_cash", "Diesel Cash"),
    ("infinity", "Infinity"),
    ("lake_zambia", "Lake Zambia"),
    ("lake_tunduma", "Lake Tunduma"),
    ("gbp_diesel", "GBP Diesel"),
)


def empty_fuel_liters_daily() -> Dict[str, Dict[date, float]]:
    return {key: {} for key, _ in FUEL_LITERS_SERIES}


def add_fuel_liters_day(
    store: Dict[str, Dict[date, float]],
    source: str,
    when: Any,
    liters: Optional[float],
) -> None:
    """Add a positive litre quantity onto one calendar day for *source*."""
    if source not in store or liters is None:
        return
    try:
        qty = float(liters)
    except (TypeError, ValueError):
        return
    if qty <= 0:
        return
    if isinstance(when, datetime):
        day = when.date()
    elif isinstance(when, date):
        day = when
    else:
        return
    bucket = store[source]
    bucket[day] = bucket.get(day, 0.0) + qty


def accumulate_diesel_cash_fuel_liters(
    docs: Iterable[dict],
    store: Dict[str, Dict[date, float]],
) -> None:
    """Parse litres from Diesel Cash descriptions into the daily store."""
    for doc in docs:
        liters = parse_liters_from_description(str(doc.get("description") or ""))
        add_fuel_liters_day(store, "diesel_cash", doc.get("date"), liters)


def _fuel_liters_as_of(year: int, today: date) -> date:
    if year == today.year:
        return today
    if year < today.year:
        return date(year, 12, 31)
    return date(year, 1, 1)


def _fuel_bucket_date(day: date, grain: str) -> date:
    if grain == "month":
        return date(day.year, day.month, 1)
    if grain == "week":
        return day - timedelta(days=day.weekday())
    return day


def fuel_liters_period_spec(
    period: str,
    year: int,
    today: Optional[date] = None,
) -> dict:
    """Labels, bucket keys, and date window for an Overview fuel-litres period."""
    today = today or date.today()
    period = period if period in FUEL_LITERS_PERIODS else "year"
    as_of = _fuel_liters_as_of(year, today)

    if period == "month":
        start = date(as_of.year, as_of.month, 1)
        keys = [date(as_of.year, as_of.month, day) for day in range(1, as_of.day + 1)]
        labels = [str(day) for day in range(1, as_of.day + 1)]
        subtitle = (
            f"Litres by day, {calendar.month_name[as_of.month]} {as_of.year}"
        )
        grain = "day"
        end = as_of
    elif period == "3m":
        start_month = as_of.month - 2
        start_year = as_of.year
        while start_month < 1:
            start_month += 12
            start_year -= 1
        start = date(start_year, start_month, 1)
        fy_start = date(year, 1, 1)
        if start < fy_start:
            start = fy_start
        end = as_of
        grain = "week"
        keys: List[date] = []
        labels = []
        cursor = start - timedelta(days=start.weekday())
        while cursor <= end:
            keys.append(cursor)
            label_day = start if cursor < start else cursor
            labels.append(f"{label_day.day} {calendar.month_abbr[label_day.month]}")
            cursor += timedelta(days=7)
        if start.month == as_of.month and start.year == as_of.year:
            subtitle = (
                f"Litres by week, {calendar.month_name[as_of.month]} {as_of.year}"
            )
        elif start.year == as_of.year:
            subtitle = (
                f"Litres by week, {calendar.month_abbr[start.month]} to "
                f"{calendar.month_abbr[as_of.month]} {as_of.year}"
            )
        else:
            subtitle = (
                f"Litres by week, {calendar.month_abbr[start.month]} {start.year} "
                f"to {calendar.month_abbr[as_of.month]} {as_of.year}"
            )
    else:
        grain = "month"
        start = date(year, 1, 1)
        end_month = as_of.month if as_of.year == year else 12
        end = date(year, end_month, calendar.monthrange(year, end_month)[1])
        if as_of.year == year:
            end = min(end, as_of)
        keys = [date(year, month, 1) for month in range(1, end_month + 1)]
        labels = [calendar.month_abbr[month] for month in range(1, end_month + 1)]
        if end_month == 1:
            subtitle = f"Litres by month, Jan {year}"
        elif end_month == 12:
            subtitle = f"Litres by month, {year}"
        else:
            subtitle = (
                f"Litres by month, Jan to {calendar.month_abbr[end_month]} {year}"
            )

    return {
        "period": period,
        "grain": grain,
        "start": start,
        "end": end,
        "keys": keys,
        "labels": labels,
        "subtitle": subtitle,
    }


def bucket_daily_fuel_liters(
    daily: Dict[str, Dict[date, float]],
    period: str,
    year: int,
    today: Optional[date] = None,
) -> dict:
    """Roll daily litres into the requested period for the line chart."""
    spec = fuel_liters_period_spec(period, year, today)
    index = {key: i for i, key in enumerate(spec["keys"])}
    series = []
    total = 0.0
    for key, label in FUEL_LITERS_SERIES:
        values = [0.0] * len(spec["keys"])
        for day, liters in (daily.get(key) or {}).items():
            if day < spec["start"] or day > spec["end"]:
                continue
            bucket = _fuel_bucket_date(day, spec["grain"])
            slot = index.get(bucket)
            if slot is None:
                continue
            values[slot] += float(liters or 0.0)
        total += sum(values)
        series.append({"key": key, "label": label, "values": values})
    return {
        "period": spec["period"],
        "grain": spec["grain"],
        "labels": spec["labels"],
        "keys": spec["keys"],
        "subtitle": spec["subtitle"],
        "series": series,
        "total": total,
    }


async def get_overview_fuel_liters(year: int) -> dict:
    """Daily litres for Diesel Cash and each fuel station in *year*."""
    payload = await api_client.request(
        "GET",
        "v1/accountant/overview/fuel-liters",
        params={"year": int(year)},
    )
    return {
        "year": int(payload.get("year") or year),
        "series": _deserialize_fuel_liters_series(payload.get("series")),
    }


async def get_overview_month_totals(year: int) -> Dict:
    """Per-month TZS / USD / ZMW totals for the Overview trend chart.

    Master ledger supplies TZS/USD/(any ZMW on transactions). Separate-expense
    USD feeds (Congo, Kimvi, Parking Congo, Afritrack) are added into USD;
    Toll Plaza and Zambia Parking feeds are added into ZMW.

    Returns ``{"months": {1: {...}, ...}, "feed_usd_ytd": float, "feed_zmw_ytd": float}``.
    """
    payload = await api_client.request(
        "GET",
        "v1/accountant/overview/month-totals",
        params={"year": int(year)},
    )
    months = _overview_int_months(payload.get("months"))
    for month in range(1, 13):
        months.setdefault(month, {"tzs": 0.0, "usd": 0.0, "zmw": 0.0, "count": 0})
    return {
        "months": months,
        "feed_usd_ytd": float(payload.get("feed_usd_ytd") or 0.0),
        "feed_zmw_ytd": float(payload.get("feed_zmw_ytd") or 0.0),
    }


async def get_overview_dashboard(year: int) -> dict:
    """All data needed by the accountant Overview tab."""
    payload = await api_client.request(
        "GET",
        "v1/accountant/overview",
        params={"year": int(year)},
    )
    kpis = dict(payload.get("kpis") or {})
    fuel = payload.get("fuel_liters") or {}
    return {
        "kpis": {
            "pending_count": int(kpis.get("pending_count") or 0),
            "master_count": int(kpis.get("master_count") or 0),
            "verified_this_month": int(kpis.get("verified_this_month") or 0),
            "submitted_this_month": int(kpis.get("submitted_this_month") or 0),
            "total_tzs_ytd": float(kpis.get("total_tzs_ytd") or 0.0),
            "total_usd_ytd": float(kpis.get("total_usd_ytd") or 0.0),
            "total_zmw_ytd": float(kpis.get("total_zmw_ytd") or 0.0),
            "supplier_tzs_ytd": float(kpis.get("supplier_tzs_ytd") or 0.0),
            "supplier_usd_ytd": float(kpis.get("supplier_usd_ytd") or 0.0),
        },
        "month_totals": _overview_int_months(payload.get("month_totals")),
        "categories": list(payload.get("categories") or []),
        "fuel_liters": {
            "year": int(fuel.get("year") or year),
            "series": _deserialize_fuel_liters_series(fuel.get("series")),
        },
        "recent": [_tx_from_api(doc) for doc in payload.get("recent") or []],
    }


# ── Inbox filtered queries ────────────────────────────────────────────────────

def _normalize_multi_filter(value) -> List[str]:
    """Accept a single string or a list of strings; empty → no filter."""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    out: List[str] = []
    for v in value:
        s = str(v).strip()
        if s:
            out.append(s)
    return out


def _append_text_filters(
    and_clauses: list,
    *,
    search: str = "",
    item="",
    description="",
) -> None:
    if search.strip():
        clause = _feed_search_clause(
            search,
            ("truck_number",),
            ("description", "item", "category_name"),
        )
        if clause:
            and_clauses.append(clause)
    descs = _normalize_multi_filter(description)
    if descs:
        if isinstance(description, str):
            # Legacy free-text: substring match
            and_clauses.append({
                "description": {
                    "$regex": re.escape(descs[0]),
                    "$options": "i",
                },
            })
        else:
            # Multi-select: exact match on any selected description
            and_clauses.append({"$or": [
                {"description": {"$regex": f"^{re.escape(d)}$", "$options": "i"}}
                for d in descs
            ]})
    items = _normalize_multi_filter(item)
    if items:
        item_ors: list = []
        for it in items:
            esc = re.escape(it)
            item_ors.append({"item": {"$regex": f"^{esc}$", "$options": "i"}})
            item_ors.append({"category_name": {"$regex": f"^{esc}$", "$options": "i"}})
        and_clauses.append({"$or": item_ors})


def _build_inbox_query(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    edited: Optional[bool] = None,
    item="",
    description="",
) -> dict:
    # Only rows submitted from the register (or legacy docs without the field).
    query: dict = {
        "verified": False,
        "rejected": {"$ne": True},
        "$or": [
            {"register_status": "submitted"},
            {"register_status": {"$exists": False}},
        ],
    }
    # edited=False  → "New" tab: fresh entries never flagged as edited
    #                 (matches both False and missing field on legacy docs)
    # edited=True   → "Edited" tab: rows the cashier changed after save
    # edited=None   → no edited filter (all unverified)
    if edited is True:
        query["edited_after_verification"] = True
    elif edited is False:
        query["edited_after_verification"] = {"$ne": True}

    and_clauses: list = []
    _append_text_filters(
        and_clauses, search=search, item=item, description=description,
    )
    if and_clauses:
        query["$and"] = and_clauses

    if truck.strip():
        query["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    if cashier_id:
        query["cashier_id"] = cashier_id
    if date_from or date_to:
        df: dict = {}
        if date_from:
            df["$gte"] = date_from
        if date_to:
            df["$lte"] = date_to
        # Edited tab: filter by when the cashier edited, so any-date rows still show.
        date_field = "last_edited_at" if edited is True else "date"
        query[date_field] = df
    return query


async def get_unverified_filtered(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 25,
    skip: int = 0,
    edited: Optional[bool] = None,
    item="",
    description="",
) -> List[Transaction]:
    params = _inbox_params(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        edited=edited,
        item=item,
        description=description,
        limit=limit,
        skip=skip,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/inbox", params=params
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def count_unverified_filtered(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    edited: Optional[bool] = None,
    item="",
    description="",
) -> int:
    params = _inbox_params(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        edited=edited,
        item=item,
        description=description,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/inbox/count", params=params
    )
    return int(payload.get("count") or 0)


# ── Edited-after-verification queries (accountant "Edited" sub-tab) ───────────

async def get_edited_transactions(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 25,
    skip: int = 0,
    item="",
    description="",
) -> List[Transaction]:
    """Rows the cashier edited after save (verified=False AND
    edited_after_verification=True). Sorted by most-recently-edited first."""
    params = _inbox_params(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
        limit=limit,
        skip=skip,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/inbox/edited", params=params
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def count_edited_transactions(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item="",
    description="",
) -> int:
    params = _inbox_params(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/inbox/edited/count", params=params
    )
    return int(payload.get("count") or 0)


async def get_edited_count() -> int:
    """Total edited rows awaiting re-approval (for the badge)."""
    payload = await api_client.request("GET", "v1/accountant/counts/edited")
    return int(payload.get("count") or 0)


# ── Deletion-request queries (accountant "Deleted" sub-tab) ───────────────────

def _build_deletion_query(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item="",
    description="",
) -> dict:
    query: dict = {"deletion_requested": True, "trashed": {"$ne": True}}
    and_clauses: list = []
    _append_text_filters(
        and_clauses, search=search, item=item, description=description,
    )
    if and_clauses:
        query["$and"] = and_clauses
    if truck.strip():
        query["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    if cashier_id:
        query["cashier_id"] = cashier_id
    if date_from or date_to:
        df: dict = {}
        if date_from:
            df["$gte"] = date_from
        if date_to:
            df["$lte"] = date_to
        # Prefer when the cashier requested deletion so any-date rows still show.
        query["deletion_requested_at"] = df
    return query


async def get_deletion_requested_filtered(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 25,
    skip: int = 0,
    item="",
    description="",
) -> List[Transaction]:
    """Approved rows the cashier asked to permanently remove."""
    params = _inbox_params(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
        limit=limit,
        skip=skip,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/deletions", params=params
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def count_deletion_requested_filtered(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item="",
    description="",
) -> int:
    params = _inbox_params(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/deletions/count", params=params
    )
    return int(payload.get("count") or 0)


async def get_deletion_requested_count() -> int:
    """Total deletion requests awaiting accountant confirm/restore."""
    payload = await api_client.request("GET", "v1/accountant/counts/deletions")
    return int(payload.get("count") or 0)


async def get_deletion_requested_trucks() -> List[str]:
    payload = await api_client.request(
        "GET", "v1/accountant/deletions/meta/trucks"
    )
    return list(payload.get("items") or [])


async def get_deletion_requested_cashier_ids() -> List[ObjectId]:
    payload = await api_client.request(
        "GET", "v1/accountant/deletions/meta/cashiers"
    )
    return _oids_from_api_items(payload.get("items") or [])


async def get_deletion_requested_items(
    descriptions: Optional[List[str]] = None,
) -> List[str]:
    params: dict = {}
    csv = _csv_param(descriptions or [])
    if csv:
        params["descriptions"] = csv
    payload = await api_client.request(
        "GET", "v1/accountant/deletions/meta/items", params=params
    )
    return list(payload.get("items") or [])


async def get_deletion_requested_descriptions(
    items: Optional[List[str]] = None,
) -> List[str]:
    params: dict = {}
    csv = _csv_param(items or [])
    if csv:
        params["items"] = csv
    payload = await api_client.request(
        "GET", "v1/accountant/deletions/meta/descriptions", params=params
    )
    return list(payload.get("items") or [])


async def confirm_deletion(
    tx_id: ObjectId,
    *,
    actor_id: ObjectId | None = None,
) -> bool:
    """Move a deletion-requested approved row into Trash (soft delete).

    Pending-edit clones for this original are removed on the API.
    """
    _ = actor_id
    payload = await api_client.request(
        "POST", f"v1/accountant/deletions/{tx_id}/confirm"
    )
    return bool(payload.get("ok"))


async def bulk_confirm_deletions(
    tx_ids: List[ObjectId],
    *,
    actor_id: ObjectId | None = None,
) -> int:
    _ = actor_id
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/accountant/deletions/bulk-confirm",
        json={"ids": [str(oid) for oid in tx_ids]},
    )
    return int(payload.get("updated") or 0)


async def restore_deletion(tx_id: ObjectId) -> bool:
    """Clear deletion request flags so the row returns to Master / register."""
    payload = await api_client.request(
        "POST", f"v1/accountant/deletions/{tx_id}/restore"
    )
    return bool(payload.get("ok"))


async def bulk_restore_deletions(tx_ids: List[ObjectId]) -> int:
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/accountant/deletions/bulk-restore",
        json={"ids": [str(oid) for oid in tx_ids]},
    )
    return int(payload.get("updated") or 0)


# ── Restore-request queries (accountant "Restore" sub-tab) ────────────────────

async def get_restore_requested_filtered(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 25,
    skip: int = 0,
    item="",
    description="",
) -> List[Transaction]:
    """Trashed rows the cashier asked to bring back."""
    params = _inbox_params(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
        limit=limit,
        skip=skip,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/restores", params=params
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def count_restore_requested_filtered(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item="",
    description="",
) -> int:
    params = _inbox_params(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/restores/count", params=params
    )
    return int(payload.get("count") or 0)


async def get_restore_requested_trucks() -> List[str]:
    payload = await api_client.request(
        "GET", "v1/accountant/restores/meta/trucks"
    )
    return list(payload.get("items") or [])


async def get_restore_requested_cashier_ids() -> List[ObjectId]:
    payload = await api_client.request(
        "GET", "v1/accountant/restores/meta/cashiers"
    )
    return _oids_from_api_items(payload.get("items") or [])


async def get_restore_requested_items(
    descriptions: Optional[List[str]] = None,
) -> List[str]:
    params: dict = {}
    csv = _csv_param(descriptions or [])
    if csv:
        params["descriptions"] = csv
    payload = await api_client.request(
        "GET", "v1/accountant/restores/meta/items", params=params
    )
    return list(payload.get("items") or [])


async def get_restore_requested_descriptions(
    items: Optional[List[str]] = None,
) -> List[str]:
    params: dict = {}
    csv = _csv_param(items or [])
    if csv:
        params["items"] = csv
    payload = await api_client.request(
        "GET", "v1/accountant/restores/meta/descriptions", params=params
    )
    return list(payload.get("items") or [])


async def confirm_restore(tx_id: ObjectId) -> bool:
    """Approve cashier restore — return row to Master / register."""
    payload = await api_client.request(
        "POST", f"v1/accountant/restores/{tx_id}/confirm"
    )
    return bool(payload.get("ok"))


async def reject_restore(tx_id: ObjectId) -> bool:
    """Deny cashier restore — keep row in Trash."""
    payload = await api_client.request(
        "POST", f"v1/accountant/restores/{tx_id}/reject"
    )
    return bool(payload.get("ok"))


async def bulk_confirm_restores(tx_ids: List[ObjectId]) -> int:
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/accountant/restores/bulk-confirm",
        json={"ids": [str(oid) for oid in tx_ids]},
    )
    return int(payload.get("updated") or 0)


async def bulk_reject_restores(tx_ids: List[ObjectId]) -> int:
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/accountant/restores/bulk-reject",
        json={"ids": [str(oid) for oid in tx_ids]},
    )
    return int(payload.get("updated") or 0)


# ── Trash (soft-deleted confirmed removals) ───────────────────────────────────

async def get_trashed_transactions(
    search: str = "",
    truck: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 100,
    skip: int = 0,
) -> List[Transaction]:
    params = _trash_params(
        search=search,
        truck=truck,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        skip=skip,
    )
    payload = await api_client.request("GET", "v1/accountant/trash", params=params)
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def count_trashed_transactions(
    search: str = "",
    truck: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    params = _trash_params(
        search=search, truck=truck, date_from=date_from, date_to=date_to
    )
    payload = await api_client.request(
        "GET", "v1/accountant/trash/count", params=params
    )
    return int(payload.get("count") or 0)


async def get_trash_count() -> int:
    payload = await api_client.request("GET", "v1/accountant/counts/trash")
    return int(payload.get("count") or 0)


async def restore_from_trash(tx_id: ObjectId) -> bool:
    """Restore a trashed row back to Master / register."""
    payload = await api_client.request(
        "POST", f"v1/accountant/trash/{tx_id}/restore"
    )
    return bool(payload.get("ok"))


async def bulk_restore_from_trash(tx_ids: List[ObjectId]) -> int:
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/accountant/trash/bulk-restore",
        json={"ids": [str(oid) for oid in tx_ids]},
    )
    return int(payload.get("updated") or 0)


async def permanently_delete_trashed(
    tx_id: ObjectId,
    *,
    actor_id: ObjectId | None = None,
) -> bool:
    """Hard-delete a row that is already in Trash."""
    _ = actor_id
    payload = await api_client.request(
        "DELETE", f"v1/accountant/trash/{tx_id}"
    )
    return bool(payload.get("ok"))


async def bulk_permanently_delete_trashed(
    tx_ids: List[ObjectId],
    *,
    actor_id: ObjectId | None = None,
) -> int:
    _ = actor_id
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/accountant/trash/bulk-delete",
        json={"ids": [str(oid) for oid in tx_ids]},
    )
    return int(payload.get("deleted") or 0)


# ── Rejected queries ──────────────────────────────────────────────────────────

def _build_rejected_query(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item="",
    description="",
) -> dict:
    query: dict = {"rejected": True, "discarded": {"$ne": True}}
    and_clauses: list = []
    _append_text_filters(
        and_clauses, search=search, item=item, description=description,
    )
    if and_clauses:
        query["$and"] = and_clauses
    if truck.strip():
        query["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}
    if cashier_id:
        query["cashier_id"] = cashier_id
    if date_from or date_to:
        df: dict = {}
        if date_from:
            df["$gte"] = date_from
        if date_to:
            df["$lte"] = date_to
        query["date"] = df
    return query


async def get_rejected_transactions(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 25,
    skip: int = 0,
    item="",
    description="",
) -> List[Transaction]:
    params = _inbox_params(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
        limit=limit,
        skip=skip,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/rejected", params=params
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def count_rejected_transactions(
    search: str = "",
    truck: str = "",
    cashier_id: Optional[ObjectId] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item="",
    description="",
) -> int:
    params = _inbox_params(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/rejected/count", params=params
    )
    return int(payload.get("count") or 0)


async def get_rejected_count() -> int:
    """Total rejected entries across all cashiers (for the badge)."""
    payload = await api_client.request("GET", "v1/accountant/counts/rejected")
    return int(payload.get("count") or 0)


async def return_to_inbox(tx_id: ObjectId) -> bool:
    """Undo a rejection — clears rejected flag so the entry reappears in its original inbox tab."""
    payload = await api_client.request(
        "POST", f"v1/accountant/transactions/{tx_id}/return-to-inbox"
    )
    return bool(payload.get("ok"))


# ── Re-approve (edited entries) ───────────────────────────────────────────────

async def re_approve_transaction(tx_id: ObjectId, accountant_id: ObjectId) -> bool:
    """Approve an edited row.

    Pending-edit cascade (copy onto original + delete clone) runs on the API.
    ``accountant_id`` is kept for call-site compatibility; the API uses the session user.
    """
    _ = accountant_id
    payload = await api_client.request(
        "POST", f"v1/accountant/transactions/{tx_id}/re-approve"
    )
    return bool(payload.get("ok"))


async def bulk_re_approve_transactions(
    tx_ids: List[ObjectId], accountant_id: ObjectId
) -> int:
    _ = accountant_id
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/accountant/transactions/bulk-re-approve",
        json={"ids": [str(oid) for oid in tx_ids]},
    )
    return int(payload.get("updated") or 0)


def _dates_from_meta_payload(payload: dict) -> List[date]:
    """Parse ``{items: [{date: YYYY-MM-DD}, ...]}`` from verify meta endpoints."""
    out: List[date] = []
    for row in payload.get("items") or []:
        raw = row.get("date") if isinstance(row, dict) else row
        text = str(raw or "").strip()[:10]
        if not text:
            continue
        try:
            out.append(date.fromisoformat(text))
        except ValueError:
            continue
    return out


async def get_unverified_available_dates(
    *, edited: Optional[bool] = False,
) -> List[date]:
    """Expense days present in New (edited=False) or Edited (edited=True) queues."""
    params: dict = {}
    if edited is True:
        params["edited"] = "true"
    elif edited is False:
        params["edited"] = "false"
    payload = await api_client.request(
        "GET", "v1/accountant/inbox/meta/dates", params=params
    )
    return _dates_from_meta_payload(payload)


async def get_rejected_available_dates() -> List[date]:
    payload = await api_client.request("GET", "v1/accountant/rejected/meta/dates")
    return _dates_from_meta_payload(payload)


async def get_deletion_requested_available_dates() -> List[date]:
    payload = await api_client.request("GET", "v1/accountant/deletions/meta/dates")
    return _dates_from_meta_payload(payload)


async def get_restore_requested_available_dates() -> List[date]:
    payload = await api_client.request("GET", "v1/accountant/restores/meta/dates")
    return _dates_from_meta_payload(payload)


async def get_unverified_trucks() -> List[str]:
    payload = await api_client.request("GET", "v1/accountant/inbox/meta/trucks")
    return list(payload.get("items") or [])


def _inbox_base_query(*, rejected: bool = False) -> dict:
    if rejected:
        return {"rejected": True, "discarded": {"$ne": True}}
    return {
        "verified": False,
        "rejected": {"$ne": True},
        "$or": [
            {"register_status": "submitted"},
            {"register_status": {"$exists": False}},
        ],
    }


def _apply_cascade_filters(
    base: dict,
    *,
    items: Optional[List[str]] = None,
    descriptions: Optional[List[str]] = None,
) -> dict:
    """Narrow a distinct-options query by the other cascading filter."""
    query = dict(base)
    and_clauses: list = []
    _append_text_filters(
        and_clauses,
        item=items or [],
        description=descriptions or [],
    )
    if and_clauses:
        existing = query.pop("$and", None)
        if existing:
            and_clauses = list(existing) + and_clauses
        query["$and"] = and_clauses
    return query


async def get_unverified_items(
    descriptions: Optional[List[str]] = None,
) -> List[str]:
    """Distinct item / category names for Verify filter dropdowns."""
    params: dict = {}
    csv = _csv_param(descriptions or [])
    if csv:
        params["descriptions"] = csv
    payload = await api_client.request(
        "GET", "v1/accountant/inbox/meta/items", params=params
    )
    return list(payload.get("items") or [])


async def get_unverified_descriptions(
    items: Optional[List[str]] = None,
) -> List[str]:
    """Distinct descriptions on unverified inbox rows (optionally scoped by items)."""
    params: dict = {}
    csv = _csv_param(items or [])
    if csv:
        params["items"] = csv
    payload = await api_client.request(
        "GET", "v1/accountant/inbox/meta/descriptions", params=params
    )
    return list(payload.get("items") or [])


async def get_unverified_cashier_ids() -> List[ObjectId]:
    payload = await api_client.request("GET", "v1/accountant/inbox/meta/cashiers")
    return _oids_from_api_items(payload.get("items") or [])


async def get_rejected_trucks() -> List[str]:
    payload = await api_client.request(
        "GET", "v1/accountant/rejected/meta/trucks"
    )
    return list(payload.get("items") or [])


async def get_rejected_items(
    descriptions: Optional[List[str]] = None,
) -> List[str]:
    params: dict = {}
    csv = _csv_param(descriptions or [])
    if csv:
        params["descriptions"] = csv
    payload = await api_client.request(
        "GET", "v1/accountant/rejected/meta/items", params=params
    )
    return list(payload.get("items") or [])


async def get_rejected_descriptions(
    items: Optional[List[str]] = None,
) -> List[str]:
    params: dict = {}
    csv = _csv_param(items or [])
    if csv:
        params["items"] = csv
    payload = await api_client.request(
        "GET", "v1/accountant/rejected/meta/descriptions", params=params
    )
    return list(payload.get("items") or [])


async def get_rejected_cashier_ids() -> List[ObjectId]:
    payload = await api_client.request(
        "GET", "v1/accountant/rejected/meta/cashiers"
    )
    return _oids_from_api_items(payload.get("items") or [])


async def bulk_approve_transactions(
    tx_ids: List[ObjectId], accountant_id: ObjectId
) -> int:
    _ = accountant_id
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/accountant/transactions/bulk-approve",
        json={"ids": [str(oid) for oid in tx_ids]},
    )
    return int(payload.get("updated") or 0)


async def update_transaction_category(
    tx_id: ObjectId,
    category_name: str,
    category_id: Optional[ObjectId] = None,
    *,
    payee: Optional[str] = None,
) -> bool:
    body: dict = {"category_name": category_name}
    if category_id is not None:
        body["category_id"] = str(category_id)
    if payee is not None:
        body["payee"] = " ".join((payee or "").strip().upper().split())
    payload = await api_client.request(
        "PATCH",
        f"v1/accountant/transactions/{tx_id}/category",
        json=body,
    )
    return bool(payload.get("ok"))


async def bulk_update_transaction_category(
    tx_ids: List[ObjectId],
    category_name: str,
    category_id: Optional[ObjectId] = None,
    *,
    payee: Optional[str] = None,
) -> int:
    """Assign the same item/category (and optional payee) to many transactions."""
    if not tx_ids:
        return 0
    body: dict = {
        "ids": [str(oid) for oid in tx_ids],
        "category_name": category_name,
    }
    if category_id is not None:
        body["category_id"] = str(category_id)
    if payee is not None:
        body["payee"] = " ".join((payee or "").strip().upper().split())
    payload = await api_client.request(
        "POST",
        "v1/accountant/transactions/bulk-category",
        json=body,
    )
    return int(payload.get("updated") or 0)


# ── Master Expenses in-place edits (accountant) ───────────────────────────────

_MASTER_EDIT_FIELDS = frozenset({
    "date", "description", "truck_number", "amount", "currency",
    "memo", "receipt_status", "notes_flag", "ref_float",
    "ownership", "approver", "item", "category_name", "category_id",
    "month", "year",
})

_MASTER_EDIT_FILTER = {
    "verified": True,
    "deletion_requested": {"$ne": True},
    "trashed": {"$ne": True},
}

_MONTH_ABBR = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _master_month_label(dt: datetime) -> str:
    return f"{_MONTH_ABBR[dt.month]} {str(dt.year)[-2:]}"


def _normalize_master_receipt(status: str) -> str:
    s = (status or "pending").strip().lower().replace(" ", "_")
    if s in ("no_receipt", "missing", "no", "n/a", "none"):
        return "no_receipt"
    if "received" in s or s in ("yes", "receipt", "rcvd"):
        return "received"
    if s == "pending" or "pending" in s:
        return "pending"
    return "pending"


def prepare_master_updates(updates: dict) -> dict:
    """Sanitize accountant Master edits and keep dependent fields in sync.

    Keeps item/category_name aligned, derives month/year from date, and
    syncs notes_flag with Ref_Float. Unknown keys are dropped.
    """
    clean: dict = {}
    for key, value in (updates or {}).items():
        if key in _MASTER_EDIT_FIELDS:
            clean[key] = value
    if not clean:
        return {}

    if "date" in clean and isinstance(clean["date"], datetime):
        dt = clean["date"]
        clean["month"] = _master_month_label(dt)
        clean["year"] = dt.year

    if "item" in clean and "category_name" not in clean:
        clean["category_name"] = clean["item"]
    if "category_name" in clean and "item" not in clean:
        clean["item"] = clean["category_name"]

    if "ref_float" in clean:
        ref = (clean.get("ref_float") or "").strip()
        clean["ref_float"] = ref
        clean["notes_flag"] = ref.upper() == "REFUND TO FLOAT"

    if "receipt_status" in clean:
        clean["receipt_status"] = _normalize_master_receipt(
            str(clean.get("receipt_status") or ""),
        )

    if "currency" in clean and clean["currency"] is not None:
        cur = str(clean["currency"]).strip().upper() or "TZS"
        clean["currency"] = "TZS" if cur in {"TZS", "TSH", "TZ"} else cur

    if "amount" in clean and clean["amount"] is not None:
        clean["amount"] = float(clean["amount"])

    for text_key in (
        "description", "truck_number", "memo", "ownership", "approver",
        "item", "category_name",
    ):
        if text_key in clean and clean[text_key] is not None:
            clean[text_key] = str(clean[text_key]).strip()

    if "truck_number" in clean:
        stamp_truck_sort_key(clean)

    return clean


async def update_master_transaction(
    tx_id: ObjectId,
    updates: dict,
    accountant_id: Optional[ObjectId] = None,
) -> bool:
    """Apply accountant edits on a verified Master row via API.

    Actor comes from the API session; *accountant_id* kept for call-site
    compatibility. Server prepares fields and writes the audit event.
    """
    _ = accountant_id
    body = _jsonable_master_updates(updates)
    if not body:
        return False
    payload = await api_client.request(
        "PATCH",
        f"v1/accountant/master/{tx_id}",
        json={"updates": body},
    )
    return bool(payload.get("ok"))


async def bulk_update_master_transactions(
    tx_ids: List[ObjectId],
    updates: dict,
    accountant_id: Optional[ObjectId] = None,
) -> int:
    """Apply the same field updates to many verified Master rows via API."""
    _ = accountant_id
    if not tx_ids:
        return 0
    body = _jsonable_master_updates(updates)
    if not body:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/accountant/master/bulk-update",
        json={
            "ids": [str(oid) for oid in tx_ids],
            "updates": body,
        },
    )
    return int(payload.get("updated") or 0)


async def bulk_patch_master_transactions(
    items: Sequence[Tuple[ObjectId, dict]],
    accountant_id: Optional[ObjectId] = None,
) -> dict:
    """Apply heterogeneous Master dirty-row edits in one bulk-patch call.

    Each item is ``(tx_id, updates)``. Returns ``{updated, matched, requested}``.
    """
    _ = accountant_id
    if not items:
        return {"updated": 0, "matched": 0, "requested": 0}
    body_items = []
    for tx_id, updates in items:
        if not tx_id or not updates:
            continue
        prepared = _jsonable_master_updates(updates)
        if not prepared:
            continue
        body_items.append({"id": str(tx_id), "updates": prepared})
    if not body_items:
        return {"updated": 0, "matched": 0, "requested": 0}
    payload = await api_client.request(
        "POST",
        "v1/accountant/master/bulk-patch",
        json={"items": body_items},
    )
    return {
        "updated": int(payload.get("updated") or 0),
        "matched": int(payload.get("matched") or 0),
        "requested": int(payload.get("requested") or len(body_items)),
    }


# ── Master Expenses Table queries ────────────────────────────────────────────

# Excel ▾ column filter keys (UI display values → Mongo clauses).
_MASTER_COL_FIELDS = frozenset({
    "date", "item", "description", "truck_number", "memo", "ref_float",
    "tzs", "usd", "receipt_status", "ownership", "approver", "cashier",
})

_RECEIPT_FILTER_MAP = {
    "received": ["received"],
    "pending": ["pending"],
    "missing": ["missing", "no_receipt"],
    "no_receipt": ["missing", "no_receipt"],
    "no receipt": ["missing", "no_receipt"],
}


def _master_period_clause(start: datetime, end: datetime) -> dict:
    """Master year/month tabs filter by Excel transaction ``date`` only.

    Register day (``import_primary_date``) is used to file/open an upload as a
    batch elsewhere. Mixing it into Master month filters pulled Jan-dated rows
    into December when the upload was filed on 31 Dec — so Master stays
    date-true here.
    """
    return {"date": {"$gte": start, "$lte": end}}


def _master_short_name(name: str) -> str:
    parts = (name or "").strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1][0]}."
    return (name or "").strip() or "—"


def _parse_master_filter_date(value: str, year: int) -> Optional[datetime]:
    """Parse Excel filter date labels like ``18 Jul`` or ``18 Jul 2026``."""
    text = (value or "").strip()
    if not text or text == "—":
        return None
    for fmt in ("%d %b %Y", "%d %B %Y", "%d %b", "%d %B", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            if fmt in ("%d %b", "%d %B"):
                parsed = datetime.strptime(f"{text} {year}", f"{fmt} %Y")
            else:
                parsed = datetime.strptime(text, fmt)
            return parsed.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            continue
    return None


def _parse_master_filter_amount(value: str) -> Optional[float]:
    text = (value or "").strip().replace(",", "").replace(" ", "")
    if not text or text == "—":
        return None
    # Allow a currency prefix like "USD 12.50" from non-TZS leftovers.
    for prefix in ("TZS", "TSH", "TZ", "USD", "ZMW", "ZK"):
        if text.upper().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    try:
        return float(text)
    except ValueError:
        return None


def _fmt_master_amount(amount: float, *, decimals: int) -> str:
    if decimals <= 0:
        return f"{amount:,.0f}"
    return f"{amount:,.{decimals}f}"


def _receipt_statuses_for_filter(values: Sequence[str]) -> List[str]:
    statuses: List[str] = []
    seen: Set[str] = set()
    for raw in values:
        key = (raw or "").strip().lower().replace("_", " ")
        mapped = _RECEIPT_FILTER_MAP.get(key)
        if mapped is None:
            compact = key.replace(" ", "_")
            mapped = _RECEIPT_FILTER_MAP.get(compact)
        if mapped is None and key in {"received", "pending", "missing", "no_receipt"}:
            mapped = [key if key != "missing" else "missing"]
        for st in mapped or []:
            if st not in seen:
                seen.add(st)
                statuses.append(st)
    return statuses


def _master_exact_ors(field: str, values: Sequence[str]) -> Optional[dict]:
    vals = [v for v in (_normalize_multi_filter(values)) if v != "—"]
    if not vals:
        return None
    return {"$or": [
        {field: {"$regex": f"^{re.escape(v)}$", "$options": "i"}} for v in vals
    ]}


def _append_master_column_filters(
    and_clauses: list,
    column_filters: Optional[Dict[str, Sequence[str]]],
    *,
    year: int,
    cashier_ids: Optional[Sequence[ObjectId]] = None,
) -> None:
    """Append Excel ▾ multi-select clauses (display values → DB match)."""
    if not column_filters:
        return
    for field, raw_vals in column_filters.items():
        if field not in _MASTER_COL_FIELDS:
            continue
        vals = [v for v in _normalize_multi_filter(raw_vals) if v != "—"]
        if not vals:
            continue

        if field == "date":
            day_ors: list = []
            for v in vals:
                day = _parse_master_filter_date(v, year)
                if day is None:
                    continue
                day_ors.append({
                    "date": {
                        "$gte": day,
                        "$lte": day.replace(hour=23, minute=59, second=59),
                    },
                })
            if day_ors:
                and_clauses.append({"$or": day_ors})
            continue

        if field == "item":
            item_ors: list = []
            for it in vals:
                esc = re.escape(it)
                item_ors.append({"item": {"$regex": f"^{esc}$", "$options": "i"}})
                item_ors.append({
                    "category_name": {"$regex": f"^{esc}$", "$options": "i"},
                })
            and_clauses.append({"$or": item_ors})
            continue

        if field == "receipt_status":
            statuses = _receipt_statuses_for_filter(vals)
            if statuses:
                and_clauses.append({"receipt_status": {"$in": statuses}})
            continue

        if field == "tzs":
            amounts = [a for a in (_parse_master_filter_amount(v) for v in vals) if a is not None]
            if amounts:
                and_clauses.append({
                    "currency": {"$in": ["TZS", "TSH", "TZ", "tzs", "tsh", "tz"]},
                    "amount": {"$in": amounts},
                })
            continue

        if field == "usd":
            amounts = [a for a in (_parse_master_filter_amount(v) for v in vals) if a is not None]
            if amounts:
                and_clauses.append({
                    "currency": {"$in": ["USD", "usd"]},
                    "amount": {"$in": amounts},
                })
            continue

        if field == "ref_float":
            ref_ors: list = []
            for v in vals:
                if v.strip().upper() == "REFUND TO FLOAT":
                    ref_ors.append({"notes_flag": True})
                    ref_ors.append({
                        "ref_float": {
                            "$regex": f"^{re.escape(v)}$",
                            "$options": "i",
                        },
                    })
                else:
                    ref_ors.append({
                        "ref_float": {
                            "$regex": f"^{re.escape(v)}$",
                            "$options": "i",
                        },
                    })
            if ref_ors:
                and_clauses.append({"$or": ref_ors})
            continue

        if field == "cashier":
            if cashier_ids:
                and_clauses.append({"cashier_id": {"$in": list(cashier_ids)}})
            continue

        clause = _master_exact_ors(field, vals)
        if clause:
            and_clauses.append(clause)


def _merge_and_clauses(query: dict, and_clauses: list) -> dict:
    if not and_clauses:
        return query
    out = dict(query)
    existing = out.pop("$and", None)
    merged = list(existing or []) + list(and_clauses)
    if len(merged) == 1 and "$or" not in out and not existing:
        # Single top-level clause can merge unless query already uses $or.
        only = merged[0]
        if len(only) == 1 and next(iter(only)) not in out:
            out.update(only)
            return out
    out["$and"] = merged
    return out


def _build_master_query(
    year: Optional[int],
    month: int,          # 0 = all, 1‑12 = month index
    search: str,
    truck: str,
    category: str,
    receipt: str,
    description: str = "",   # exact-ish sub-route filter (description contains)
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    cashier_ids: Optional[Sequence[ObjectId]] = None,
) -> dict:
    _year = year or date.today().year
    if month and 1 <= month <= 12:
        last_day = calendar.monthrange(_year, month)[1]
        start = datetime(_year, month, 1)
        end = datetime(_year, month, last_day, 23, 59, 59)
    else:
        start = datetime(_year, 1, 1)
        end = datetime(_year, 12, 31, 23, 59, 59)

    if date_from is not None:
        df = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
        start = max(start, df)
    if date_to is not None:
        dt = date_to.replace(hour=23, minute=59, second=59, microsecond=0)
        end = min(end, dt)

    query: dict = {
        "verified": True,
        "deletion_requested": {"$ne": True},
        "trashed": {"$ne": True},
        **_master_period_clause(start, end),
    }

    and_clauses: list = []

    # Free-text search matches description or truck number.
    if search.strip():
        clause = _feed_search_clause(
            search, ("truck_number",), ("description",),
        )
        if clause:
            and_clauses.append(clause)

    if description.strip():
        and_clauses.append({
            "description": {"$regex": re.escape(description.strip()), "$options": "i"},
        })

    if truck.strip():
        query["truck_number"] = truck.strip()
    if category.strip():
        query["category_name"] = {
            "$regex": f"^{re.escape(category.strip())}$",
            "$options": "i",
        }
    if receipt.strip() and receipt != "all":
        r = receipt.strip()
        if r == "missing":
            query["receipt_status"] = {"$in": ["missing", "no_receipt"]}
        else:
            query["receipt_status"] = r

    _append_master_column_filters(
        and_clauses,
        column_filters,
        year=_year,
        cashier_ids=cashier_ids,
    )
    return _merge_and_clauses(query, and_clauses)


def _exclude_supplier_payments(query: dict, supplier_names_lower: Sequence[str]) -> dict:
    """Keep supplier payments out of Master Expenses (still visible on Suppliers)."""
    if not supplier_names_lower:
        return query
    clause = {
        "$expr": {
            "$not": {
                "$in": [
                    {"$toLower": {"$ifNull": ["$category_name", ""]}},
                    list(supplier_names_lower),
                ]
            }
        }
    }
    return _merge_and_clauses(query, [clause])


class MasterPage(NamedTuple):
    """One Master Expenses page: rows + full match count + cashier names."""

    items: List[Transaction]
    total: int
    cashier_names: Dict[ObjectId, str]


def _master_cashier_names_from_payload(
    names_raw: Dict[str, Any],
    txs: Sequence[Transaction],
) -> Dict[ObjectId, str]:
    names_by_oid: Dict[ObjectId, str] = {}
    for raw_id, name in (names_raw or {}).items():
        oid = _normalize_user_id(raw_id)
        if oid is None:
            continue
        names_by_oid[oid] = (name or "").strip() or "Unknown cashier"
    out: Dict[ObjectId, str] = {}
    for tx in txs:
        cid = tx.cashier_id
        oid = _normalize_user_id(cid)
        if cid is None or oid is None:
            continue
        out[cid] = names_by_oid.get(oid, "Unknown cashier")
    return out


async def get_master_page(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: str = "date",
    sort_asc: bool = False,
    limit: int = 50,
    skip: int = 0,
) -> MasterPage:
    """Fetch one Master page with real total + cashier names (one HTTP call)."""
    params = _master_params(
        year=year,
        month=month,
        search=search,
        truck=truck,
        category=category,
        receipt=receipt,
        description=description,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
        sort_field=sort_field,
        sort_asc=sort_asc,
        limit=limit,
        skip=skip,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/master", params=params
    )
    items = [_tx_from_api(doc) for doc in payload.get("items") or []]
    # New API embeds cashier_names + real total. Old API only had page-length
    # total and no names — fall back so mixed desktop/API deploys still work.
    if "cashier_names" in payload:
        total = int(payload.get("total") or 0)
        names = _master_cashier_names_from_payload(
            payload.get("cashier_names") or {}, items,
        )
    else:
        cashier_ids = [tx.cashier_id for tx in items if tx.cashier_id]
        names = await get_cashier_names(cashier_ids) if cashier_ids else {}
        total = await count_master_transactions(
            year=year,
            month=month,
            search=search,
            truck=truck,
            category=category,
            receipt=receipt,
            description=description,
            date_from=date_from,
            date_to=date_to,
            column_filters=column_filters,
        )
    return MasterPage(items=items, total=total, cashier_names=names)


async def get_master_transactions(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: str = "date",
    sort_asc: bool = False,
    limit: int = 50,
    skip: int = 0,
) -> List[Transaction]:
    page = await get_master_page(
        year=year,
        month=month,
        search=search,
        truck=truck,
        category=category,
        receipt=receipt,
        description=description,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
        sort_field=sort_field,
        sort_asc=sort_asc,
        limit=limit,
        skip=skip,
    )
    return page.items


async def count_master_transactions(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
) -> int:
    params = _master_params(
        year=year,
        month=month,
        search=search,
        truck=truck,
        category=category,
        receipt=receipt,
        description=description,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/master/count", params=params
    )
    return int(payload.get("count") or 0)


async def get_master_totals(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
) -> dict:
    """Aggregate TZS + USD totals for the current filter (all pages, not just current)."""
    params = _master_params(
        year=year,
        month=month,
        search=search,
        truck=truck,
        category=category,
        receipt=receipt,
        description=description,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/master/totals", params=params
    )
    return {
        "tzs": float(payload.get("tzs") or 0.0),
        "usd": float(payload.get("usd") or 0.0),
    }


class MasterBootstrap(NamedTuple):
    """Background Master meta: footer totals + optional year filter chrome."""

    totals: dict
    month_totals: Optional[Dict[int, dict]]
    trucks: Optional[List[str]]
    categories: Optional[List[str]]


async def get_master_bootstrap(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    *,
    include_year_meta: bool = True,
) -> MasterBootstrap:
    """One RTT for Master footer totals (+ trucks/categories/month tabs)."""
    params = _master_params(
        year=year,
        month=month,
        search=search,
        truck=truck,
        category=category,
        receipt=receipt,
        description=description,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
    )
    params["include_year_meta"] = "true" if include_year_meta else "false"
    try:
        payload = await api_client.request(
            "GET", "v1/accountant/master/meta/bootstrap", params=params
        )
    except Exception:
        # Older API without bootstrap — fall back to parallel legacy calls.
        if include_year_meta:
            totals, month_data, trucks, cats = await asyncio.gather(
                get_master_totals(
                    year=year, month=month, search=search, truck=truck,
                    category=category, receipt=receipt, description=description,
                    date_from=date_from, date_to=date_to,
                    column_filters=column_filters,
                ),
                get_master_month_totals(int(year or date.today().year)),
                get_master_trucks(year),
                get_master_categories(year),
            )
            return MasterBootstrap(
                totals=totals,
                month_totals=month_data,
                trucks=trucks,
                categories=cats,
            )
        totals = await get_master_totals(
            year=year, month=month, search=search, truck=truck,
            category=category, receipt=receipt, description=description,
            date_from=date_from, date_to=date_to,
            column_filters=column_filters,
        )
        return MasterBootstrap(
            totals=totals,
            month_totals=None,
            trucks=None,
            categories=None,
        )

    totals_raw = payload.get("totals") or {}
    totals = {
        "tzs": float(totals_raw.get("tzs") or 0.0),
        "usd": float(totals_raw.get("usd") or 0.0),
    }
    if not include_year_meta:
        return MasterBootstrap(
            totals=totals,
            month_totals=None,
            trucks=None,
            categories=None,
        )
    months_raw = payload.get("month_totals")
    month_totals = (
        _overview_int_months(months_raw) if months_raw is not None else None
    )
    trucks_raw = payload.get("trucks")
    cats_raw = payload.get("categories")
    return MasterBootstrap(
        totals=totals,
        month_totals=month_totals,
        trucks=(
            [str(v) for v in trucks_raw if v]
            if isinstance(trucks_raw, list)
            else None
        ),
        categories=(
            [str(v) for v in cats_raw if v]
            if isinstance(cats_raw, list)
            else None
        ),
    )


async def get_category_lifetime_usage(category: str) -> dict:
    """Verified master usage for one item across all years (no FY window).

    Returns ``{"count": int, "tzs": float, "usd": float}``.
    """
    name = (category or "").strip()
    if not name:
        return {"count": 0, "tzs": 0.0, "usd": 0.0}
    payload = await api_client.request(
        "GET", _category_report_path(name, "lifetime")
    )
    return _totals_from_payload(payload)


def _category_report_reference_year(
    date_from: Optional[datetime],
    date_to: Optional[datetime],
) -> int:
    if date_from is not None:
        return date_from.year
    if date_to is not None:
        return date_to.year
    return date.today().year


def _category_report_query(
    category: str,
    *,
    description: str = "",
    descriptions: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    cashier_ids: Optional[Sequence[ObjectId]] = None,
) -> dict:
    """Verified / not-trashed rows for one item; optional filters + date range."""
    name = (category or "").strip()
    query: dict = {
        "verified": True,
        "deletion_requested": {"$ne": True},
        "trashed": {"$ne": True},
        "category_name": {
            "$regex": f"^{re.escape(name)}$",
            "$options": "i",
        },
    }
    and_clauses: list = []
    desc_arg: object = descriptions if descriptions else description
    if desc_arg:
        _append_text_filters(and_clauses, description=desc_arg)
    if date_from is not None or date_to is not None:
        date_clause: dict = {}
        if date_from is not None:
            date_clause["$gte"] = date_from.replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
        if date_to is not None:
            date_clause["$lte"] = date_to.replace(
                hour=23, minute=59, second=59, microsecond=0,
            )
        and_clauses.append({"date": date_clause})
    ref_year = _category_report_reference_year(date_from, date_to)
    _append_master_column_filters(
        and_clauses,
        column_filters,
        year=ref_year,
        cashier_ids=cashier_ids,
    )
    return _merge_and_clauses(query, and_clauses)


async def get_category_report_totals(
    category: str,
    *,
    description: str = "",
    descriptions: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
) -> dict:
    """Aggregate count / TZS / USD for an item report (all years by default)."""
    name = (category or "").strip()
    if not name:
        return {"count": 0, "tzs": 0.0, "usd": 0.0}
    params = _category_report_params(
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
    )
    payload = await api_client.request(
        "GET", _category_report_path(name, "totals"), params=params
    )
    return _totals_from_payload(payload)


async def get_categories_usage_totals(
    category_names: Sequence[str],
) -> Dict[str, dict]:
    """Lifetime totals for many items (Manage Items Amount column).

    Keys are lower-cased category names. Values: ``{count, tzs, usd}``.
    """
    names = sorted({(n or "").strip() for n in category_names if (n or "").strip()})
    if not names:
        return {}
    payload = await api_client.request(
        "POST",
        "v1/accountant/reports/categories/usage-totals",
        json={"names": names},
    )
    totals = payload.get("totals") or {}
    out: Dict[str, dict] = {}
    for key, row in totals.items():
        name = str(key or "").strip().lower()
        if not name:
            continue
        out[name] = {
            "count": int((row or {}).get("count") or 0),
            "tzs": float((row or {}).get("tzs") or 0.0),
            "usd": float((row or {}).get("usd") or 0.0),
        }
    return out


async def get_payees_usage_totals(payee_names: Sequence[str]) -> Dict[str, dict]:
    """Lifetime totals for many payees (Manage Payees Amount column).

    Keys are lower-cased payee names. Values: ``{count, tzs, usd}``.
    """
    names = sorted({(n or "").strip() for n in payee_names if (n or "").strip()})
    if not names:
        return {}
    payload = await api_client.request(
        "POST",
        "v1/accountant/reports/payees/usage-totals",
        json={"names": names},
    )
    totals = payload.get("totals") or {}
    out: Dict[str, dict] = {}
    for key, row in totals.items():
        name = str(key or "").strip().lower()
        if not name:
            continue
        out[name] = {
            "count": int((row or {}).get("count") or 0),
            "tzs": float((row or {}).get("tzs") or 0.0),
            "usd": float((row or {}).get("usd") or 0.0),
        }
    return out


async def get_payee_lifetime_usage(payee: str) -> dict:
    """Verified master usage for one payee across all years."""
    name = (payee or "").strip()
    if not name:
        return {"count": 0, "tzs": 0.0, "usd": 0.0}
    payload = await api_client.request(
        "GET", _payee_report_path(name, "lifetime")
    )
    return _totals_from_payload(payload)


async def get_payee_report_totals(
    payee: str,
    *,
    description: str = "",
    descriptions: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
) -> dict:
    """Aggregate count / TZS / USD for a payee report (all years by default)."""
    name = (payee or "").strip()
    if not name:
        return {"count": 0, "tzs": 0.0, "usd": 0.0}
    params = _category_report_params(
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
    )
    payload = await api_client.request(
        "GET", _payee_report_path(name, "totals"), params=params
    )
    return _totals_from_payload(payload)


async def get_payee_report_transactions(
    payee: str,
    *,
    description: str = "",
    descriptions: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: str = "date",
    sort_asc: bool = True,
    limit: int = 100,
    skip: int = 0,
) -> List[Transaction]:
    """Paginated verified master rows for a payee QuickReport."""
    name = (payee or "").strip()
    if not name:
        return []
    params = _category_report_params(
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
        sort_field=sort_field,
        sort_asc=sort_asc,
        limit=limit,
        skip=skip,
    )
    payload = await api_client.request(
        "GET",
        _payee_report_path(name, "transactions"),
        params=params,
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def get_payee_report_descriptions(
    payee: str,
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[str]:
    """Distinct transaction descriptions for a payee QuickReport toolbar filter."""
    name = (payee or "").strip()
    if not name:
        return []
    params = _category_report_params(date_from=date_from, date_to=date_to)
    payload = await api_client.request(
        "GET",
        _payee_report_path(name, "descriptions"),
        params=params,
    )
    return [str(v) for v in (payload.get("items") or []) if v]


async def get_payee_report_column_values(
    field: str,
    payee: str,
    *,
    description: str = "",
    descriptions: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
) -> List[str]:
    """Distinct Excel ▾ values for *field* on a payee QuickReport."""
    if field not in _MASTER_COL_FIELDS:
        return []
    name = (payee or "").strip()
    if not name:
        return []
    filters = {
        k: v for k, v in (column_filters or {}).items()
        if k != field and k in _MASTER_COL_FIELDS
    }
    params = _category_report_params(
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=filters or None,
        field=field,
    )
    payload = await api_client.request(
        "GET",
        _payee_report_path(name, "column-values"),
        params=params,
    )
    return [str(v) for v in (payload.get("items") or []) if v]


async def get_supplier_payments_totals(year: int) -> dict:
    """FY totals for all verified supplier payments (Manage Suppliers KPI cards).

    Matches payments whose ``category_name`` is an active supplier item.
    Returns ``{count, tzs, usd}``.
    """
    payload = await api_client.request(
        "GET",
        "v1/accountant/reports/supplier-payments/totals",
        params={"year": int(year)},
    )
    return _totals_from_payload(payload)


async def get_category_report_transactions(
    category: str,
    *,
    description: str = "",
    descriptions: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: str = "date",
    sort_asc: bool = True,
    limit: int = 100,
    skip: int = 0,
) -> List[Transaction]:
    """Paginated verified master rows for an item QuickReport (all years by default)."""
    name = (category or "").strip()
    if not name:
        return []
    params = _category_report_params(
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
        sort_field=sort_field,
        sort_asc=sort_asc,
        limit=limit,
        skip=skip,
    )
    payload = await api_client.request(
        "GET",
        _category_report_path(name, "transactions"),
        params=params,
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def get_category_report_opening_balance(
    category: str,
    *,
    description: str = "",
    descriptions: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: str = "date",
    sort_asc: bool = True,
    skip: int = 0,
) -> dict:
    """TZS/USD sum of rows *before* the current page (for running Balance)."""
    if skip <= 0:
        return {"tzs": 0.0, "usd": 0.0}
    name = (category or "").strip()
    if not name:
        return {"tzs": 0.0, "usd": 0.0}
    params = _category_report_params(
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
        sort_field=sort_field,
        sort_asc=sort_asc,
        skip=skip,
    )
    payload = await api_client.request(
        "GET",
        _category_report_path(name, "opening-balance"),
        params=params,
    )
    return {
        "tzs": float((payload or {}).get("tzs") or 0.0),
        "usd": float((payload or {}).get("usd") or 0.0),
    }


async def get_category_report_descriptions(
    category: str,
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[str]:
    """Distinct transaction descriptions for an item QuickReport toolbar filter."""
    name = (category or "").strip()
    if not name:
        return []
    params = _category_report_params(date_from=date_from, date_to=date_to)
    payload = await api_client.request(
        "GET",
        _category_report_path(name, "descriptions"),
        params=params,
    )
    return [str(v) for v in (payload.get("items") or []) if v]


async def get_category_report_column_values(
    field: str,
    category: str,
    *,
    description: str = "",
    descriptions: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
) -> List[str]:
    """Distinct Excel ▾ values for *field* on an item QuickReport (all years)."""
    if field not in _MASTER_COL_FIELDS:
        return []
    name = (category or "").strip()
    if not name:
        return []
    filters = {
        k: v for k, v in (column_filters or {}).items()
        if k != field and k in _MASTER_COL_FIELDS
    }
    params = _category_report_params(
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=filters or None,
        field=field,
    )
    payload = await api_client.request(
        "GET",
        _category_report_path(name, "column-values"),
        params=params,
    )
    return [str(v) for v in (payload.get("items") or []) if v]


async def get_master_column_values(
    field: str,
    *,
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    category: str = "",
    receipt: str = "",
    description: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
) -> List[str]:
    """Distinct Excel ▾ values for *field* across the whole selected range."""
    if field not in _MASTER_COL_FIELDS:
        return []
    filters = {
        k: v for k, v in (column_filters or {}).items()
        if k != field and k in _MASTER_COL_FIELDS
    }
    params = _master_params(
        year=year,
        month=month,
        search=search,
        truck=truck,
        category=category,
        receipt=receipt,
        description=description,
        date_from=date_from,
        date_to=date_to,
        column_filters=filters or None,
        field=field,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/master/meta/column-values", params=params
    )
    return [str(v) for v in (payload.get("items") or []) if v]


async def get_master_month_totals(year: int) -> Dict:
    """Per-month TZS / USD / ZMW totals for the year (master ledger)."""
    payload = await api_client.request(
        "GET",
        "v1/accountant/master/month-totals",
        params={"year": int(year)},
    )
    months = payload.get("months") or {}
    out: Dict[int, dict] = {}
    for key, row in months.items():
        try:
            month = int(key)
        except (TypeError, ValueError):
            continue
        out[month] = {
            "tzs": float((row or {}).get("tzs") or 0.0),
            "usd": float((row or {}).get("usd") or 0.0),
            "zmw": float((row or {}).get("zmw") or 0.0),
            "count": int((row or {}).get("count") or 0),
        }
    return out


async def get_master_available_years() -> List[int]:
    """Calendar years that have verified Master rows (Excel date or register day)."""
    payload = await api_client.request("GET", "v1/accountant/master/years")
    return [int(y) for y in (payload.get("items") or []) if y is not None]


async def get_master_trucks(year: Optional[int] = None) -> List[str]:
    params: dict = {}
    if year is not None:
        params["year"] = int(year)
    payload = await api_client.request(
        "GET", "v1/accountant/master/meta/trucks", params=params
    )
    return [str(v) for v in (payload.get("items") or []) if v]


async def get_master_categories(year: Optional[int] = None) -> List[str]:
    params: dict = {}
    if year is not None:
        params["year"] = int(year)
    payload = await api_client.request(
        "GET", "v1/accountant/master/meta/categories", params=params
    )
    return [str(v) for v in (payload.get("items") or []) if v]


# ── Diesel Cash (cashier-fed, verified transactions) ─────────────────────────

DIESEL_CASH_CATEGORY = "Diesel Cash"
DIESEL_CASH_ITEMS_SETTING = "diesel_cash_items"

# Default item name(s) when the accountant has not configured a list yet.
# Matching is case-insensitive exact on item / category_name — never description.
DIESEL_CASH_CATEGORIES = ("Diesel Cash",)

_MONTH_NAMES = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def normalize_diesel_cash_item_names(raw: Any = None) -> List[str]:
    """Unique item names, order preserved. ``None`` → default; ``[]`` stays empty."""
    if raw is None:
        values: Sequence[Any] = DIESEL_CASH_CATEGORIES
    elif isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = DIESEL_CASH_CATEGORIES
    names: List[str] = []
    seen: Set[str] = set()
    for value in values:
        name = str(value or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


async def get_diesel_cash_item_names() -> List[str]:
    """Configured item names that feed Fuel Consumption → Diesel Cash."""
    payload = await api_client.request("GET", "v1/accountant/diesel-cash/items")
    items = payload.get("items")
    if items is None:
        return list(DIESEL_CASH_CATEGORIES)
    return normalize_diesel_cash_item_names(items)


async def set_diesel_cash_item_names(names: Sequence[str]) -> List[str]:
    """Persist the item names that feed Diesel Cash. Empty list catches nothing."""
    cleaned = normalize_diesel_cash_item_names(list(names or []))
    payload = await api_client.request(
        "PUT",
        "v1/accountant/diesel-cash/items",
        json={"names": cleaned},
    )
    return normalize_diesel_cash_item_names(payload.get("items") or cleaned)


def _diesel_cash_name_filter(names: Optional[Sequence[str]] = None) -> Optional[dict]:
    """Case-insensitive exact match against the configured diesel item names."""
    cleaned = (
        normalize_diesel_cash_item_names(names)
        if names is not None
        else list(DIESEL_CASH_CATEGORIES)
    )
    if not cleaned:
        return None
    # Longer names first so "Diesel Cash" is not shadowed by "Diesel".
    alternation = "|".join(
        re.escape(n) for n in sorted(cleaned, key=len, reverse=True)
    )
    return {"$regex": f"^(?:{alternation})$", "$options": "i"}


def is_diesel_cash_item(
    name: Optional[str],
    item_names: Optional[Sequence[str]] = None,
) -> bool:
    """True when *name* is one of the configured diesel item names."""
    if not (name or "").strip():
        return False
    name_filter = _diesel_cash_name_filter(item_names)
    if not name_filter:
        return False
    return bool(re.fullmatch(name_filter["$regex"], str(name), re.IGNORECASE))


def _diesel_cash_verified_match(names: Optional[Sequence[str]] = None) -> dict:
    """``verified`` + item/category_name match (source-agnostic: cashier or import)."""
    query: dict = {
        "verified": True,
        "deletion_requested": {"$ne": True},
        "trashed": {"$ne": True},
    }
    name_filter = _diesel_cash_name_filter(names)
    if name_filter is None:
        query["category_name"] = {"$in": []}
        return query
    query["$or"] = [
        {"category_name": name_filter},
        {"item": name_filter},
    ]
    return query


def _build_diesel_cash_query(
    year: Optional[int],
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    item_names: Optional[Sequence[str]] = None,
) -> dict:
    query: dict = _diesel_cash_verified_match(item_names)

    _year = int(year or 0)
    if _year > 0:
        if month and 1 <= month <= 12:
            last_day = calendar.monthrange(_year, month)[1]
            start = datetime(_year, month, 1)
            end = datetime(_year, month, last_day, 23, 59, 59)
        else:
            start = datetime(_year, 1, 1)
            end = datetime(_year, 12, 31, 23, 59, 59)
        if date_from is not None:
            start = max(start, date_from.replace(hour=0, minute=0, second=0, microsecond=0))
        if date_to is not None:
            end = min(end, date_to.replace(hour=23, minute=59, second=59, microsecond=0))
        query["date"] = {"$gte": start, "$lte": end}
    else:
        clause = _date_range_clause("date", date_from, date_to)
        if clause:
            query.update(clause)

    if search.strip():
        query["description"] = {"$regex": re.escape(search.strip()), "$options": "i"}
    if truck.strip():
        query["truck_number"] = _truck_exact(truck)
    if receipt.strip() and receipt != "all":
        query["receipt_status"] = receipt.strip()
    return query


def _diesel_cash_params(
    *,
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    sort_field: Optional[str] = None,
    sort_asc: Optional[bool] = None,
    limit: Optional[int] = None,
    skip: Optional[int] = None,
    station: Optional[str] = None,
) -> dict:
    params: dict = {}
    if year is not None:
        params["year"] = int(year)
    if month:
        params["month"] = int(month)
    if search:
        params["search"] = search
    if truck:
        params["truck"] = truck
    if receipt:
        params["receipt"] = receipt
    if date_from is not None:
        params["date_from"] = _iso_dt(date_from)
    if date_to is not None:
        params["date_to"] = _iso_dt(date_to)
    if sort_field is not None:
        params["sort_field"] = sort_field
    if sort_asc is not None:
        params["sort_asc"] = "true" if sort_asc else "false"
    if limit is not None:
        params["limit"] = limit
    if skip is not None:
        params["skip"] = skip
    if station:
        params["station"] = station
    return params


def _parse_api_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None)
        except ValueError:
            return None
    return None


async def get_diesel_cash_month_summaries(year: int) -> list:
    """One summary row per calendar month for verified Diesel Cash transactions."""
    payload = await api_client.request(
        "GET",
        "v1/accountant/diesel-cash/month-summaries",
        params={"year": int(year)},
    )
    summaries = []
    for doc in payload.get("items") or []:
        month_idx = int(doc.get("month") or 0)
        summaries.append({
            "month": month_idx,
            "month_name": (
                doc.get("month_name")
                or (_MONTH_NAMES[month_idx] if 1 <= month_idx <= 12 else str(month_idx))
            ),
            "record_count": int(doc.get("record_count") or 0),
            "tzs_total": float(doc.get("tzs_total") or 0.0),
            "usd_total": float(doc.get("usd_total") or 0.0),
            "min_date": _parse_api_datetime(doc.get("min_date")),
            "max_date": _parse_api_datetime(doc.get("max_date")),
        })
    return summaries


async def get_diesel_cash_transactions(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    sort_field: str = "date",
    sort_asc: bool = False,
    limit: int = 50,
    skip: int = 0,
) -> List[Transaction]:
    params = _diesel_cash_params(
        year=year,
        month=month,
        search=search,
        truck=truck,
        receipt=receipt,
        date_from=date_from,
        date_to=date_to,
        sort_field=sort_field,
        sort_asc=sort_asc,
        limit=limit,
        skip=skip,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/diesel-cash/transactions", params=params
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def count_diesel_cash_transactions(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    params = _diesel_cash_params(
        year=year,
        month=month,
        search=search,
        truck=truck,
        receipt=receipt,
        date_from=date_from,
        date_to=date_to,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/diesel-cash/transactions/count", params=params
    )
    return int(payload.get("count") or 0)


async def get_diesel_cash_totals(
    year: Optional[int] = None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    params = _diesel_cash_params(
        year=year,
        month=month,
        search=search,
        truck=truck,
        receipt=receipt,
        date_from=date_from,
        date_to=date_to,
    )
    payload = await api_client.request(
        "GET", "v1/accountant/diesel-cash/totals", params=params
    )
    return {
        "tzs": float(payload.get("tzs") or 0.0),
        "usd": float(payload.get("usd") or 0.0),
    }


# ── stubs (diesel / reconciliation) ──────────────────────────────────────────

async def get_diesel_entries(
    station: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    params = _diesel_cash_params(station=station, limit=limit, skip=skip)
    payload = await api_client.request(
        "GET", "v1/accountant/diesel-cash/entries", params=params
    )
    return list(payload.get("items") or [])


async def get_reconciliation_entries(
    entity: Optional[str] = None,
    station: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
    *,
    table: str = "bonds",
) -> list:
    """Alias for reconciliation rows (Phase 6). ``entity`` is unused (always SM Burhani)."""
    _ = entity
    from tahmeed.services.reconciliation_service import get_reconciliation_rows

    entries = await get_reconciliation_rows(
        table,
        station=station or "",
        limit=limit,
        skip=skip,
    )
    return [entry.to_doc() for entry in entries]


# ── Imported feeds (toll_plaza / parking_congo / zambia_parking) ──────────────

def _feed_search_clause(
    search: str,
    truck_fields: Sequence[str],
    other_fields: Sequence[str] = (),
) -> Optional[dict]:
    """Plate queries match only the truck field; other text searches all fields.

    ``T103 DVL`` must not also hit plaza names, cashiers, or ``T102 DVL``.
    """
    text = (search or "").strip()
    if not text:
        return None
    from tahmeed.services.import_truck_check import truck_field_search_regex

    plate_rx = truck_field_search_regex(text)
    if plate_rx:
        clauses = [
            {field: {"$regex": plate_rx, "$options": "i"}}
            for field in truck_fields
        ]
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$or": clauses}
    escaped = re.escape(text)
    fields = list(truck_fields) + list(other_fields)
    if not fields:
        return None
    if len(fields) == 1:
        return {fields[0]: {"$regex": escaped, "$options": "i"}}
    return {"$or": [{field: {"$regex": escaped, "$options": "i"}} for field in fields]}


def _put_search(
    query: dict,
    search: str,
    truck_fields: Sequence[str],
    other_fields: Sequence[str] = (),
) -> None:
    clause = _feed_search_clause(search, truck_fields, other_fields)
    if clause:
        query.update(clause)


def _build_feed_query(feed_type: str, search: str = "", extra: str = "") -> dict:
    query: dict = {"feed_type": feed_type}
    _put_search(
        query,
        search,
        ("vehicle_reg", "vehicle_no", "plate_num"),
        ("toll_plaza", "receipt_no", "serial", "ticket_no", "heading_to"),
    )
    if extra.strip():
        query["toll_plaza"] = extra.strip()
    return query


async def get_imported_feed(
    feed_type: str,
    search: str = "",
    extra: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_imported_feed(feed_type=feed_type, search=search, extra=extra, limit=limit, skip=skip)


async def count_imported_feed(feed_type: str, search: str = "", extra: str = "") -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_imported_feed(feed_type=feed_type, search=search, extra=extra)


def _safe_double(field: str) -> dict:
    """$convert expression that safely coerces a string field to double.

    Handles null, missing, and empty-string values — all yield 0.0 rather
    than raising ConversionFailure (which $toDouble does on empty strings).
    """
    return {"$convert": {"input": f"${field}", "to": "double", "onError": 0.0, "onNull": 0.0}}


async def get_imported_feed_totals(feed_type: str) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_imported_feed_totals(feed_type=feed_type)


async def get_existing_feed_keys(keys: List[str], feed_type: str = "toll_plaza") -> set:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_existing_feed_keys(keys=keys, feed_type=feed_type)


def _parse_toll_date(val) -> Optional[datetime]:
    """Best-effort parse of a toll/payment/sales date from import (incl. Excel serials)."""
    return parse_excel_date(val)


def _toll_month_regex_patterns(year: int, month: int) -> List[str]:
    """Regex fragments matching common toll_date string formats for a month."""
    abbr = calendar.month_abbr[month]
    full = calendar.month_name[month]
    y, m2 = str(year), f"{month:02d}"
    return [
        rf"{y}-{m2}", rf"{y}/{m2}", rf"{m2}/{y}", rf"{m2}-{y}",
        rf"\b{abbr}\b.*{y}", rf"\b{full}\b.*{y}",
        rf"{y}.*\b{abbr}\b", rf"{y}.*\b{full}\b",
    ]


def _toll_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering toll records by calendar year and optional month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"transaction_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"transaction_date": {"$exists": False}},
                    {"transaction_date": None},
                ]},
                {"$or": [{"toll_date": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _toll_plaza_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Build a MongoDB query for all Toll Plaza records (cross-upload)."""
    clauses: List[dict] = [{"feed_type": "toll_plaza"}]
    search_clause = _feed_search_clause(
        search,
        ("vehicle_reg",),
        ("toll_plaza", "receipt_no", "cashier_name", "client_name", "card_no"),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_toll_date_filter(year, month))
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_toll_plaza_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_toll_plaza_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_toll_plaza_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_toll_plaza_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to)


async def get_toll_plaza_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_toll_plaza_all_totals(search=search, year=year, month=month, date_from=date_from, date_to=date_to)


async def _feed_available_years(feed_type: str, date_field: str) -> List[int]:
    """Return distinct calendar years present in uploaded records, newest first."""
    del date_field
    from tahmeed.services import feed_service as _feeds

    return await _feeds.years(_feeds.feed_slug(feed_type))


async def get_toll_plaza_available_years() -> List[int]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_toll_plaza_available_years()


async def get_parking_congo_available_years() -> List[int]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_parking_congo_available_years()


def _uppercase_import_text(doc: dict) -> None:
    """Uppercase string fields on an imported record (in place)."""
    for key, val in doc.items():
        if key in _IMPORT_UPPER_SKIP:
            continue
        if isinstance(val, str):
            doc[key] = val.upper()


async def save_imported_feed(records: list) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.save_imported_feed(records=records)


async def get_toll_plaza_uploads() -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_toll_plaza_uploads()


async def get_toll_plaza_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "toll_date",
    sort_asc: bool = True,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_toll_plaza_upload_records(upload_id=upload_id, search=search, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_toll_plaza_upload_records(upload_id: str, search: str = "") -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_toll_plaza_upload_records(upload_id=upload_id, search=search)


async def delete_toll_plaza_upload(upload_id: str) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.delete_toll_plaza_upload(upload_id=upload_id)


async def get_parking_congo_uploads() -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_parking_congo_uploads()


async def get_parking_congo_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "payment_date",
    sort_asc: bool = False,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_parking_congo_upload_records(upload_id=upload_id, search=search, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_parking_congo_upload_records(upload_id: str, search: str = "") -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_parking_congo_upload_records(upload_id=upload_id, search=search)


def _parking_month_regex_patterns(year: int, month: int) -> List[str]:
    """Regex fragments matching common payment_date string formats for a month."""
    return _toll_month_regex_patterns(year, month)


def _parking_congo_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering Parking Congo records by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _parking_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"transaction_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"transaction_date": {"$exists": False}},
                    {"transaction_date": None},
                ]},
                {"$or": [{"payment_date": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _parking_congo_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Build a MongoDB query for all Parking Congo records (cross-upload)."""
    clauses: List[dict] = [{"feed_type": "parking_congo"}]
    search_clause = _feed_search_clause(
        search,
        ("vehicle_no",),
        ("ledger_id", "transaction_type", "cashier", "transaction_details", "direction"),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_parking_congo_date_filter(year, month))
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_parking_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_parking_congo_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_parking_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_parking_congo_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to)


async def get_parking_congo_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_parking_congo_all_totals(search=search, year=year, month=month, date_from=date_from, date_to=date_to)


async def delete_parking_congo_upload(upload_id: str) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.delete_parking_congo_upload(upload_id=upload_id)


def _parking_congo_deposit_clause() -> dict:
    """Match Parking Congo deposit rows (tagged or by Type)."""
    return {"$or": [
        {"is_deposit": True},
        {"transaction_type": {"$regex": r"^deposit$", "$options": "i"}},
    ]}


def _parking_congo_deposits_query(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """MongoDB query for Parking Congo deposit records."""
    clauses: List[dict] = [
        {"feed_type": "parking_congo"},
        _parking_congo_deposit_clause(),
    ]
    if search.strip():
        s = re.escape(search.strip())
        clauses.append({"$or": [
            {"ledger_id":           {"$regex": s, "$options": "i"}},
            {"cashier":             {"$regex": s, "$options": "i"}},
            {"transaction_details": {"$regex": s, "$options": "i"}},
            {"source_filename":     {"$regex": s, "$options": "i"}},
            {"payment_date":        {"$regex": s, "$options": "i"}},
        ]})
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    return {"$and": clauses}


async def get_parking_congo_deposits(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_parking_congo_deposits(search=search, date_from=date_from, date_to=date_to, limit=limit, skip=skip)


async def count_parking_congo_deposits(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_parking_congo_deposits(search=search, date_from=date_from, date_to=date_to)


async def get_parking_congo_deposit_totals(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_parking_congo_deposit_totals(search=search, date_from=date_from, date_to=date_to)


async def get_parking_congo_upload_by_id(upload_id: str) -> Optional[dict]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_parking_congo_upload_by_id(upload_id=upload_id)


async def get_rahntech_uploads() -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_rahntech_uploads()


async def get_rahntech_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "sales_date",
    sort_asc: bool = False,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_rahntech_upload_records(upload_id=upload_id, search=search, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_rahntech_upload_records(upload_id: str, search: str = "") -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_rahntech_upload_records(upload_id=upload_id, search=search)


def _rahntech_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering RahnTech records by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"transaction_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"transaction_date": {"$exists": False}},
                    {"transaction_date": None},
                ]},
                {"$or": [{"sales_date": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _rahntech_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Build a MongoDB query for all RahnTech records (cross-upload)."""
    clauses: List[dict] = [{"feed_type": "rahntech"}]
    search_clause = _feed_search_clause(
        search,
        ("truck_number",),
        ("driver_name", "trip_number", "device_number", "do_number", "sales_date"),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_rahntech_date_filter(year, month))
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_rahntech_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_rahntech_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_rahntech_all_records(search: str = "", year: int = 0, month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_rahntech_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to)


async def get_rahntech_all_totals(search: str = "", year: int = 0, month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_rahntech_all_totals(search=search, year=year, month=month, date_from=date_from, date_to=date_to)


async def get_rahntech_available_years() -> List[int]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_rahntech_available_years()


async def delete_rahntech_upload(upload_id: str) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.delete_rahntech_upload(upload_id=upload_id)


def _norm_sheet_label(label: str) -> str:
    return str(label or "").strip().upper()


def _zambia_hash_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value).strip().upper()


def zambia_batch_content_hash(records: List[dict]) -> str:
    from tahmeed.services import feed_service as _feeds
    return _feeds.zambia_batch_content_hash(records=records)


async def zambia_sheet_exists(sheet_label: str) -> bool:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.zambia_sheet_exists(sheet_label=sheet_label)


async def zambia_existing_sheet_labels(labels: List[str]) -> set:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.zambia_existing_sheet_labels(labels=labels)


async def find_zambia_uploads_by_content_hash(content_hash: str) -> List[dict]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.find_zambia_uploads_by_content_hash(content_hash=content_hash)


async def zambia_exact_content_already_uploaded(records: List[dict]) -> List[dict]:
    """If this exact statement body was imported before, return matching upload(s)."""
    from tahmeed.services import feed_service as _feeds

    return await _feeds.zambia_already_uploaded(records=records)


async def get_zambia_parking_uploads() -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_zambia_parking_uploads()


async def get_zambia_parking_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "row_index",
    sort_asc: bool = True,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_zambia_parking_upload_records(upload_id=upload_id, search=search, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_zambia_parking_upload_records(upload_id: str, search: str = "") -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_zambia_parking_upload_records(upload_id=upload_id, search=search)


def _zambia_parking_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering Zambia Parking records by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"transaction_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"transaction_date": {"$exists": False}},
                    {"transaction_date": None},
                ]},
                {"$or": [{"date": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _zambia_parking_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    credit_only: bool = False,
) -> dict:
    """Build a MongoDB query for Zambia Parking rows (cross-upload)."""
    clauses: List[dict] = [{"feed_type": "zambia_parking"}]
    if credit_only:
        clauses.append({"credit": {"$gt": 0}})
    search_clause = _feed_search_clause(
        search,
        ("plate_num",),
        ("ticket_no", "heading_to", "type", "date"),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_zambia_parking_date_filter(year, month))
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_zambia_parking_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    credit_only: bool = False,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_zambia_parking_all_records(search=search, year=year, month=month, credit_only=credit_only, date_from=date_from, date_to=date_to, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_zambia_parking_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    credit_only: bool = False,
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_zambia_parking_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to, credit_only=credit_only)


async def get_zambia_parking_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    credit_only: bool = False,
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_zambia_parking_all_totals(search=search, year=year, month=month, date_from=date_from, date_to=date_to, credit_only=credit_only)


async def get_zambia_parking_available_years() -> List[int]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_zambia_parking_available_years()


async def delete_zambia_parking_upload(upload_id: str) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.delete_zambia_parking_upload(upload_id=upload_id)


def _build_insurance_query(
    feed_type: str, search: str = "", month: str = "", status: str = ""
) -> dict:
    query: dict = {"feed_type": feed_type}
    _put_search(query, search, ("truck_reg", "reg_no"), ("name", "card_no"))
    if month.strip() and month.upper() != "ALL":
        query["month"] = {"$regex": re.escape(month.strip()), "$options": "i"}
    if status.strip() and status.upper() != "ALL":
        query["status"] = status.strip()
    return query


async def get_insurance_feed(
    feed_type: str,
    search: str = "",
    month: str = "",
    status: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "month",
    sort_asc: bool = True,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_insurance_feed(feed_type=feed_type, search=search, month=month, status=status, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_insurance_feed(
    feed_type: str, search: str = "", month: str = "", status: str = ""
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_insurance_feed(feed_type=feed_type, search=search, month=month, status=status)


async def get_insurance_totals(
    feed_type: str, month: str = "", status: str = ""
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_insurance_totals(feed_type=feed_type, month=month, status=status)


async def get_existing_insurance_keys(feed_type: str, keys: List[str]) -> set:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_existing_insurance_keys(feed_type=feed_type, keys=keys)


async def get_diesel_feed(
    feed_type: str,
    search: str = "",
    truck: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_diesel_feed(feed_type=feed_type, search=search, truck=truck, limit=limit, skip=skip)


async def count_diesel_feed(feed_type: str, search: str = "", truck: str = "") -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_diesel_feed(feed_type=feed_type, search=search, truck=truck)


def _diesel_computed_amount() -> dict:
    """Litres × rate — Excel totals are not trusted."""
    return {"$multiply": [_safe_double("ltrs"), _safe_double("price_per_ltr")]}


async def get_diesel_totals(feed_type: str) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_diesel_totals(feed_type=feed_type)


async def get_diesel_uploads(feed_type: str) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_diesel_uploads(feed_type=feed_type)


def _diesel_records_query(feed_type: str, upload_id: str, search: str) -> dict:
    query: dict = {"feed_type": feed_type, "upload_id": upload_id}
    _put_search(
        query,
        search,
        ("truck_no",),
        (
            "lpo_no", "do_sdo_no", "clients_name", "destinations",
            "diesel_at", "upload_label", "source_filename",
        ),
    )
    return query


async def get_diesel_upload_records(
    feed_type: str,
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "date",
    sort_asc: bool = True,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_diesel_upload_records(feed_type=feed_type, upload_id=upload_id, search=search, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_diesel_upload_records(
    feed_type: str, upload_id: str, search: str = "",
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_diesel_upload_records(feed_type=feed_type, upload_id=upload_id, search=search)


async def get_diesel_upload_totals(
    feed_type: str, upload_id: str, search: str = "",
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_diesel_upload_totals(feed_type=feed_type, upload_id=upload_id, search=search)


async def delete_diesel_upload(feed_type: str, upload_id: str) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.delete_diesel_upload(feed_type=feed_type, upload_id=upload_id)


def _diesel_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering diesel records by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"transaction_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"transaction_date": {"$exists": False}},
                    {"transaction_date": None},
                ]},
                {"$or": [{"date": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def diesel_display_label(rec: dict) -> str:
    """Upload description entered at import time, else the Excel file name."""
    label = str(rec.get("upload_label") or "").strip()
    if label:
        return label
    return str(rec.get("source_filename") or "").strip()


def unique_diesel_file_labels(labels: Iterable[str]) -> List[str]:
    """Case-insensitive unique file-name / description labels, sorted."""
    seen: Set[str] = set()
    out: List[str] = []
    for raw in labels:
        label = str(raw or "").strip()
        key = label.lower()
        if label and key not in seen:
            seen.add(key)
            out.append(label)
    out.sort(key=str.lower)
    return out


def _diesel_empty_upload_label_clause() -> dict:
    """Match rows whose import description is missing or blank."""
    return {"$or": [
        {"upload_label": {"$exists": False}},
        {"upload_label": None},
        {"upload_label": {"$regex": r"^\s*$"}},
    ]}


def _diesel_file_labels_clause(file_labels: Optional[Sequence[str]] = None) -> Optional[dict]:
    """Match rows whose displayed FILE NAME is any of the selected labels."""
    labels = unique_diesel_file_labels(file_labels or [])
    if not labels:
        return None
    empty = _diesel_empty_upload_label_clause()
    branches: List[dict] = []
    for label in labels:
        exact = {"$regex": f"^{re.escape(label)}$", "$options": "i"}
        branches.append({"upload_label": exact})
        branches.append({"$and": [empty, {"source_filename": exact}]})
    if len(branches) == 1:
        return branches[0]
    return {"$or": branches}


def _diesel_all_query(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    file_labels: Optional[Sequence[str]] = None,
) -> dict:
    """Build a MongoDB query for diesel rows across all uploads."""
    clauses: List[dict] = [{"feed_type": feed_type}]
    search_clause = _feed_search_clause(
        search,
        ("truck_no",),
        (
            "lpo_no", "do_sdo_no", "clients_name", "destinations",
            "diesel_at", "ownership", "source_filename", "upload_label", "date",
        ),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_diesel_date_filter(year, month))
    clause = _date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    labels_clause = _diesel_file_labels_clause(file_labels)
    if labels_clause:
        clauses.append(labels_clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_diesel_file_labels(feed_type: str) -> List[str]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_diesel_file_labels(feed_type=feed_type)


async def get_diesel_all_records(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    file_labels: Optional[Sequence[str]] = None,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_diesel_all_records(feed_type=feed_type, search=search, year=year, month=month, date_from=date_from, date_to=date_to, limit=limit, skip=skip, file_labels=file_labels, sort_field=sort_field, sort_asc=sort_asc)


async def count_diesel_all_records(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    file_labels: Optional[Sequence[str]] = None,
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_diesel_all_records(feed_type=feed_type, search=search, year=year, month=month, date_from=date_from, date_to=date_to, file_labels=file_labels)


async def get_diesel_all_totals(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    file_labels: Optional[Sequence[str]] = None,
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_diesel_all_totals(feed_type=feed_type, search=search, year=year, month=month, date_from=date_from, date_to=date_to, file_labels=file_labels)


async def get_diesel_available_years(feed_type: str) -> List[int]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_diesel_available_years(feed_type=feed_type)


def diesel_batch_content_hash(records: List[dict]) -> str:
    from tahmeed.services import feed_service as _feeds
    return _feeds.diesel_batch_content_hash(records=records)


def _diesel_filename_sheet_query(
    feed_type: str,
    source_filename: str,
    sheet_label: str = "",
) -> Optional[dict]:
    """Match a prior diesel upload by file name + sheet tab (case-insensitive)."""
    fname = str(source_filename or "").strip()
    if not feed_type or not fname:
        return None
    query: dict = {
        "feed_type": feed_type,
        "source_filename": {
            "$regex": f"^{re.escape(fname)}$",
            "$options": "i",
        },
    }
    sheet = str(sheet_label or "").strip()
    if sheet:
        query["sheet_label"] = {
            "$regex": f"^{re.escape(sheet)}$",
            "$options": "i",
        }
    else:
        query["$or"] = [
            {"sheet_label": {"$in": ["", None]}},
            {"sheet_label": {"$exists": False}},
        ]
    return query


def _diesel_upload_summary(up: dict, *, content_hash: str = "") -> dict:
    uid = str(up.get("upload_id") or up.get("_id") or "").strip()
    summary = {
        "upload_id": uid,
        "sheet_label": up.get("sheet_label") or "",
        "source_filename": up.get("source_filename") or "",
        "import_date": up.get("import_date"),
        "record_count": int(up.get("record_count", 0) or 0),
    }
    if content_hash:
        summary["content_hash"] = content_hash
    return summary


async def diesel_filename_already_uploaded(
    feed_type: str,
    source_filename: str,
    sheet_label: str = "",
) -> List[dict]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.diesel_filename_already_uploaded(feed_type=feed_type, source_filename=source_filename, sheet_label=sheet_label)


async def find_diesel_uploads_by_content_hash(
    feed_type: str, content_hash: str,
) -> List[dict]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.find_diesel_uploads_by_content_hash(feed_type=feed_type, content_hash=content_hash)


async def diesel_already_uploaded(
    feed_type: str,
    records: List[dict],
    source_filename: str = "",
    sheet_label: str = "",
) -> List[dict]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.diesel_already_uploaded(feed_type=feed_type, records=records, source_filename=source_filename, sheet_label=sheet_label)


async def kimvi_sheet_exists(sheet_label: str) -> bool:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.kimvi_sheet_exists(sheet_label=sheet_label)


async def kimvi_existing_sheet_labels(labels: List[str]) -> set:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.kimvi_existing_sheet_labels(labels=labels)


async def save_kimvi_import(records: list) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.save_kimvi_import(records=records)


async def get_kimvi_uploads() -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_kimvi_uploads()


async def get_kimvi_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "row_index",
    sort_asc: bool = True,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_kimvi_upload_records(upload_id=upload_id, search=search, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_kimvi_upload_records(upload_id: str, search: str = "") -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_kimvi_upload_records(upload_id=upload_id, search=search)


def _kimvi_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering Ahmed Kimvi rows by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"expense_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"expense_date": {"$exists": False}},
                    {"expense_date": None},
                ]},
                {"$or": [{"date_str": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _usd_amount_clause(
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> Optional[dict]:
    """Filter signed USD rows: money-in is negative; called-out is everything else."""
    if money_in_only:
        return {"amount_usd": {"$lt": 0}}
    if called_out_only:
        return {"$nor": [{"amount_usd": {"$lt": 0}}]}
    return None


def _kimvi_entries_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> dict:
    """Build a MongoDB query for Ahmed Kimvi rows (cross-upload)."""
    clauses: List[dict] = [{"expense_type": "ahmed_kimvi"}]
    amount_clause = _usd_amount_clause(money_in_only, called_out_only)
    if amount_clause:
        clauses.append(amount_clause)
    search_clause = _feed_search_clause(
        search, ("truck_no",), ("description", "date_str"),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_kimvi_date_filter(year, month))
    clause = _date_range_clause("expense_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_kimvi_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    money_in_only: bool = False,
    called_out_only: bool = False,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "expense_date",
    sort_asc: bool = False,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_kimvi_all_records(search=search, year=year, month=month, money_in_only=money_in_only, called_out_only=called_out_only, date_from=date_from, date_to=date_to, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_kimvi_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_kimvi_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to, money_in_only=money_in_only, called_out_only=called_out_only)


async def get_kimvi_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_kimvi_all_totals(search=search, year=year, month=month, date_from=date_from, date_to=date_to, money_in_only=money_in_only, called_out_only=called_out_only)


async def get_kimvi_available_years() -> List[int]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_kimvi_available_years()


async def delete_kimvi_upload(upload_id: str) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.delete_kimvi_upload(upload_id=upload_id)


async def congo_sheet_exists(sheet_label: str) -> bool:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.congo_sheet_exists(sheet_label=sheet_label)


async def congo_existing_sheet_labels(labels: List[str]) -> set:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.congo_existing_sheet_labels(labels=labels)


async def save_congo_import(records: list) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.save_congo_import(records=records)


async def get_congo_uploads() -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_congo_uploads()


async def get_congo_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "row_index",
    sort_asc: bool = True,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_congo_upload_records(upload_id=upload_id, search=search, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_congo_upload_records(upload_id: str, search: str = "") -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_congo_upload_records(upload_id=upload_id, search=search)


def _congo_date_filter(year: int, month: int) -> dict:
    """MongoDB clause filtering Congo Expenses by calendar year/month."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1) if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = _toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {"$or": [
        {"expense_date": {"$gte": start, "$lt": end}},
        {
            "$and": [
                {"$or": [
                    {"expense_date": {"$exists": False}},
                    {"expense_date": None},
                ]},
                {"$or": [{"date_str": {"$regex": p, "$options": "i"}} for p in legacy]},
            ],
        },
    ]}


def _congo_entries_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> dict:
    """Build a MongoDB query for Congo Expenses rows (cross-upload)."""
    clauses: List[dict] = [{"expense_type": "congo_expenses"}]
    amount_clause = _usd_amount_clause(money_in_only, called_out_only)
    if amount_clause:
        clauses.append(amount_clause)
    search_clause = _feed_search_clause(
        search, ("truck_no",), ("description", "lpo_no", "date_str"),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_congo_date_filter(year, month))
    clause = _date_range_clause("expense_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    money_in_only: bool = False,
    called_out_only: bool = False,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "expense_date",
    sort_asc: bool = False,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_congo_all_records(search=search, year=year, month=month, money_in_only=money_in_only, called_out_only=called_out_only, date_from=date_from, date_to=date_to, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_congo_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to, money_in_only=money_in_only, called_out_only=called_out_only)


async def get_congo_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_congo_all_totals(search=search, year=year, month=month, date_from=date_from, date_to=date_to, money_in_only=money_in_only, called_out_only=called_out_only)


async def get_congo_available_years() -> List[int]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_congo_available_years()


async def delete_congo_upload(upload_id: str) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.delete_congo_upload(upload_id=upload_id)


# Per-station source_group keys (sidebar keys) so Fuel Overview can filter
# Infinity vs Lake Zambia vs … while Truck Overview still treats them as one
# "Diesel Imports" bucket.
_DIESEL_FEED_SOURCE_GROUP = {
    "diesel_infinity": "infinity",
    "diesel_lake_zambia": "lake_zambia",
    "diesel_lake_tunduma": "lake_tunduma",
    "diesel_gbp": "gbp_diesel",
}
_DIESEL_STATION_SOURCE_GROUPS = frozenset(_DIESEL_FEED_SOURCE_GROUP.values())

_TRUCK_OVERVIEW_SOURCES = {
    "all",
    "master",
    "diesel_cash",
    "diesel_imports",
    "afritrack",
    "toll_plaza",
    "parking_congo",
    "zambia_parking",
    "congo_expenses",
    "ahmed_kimvi",
    "rahntech",
    "comesa",
    "third_party",
    "sm_burhani",
    *_DIESEL_STATION_SOURCE_GROUPS,
}

_FUEL_OVERVIEW_SOURCES = {
    "all",
    "diesel_cash",
    *_DIESEL_STATION_SOURCE_GROUPS,
}


def _truck_exact(value: str) -> dict:
    """Match a plate ignoring spaces, optionally followed by ``/trailer``."""
    from tahmeed.services.import_truck_check import truck_exact_match_regex

    pattern = truck_exact_match_regex(value)
    if pattern:
        return {"$regex": pattern, "$options": "i"}
    return {"$regex": f"^{re.escape(value.strip())}$", "$options": "i"}


def _truck_and_trailer_match(value: str) -> dict:
    """Match a truck in SM Burhani ``T469EKZ/T689ELK`` cells and spaced plates."""
    return _truck_exact(value)


def _safe_float_value(value: Any) -> float:
    try:
        if value in (None, "", "None"):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_date_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value in (None, "", "None"):
        return datetime.min
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d %b %Y",
        "%d %b %y",
    ):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return datetime.min


def _truck_row_in_date_range(
    row: dict,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> bool:
    row_date = row.get("date")
    if not isinstance(row_date, datetime) or row_date == datetime.min:
        return date_from is None and date_to is None
    if date_from is not None and row_date < date_from:
        return False
    if date_to is not None and row_date > date_to:
        return False
    return True


def _diesel_overview_query(
    feed_type: str,
    truck: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Match station diesel rows the same way All Entries from/to does.

    Imports store the real datetime on ``transaction_date``; ``date`` is often
    a display string, so ranging on ``date`` returns nothing.
    """
    return _imported_feed_overview_query(
        feed_type, "truck_no", truck, date_from, date_to,
    )


def _imported_feed_overview_query(
    feed_type: str,
    truck_field: str,
    truck: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Match imported-feed rows the same way their All Entries tabs do.

    Imports store the real datetime on ``transaction_date``; display fields
    (``toll_date`` / ``payment_date`` / ``sales_date``) are often strings, so
    ranging on those returns nothing under the fiscal-year From/To filter.
    """
    return _with_date_range(
        {"feed_type": feed_type, truck_field: _truck_exact(truck)},
        "transaction_date",
        date_from,
        date_to,
    )


async def get_afritrack_uploads() -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_afritrack_uploads()


def _afritrack_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Build a MongoDB query for Afritrack rows across all uploads."""
    clauses: List[dict] = [{"feed_type": "afritrack"}]
    search_clause = _feed_search_clause(
        search, ("truck",), ("remarks", "period", "source_filename"),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        if month >= 1:
            start = datetime(year, month, 1)
            end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        else:
            start = datetime(year, 1, 1)
            end = datetime(year + 1, 1, 1)
        clauses.append({"import_date": {"$gte": start, "$lt": end}})
    clause = _date_range_clause("import_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


async def get_afritrack_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "import_date",
    sort_asc: bool = False,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_afritrack_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_afritrack_all_records(search: str = "", year: int = 0, month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_afritrack_all_records(search=search, year=year, month=month, date_from=date_from, date_to=date_to)


async def get_afritrack_all_totals(search: str = "", year: int = 0, month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_afritrack_all_totals(search=search, year=year, month=month, date_from=date_from, date_to=date_to)


async def get_afritrack_available_years() -> List[int]:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_afritrack_available_years()


async def get_afritrack_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "row_index",
    sort_asc: bool = True,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_afritrack_upload_records(upload_id=upload_id, search=search, limit=limit, skip=skip, sort_field=sort_field, sort_asc=sort_asc)


async def count_afritrack_upload_records(upload_id: str, search: str = "") -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_afritrack_upload_records(upload_id=upload_id, search=search)


async def get_afritrack_upload_totals(upload_id: str, search: str = "") -> dict:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.get_afritrack_upload_totals(upload_id=upload_id, search=search)


async def delete_afritrack_upload(upload_id: str) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.delete_afritrack_upload(upload_id=upload_id)


def _normalize_truck_overview_sources(
    source: Union[str, Sequence[str], None],
) -> Optional[Set[str]]:
    """Return ``None`` for all sources, else the selected ``source_group`` keys."""
    if source is None or source == "" or source == "all":
        return None
    if isinstance(source, str):
        keys = [source]
    else:
        keys = [str(k) for k in source]
    if not keys or "all" in keys:
        return None
    wanted = {k for k in keys if k in _TRUCK_OVERVIEW_SOURCES and k != "all"}
    if "diesel_imports" in wanted:
        wanted |= set(_DIESEL_STATION_SOURCE_GROUPS)
        wanted.discard("diesel_imports")
    return wanted or None


def _normalize_truck_overview_currency(currency: str = "") -> str:
    cur = (currency or "").strip().upper()
    if cur in ("", "ALL"):
        return ""
    if cur in ("TZS", "TSH", "TZ"):
        return "TZS"
    if cur == "USD":
        return "USD"
    if cur in ("ZMW", "ZMB", "ZK"):
        return "ZMW"
    return cur


def _truck_row_matches_currency(row: dict, currency: str = "") -> bool:
    wanted = _normalize_truck_overview_currency(currency)
    if not wanted:
        return True
    return _normalize_truck_overview_currency(row.get("currency") or "") == wanted


def _filter_truck_overview_rows(
    rows: list,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
) -> list:
    wanted = _normalize_truck_overview_sources(source)
    filtered = [
        row for row in rows
        if (wanted is None or row["source_group"] in wanted)
        and _truck_row_in_date_range(row, date_from, date_to)
        and _truck_row_matches_currency(row, currency)
    ]
    filtered.sort(key=lambda row: (row.get("date") or datetime.min))
    return filtered


def _truck_fuel_overview_params(
    *,
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
    limit: Optional[int] = None,
    skip: Optional[int] = None,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: Optional[str] = None,
    sort_asc: Optional[bool] = None,
) -> dict:
    params: dict = {"truck": truck.strip()}
    src = _csv_param(source) if source not in (None, "") else "all"
    params["source"] = src or "all"
    if currency:
        params["currency"] = currency
    if date_from is not None:
        params["date_from"] = _iso_dt(date_from)
    if date_to is not None:
        params["date_to"] = _iso_dt(date_to)
    if limit is not None:
        params["limit"] = limit
    if skip is not None:
        params["skip"] = skip
    if column_filters:
        params["column_filters"] = json.dumps(
            {str(k): [str(v) for v in vals] for k, vals in column_filters.items() if vals}
        )
    if sort_field is not None:
        params["sort_field"] = sort_field
    if sort_asc is not None:
        params["sort_asc"] = "true" if sort_asc else "false"
    return params


async def get_truck_overview_records(
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    if not truck.strip():
        return []
    payload = await api_client.request(
        "GET",
        "v1/accountant/truck-overview/records",
        params=_truck_fuel_overview_params(
            truck=truck,
            source=source,
            date_from=date_from,
            date_to=date_to,
            currency=currency,
            limit=limit,
            skip=skip,
        ),
    )
    return [desktop_document(dict(row)) for row in (payload or [])]


async def count_truck_overview_records(
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
) -> int:
    if not truck.strip():
        return 0
    payload = await api_client.request(
        "GET",
        "v1/accountant/truck-overview/records/count",
        params=_truck_fuel_overview_params(
            truck=truck,
            source=source,
            date_from=date_from,
            date_to=date_to,
            currency=currency,
        ),
    )
    return int((payload or {}).get("count") or 0)


def _empty_overview_summary() -> dict:
    return {
        "record_count": 0,
        "source_count": 0,
        "tzs_total": 0.0,
        "usd_total": 0.0,
        "zmw_total": 0.0,
        "liters_total": 0.0,
    }


def _summarize_overview_rows(rows: list) -> dict:
    tzs_total = 0.0
    usd_total = 0.0
    zmw_total = 0.0
    liters_total = 0.0
    seen_sources: set[str] = set()
    for row in rows:
        seen_sources.add(row.get("source", ""))
        amount = row.get("amount")
        row_currency = _normalize_truck_overview_currency(row.get("currency") or "")
        if amount is not None:
            if row_currency == "TZS":
                tzs_total += amount
            elif row_currency == "USD":
                usd_total += amount
            elif row_currency == "ZMW":
                zmw_total += amount
        liters_total += row.get("liters") or 0.0
    return {
        "record_count": len(rows),
        "source_count": len(seen_sources),
        "tzs_total": tzs_total,
        "usd_total": usd_total,
        "zmw_total": zmw_total,
        "liters_total": liters_total,
    }


async def get_truck_overview_summary(
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
) -> dict:
    if not truck.strip():
        return _empty_overview_summary()
    payload = await api_client.request(
        "GET",
        "v1/accountant/truck-overview/summary",
        params=_truck_fuel_overview_params(
            truck=truck,
            source=source,
            date_from=date_from,
            date_to=date_to,
            currency=currency,
        ),
    )
    return dict(payload or _empty_overview_summary())


def _normalize_fuel_overview_sources(
    source: Union[str, Sequence[str], None],
) -> Optional[Set[str]]:
    """Return ``None`` for all fuel sources, else the selected fuel keys."""
    if source is None or source == "" or source == "all":
        return None
    if isinstance(source, str):
        keys = [source]
    else:
        keys = [str(k) for k in source]
    if not keys or "all" in keys:
        return None
    wanted = {k for k in keys if k in _FUEL_OVERVIEW_SOURCES and k != "all"}
    return wanted or None


def _filter_fuel_overview_rows(
    rows: list,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
    *,
    sort_rows: bool = True,
) -> list:
    wanted = _normalize_fuel_overview_sources(source)
    filtered = [
        row for row in rows
        if (wanted is None or row["source_group"] in wanted)
        and _truck_row_in_date_range(row, date_from, date_to)
        and _truck_row_matches_currency(row, currency)
    ]
    if sort_rows:
        filtered.sort(key=lambda row: (row.get("date") or datetime.min))
    return filtered


def _overview_amount_triple(row: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (tzs, usd, zmw) with the amount only in its currency column."""
    amount = row.get("amount")
    if amount in (None, ""):
        return None, None, None
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return None, None, None
    cur = _normalize_truck_overview_currency(row.get("currency") or "")
    if cur == "TZS":
        return val, None, None
    if cur == "USD":
        return None, val, None
    if cur == "ZMW":
        return None, None, val
    return None, None, None


def _fmt_overview_num(value, *, decimals: int = 0) -> str:
    if value in (None, "", 0, 0.0):
        return "—"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(val) < 1e-12:
        return "—"
    return f"{val:,.{decimals}f}"


def _fmt_overview_currency_cell(currency: str, value) -> str:
    if value is None:
        return "—"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "—"
    decimals = 2 if currency == "USD" else 0
    return f"{val:,.{decimals}f}"


def overview_row_display(row: dict, field: str) -> str:
    """Display text for Fuel / Truck overview cells (matches on-screen table)."""
    if field == "date":
        dt = row.get("date")
        if isinstance(dt, datetime) and dt.year > 1:
            return dt.strftime("%d %b %Y")
        return "—"
    if field == "source":
        return str(row.get("source") or "—")
    if field == "description":
        return str(row.get("description") or "—")
    if field == "reference":
        return str(row.get("reference") or "—")
    if field == "truck_value":
        return str(row.get("truck_value") or "—")
    if field == "tzs":
        tzs, _usd, _zmw = _overview_amount_triple(row)
        return _fmt_overview_currency_cell("TZS", tzs)
    if field == "usd":
        _tzs, usd, _zmw = _overview_amount_triple(row)
        return _fmt_overview_currency_cell("USD", usd)
    if field == "zmw":
        _tzs, _usd, zmw = _overview_amount_triple(row)
        return _fmt_overview_currency_cell("ZMW", zmw)
    if field == "liters":
        return _fmt_overview_num(row.get("liters"), decimals=0)
    if field == "rate":
        return _fmt_overview_num(row.get("rate"), decimals=2)
    if field == "station":
        return str(row.get("station") or "—")
    if field == "upload_description":
        return str(row.get("upload_description") or "—")
    if field == "receipt_status":
        receipt = str(row.get("receipt_status") or "").strip()
        return receipt if receipt and receipt != "—" else "—"
    return str(row.get(field) or "—")


def _apply_overview_column_filters(
    rows: list,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
) -> list:
    if not column_filters:
        return list(rows)
    out: list = []
    for row in rows:
        keep = True
        for field, accepted in column_filters.items():
            if not accepted:
                continue
            allowed = {str(v) for v in accepted}
            if overview_row_display(row, field) not in allowed:
                keep = False
                break
        if keep:
            out.append(row)
    return out


def _overview_sort_key(row: dict, field: str):
    if field == "date":
        return row.get("date") or datetime.min
    if field in ("liters", "rate"):
        val = row.get(field)
        if val in (None, ""):
            return float("-inf")
        return _safe_float_value(val)
    if field in ("tzs", "usd", "zmw"):
        cur_map = {"tzs": "TZS", "usd": "USD", "zmw": "ZMW"}
        wanted = cur_map[field]
        if _normalize_truck_overview_currency(row.get("currency") or "") != wanted:
            return float("-inf")
        amount = row.get("amount")
        if amount in (None, ""):
            return float("-inf")
        return _safe_float_value(amount)
    if field == "truck_value":
        return str(row.get("truck_value") or "").upper()
    text = overview_row_display(row, field)
    return text.lower() if text != "—" else ""


def _sort_overview_rows(rows: list, sort_field: str, sort_asc: bool) -> list:
    field = (sort_field or "date").strip() or "date"
    return sorted(rows, key=lambda row: _overview_sort_key(row, field), reverse=not sort_asc)


def _finalize_fuel_overview_rows(
    rows: list,
    *,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: str = "date",
    sort_asc: bool = True,
) -> list:
    rows = _apply_overview_column_filters(rows, column_filters)
    return _sort_overview_rows(rows, sort_field, sort_asc)


FUEL_OVERVIEW_FILTER_FIELDS = (
    "date",
    "source",
    "description",
    "reference",
    "truck_value",
    "tzs",
    "usd",
    "zmw",
    "liters",
    "rate",
    "station",
    "upload_description",
    "receipt_status",
)


async def get_fuel_overview_column_filter_cache(
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    fields: Sequence[str] = FUEL_OVERVIEW_FILTER_FIELDS,
) -> Dict[str, Set[str]]:
    """Distinct display values per column for Excel ▾ menus (cascaded)."""
    if not truck.strip():
        return {field: set() for field in fields}
    payload = await api_client.request(
        "GET",
        "v1/accountant/fuel-overview/column-filters",
        params=_truck_fuel_overview_params(
            truck=truck,
            source=source,
            date_from=date_from,
            date_to=date_to,
            currency=currency,
            column_filters=column_filters,
        ),
    )
    filters = (payload or {}).get("filters") or {}
    out: Dict[str, Set[str]] = {}
    for field in fields:
        raw = filters.get(field) or []
        out[field] = {str(v) for v in raw if str(v)}
    return out


async def get_fuel_overview_records(
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
    limit: int = 50,
    skip: int = 0,
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: str = "date",
    sort_asc: bool = True,
) -> list:
    if not truck.strip():
        return []
    payload = await api_client.request(
        "GET",
        "v1/accountant/fuel-overview/records",
        params=_truck_fuel_overview_params(
            truck=truck,
            source=source,
            date_from=date_from,
            date_to=date_to,
            currency=currency,
            limit=limit,
            skip=skip,
            column_filters=column_filters,
            sort_field=sort_field,
            sort_asc=sort_asc,
        ),
    )
    return [desktop_document(dict(row)) for row in (payload or [])]


async def count_fuel_overview_records(
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: str = "date",
    sort_asc: bool = True,
) -> int:
    if not truck.strip():
        return 0
    payload = await api_client.request(
        "GET",
        "v1/accountant/fuel-overview/records/count",
        params=_truck_fuel_overview_params(
            truck=truck,
            source=source,
            date_from=date_from,
            date_to=date_to,
            currency=currency,
            column_filters=column_filters,
            sort_field=sort_field,
            sort_asc=sort_asc,
        ),
    )
    return int((payload or {}).get("count") or 0)


async def get_fuel_overview_summary(
    truck: str,
    source: Union[str, Sequence[str], None] = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    currency: str = "",
    column_filters: Optional[Dict[str, Sequence[str]]] = None,
    sort_field: str = "date",
    sort_asc: bool = True,
) -> dict:
    if not truck.strip():
        return _empty_overview_summary()
    payload = await api_client.request(
        "GET",
        "v1/accountant/fuel-overview/summary",
        params=_truck_fuel_overview_params(
            truck=truck,
            source=source,
            date_from=date_from,
            date_to=date_to,
            currency=currency,
            column_filters=column_filters,
            sort_field=sort_field,
            sort_asc=sort_asc,
        ),
    )
    return dict(payload or _empty_overview_summary())


# ── Skipped import rows (fleet check park-for-follow-up) ──────────────────────

async def save_skipped_import_rows(docs: list) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.save_skipped_import_rows(docs=docs)


async def list_skipped_import_rows(
    feed_key: str,
    *,
    search: str = "",
    limit: int = 200,
    skip: int = 0,
) -> list:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.list_skipped_import_rows(feed_key=feed_key, search=search, limit=limit, skip=skip)


async def count_skipped_import_rows(feed_key: str, search: str = "") -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.count_skipped_import_rows(feed_key=feed_key, search=search)


async def update_skipped_import_truck(doc_id: str, truck_value: str) -> bool:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.update_skipped_import_truck(doc_id=doc_id, truck_value=truck_value)


async def delete_skipped_import_rows(ids: list[str]) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.delete_skipped_import_rows(ids=ids)


async def reupload_skipped_import_rows(ids: list[str]) -> int:
    from tahmeed.services import feed_service as _feeds
    return await _feeds.reupload_skipped_import_rows(ids=ids)


