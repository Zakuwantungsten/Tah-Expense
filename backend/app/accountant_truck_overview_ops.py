"""Truck overview row builders + filters (Phase 9.1)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional, Sequence, Union

from .accountant_diesel_ops import (
    date_range_clause,
    diesel_cash_name_filter,
    truck_exact_clause,
)
from .diesel_liters import parse_liters_from_description
from .feed_ops import diesel_line_total

TRUCK_OVERVIEW_DIESEL_FEEDS = (
    ("diesel_infinity", "Infinity Diesel"),
    ("diesel_lake_zambia", "Lake Zambia Diesel"),
    ("diesel_lake_tunduma", "Lake Tunduma Diesel"),
    ("diesel_gbp", "GBP Diesel"),
)

DIESEL_FEED_SOURCE_GROUP = {
    "diesel_infinity": "infinity",
    "diesel_lake_zambia": "lake_zambia",
    "diesel_lake_tunduma": "lake_tunduma",
    "diesel_gbp": "gbp_diesel",
}
DIESEL_STATION_SOURCE_GROUPS = frozenset(DIESEL_FEED_SOURCE_GROUP.values())

TRUCK_OVERVIEW_SOURCES = {
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
    *DIESEL_STATION_SOURCE_GROUPS,
}

_LAKE_ZAMBIA_CURRENCIES = frozenset({"USD", "ZMW"})
_DIESEL_FEED_CURRENCY = {
    "diesel_infinity": "TZS",
    "diesel_lake_zambia": "USD",
    "diesel_lake_tunduma": "TZS",
    "diesel_gbp": "TZS",
}


def with_date_range(
    query: dict[str, Any],
    field: str,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    clause = date_range_clause(field, date_from, date_to)
    if not clause:
        return query
    merged = dict(query)
    merged.update(clause)
    return merged


def imported_feed_overview_query(
    feed_type: str,
    truck_field: str,
    truck: str,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    return with_date_range(
        {"feed_type": feed_type, truck_field: truck_exact_clause(truck)},
        "transaction_date",
        date_from,
        date_to,
    )


def diesel_overview_query(
    feed_type: str,
    truck: str,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    return imported_feed_overview_query(
        feed_type, "truck_no", truck, date_from, date_to
    )


def is_diesel_cash_item(
    name: str | None,
    item_names: Sequence[str] | None = None,
) -> bool:
    if not (name or "").strip():
        return False
    name_filter = diesel_cash_name_filter(item_names)
    if not name_filter:
        return False
    return bool(
        re.fullmatch(name_filter["$regex"], str(name), re.IGNORECASE)
    )


def diesel_display_label(rec: dict[str, Any]) -> str:
    label = str(rec.get("upload_label") or "").strip()
    if label:
        return label
    return str(rec.get("source_filename") or "").strip()


def diesel_record_currency(doc: dict[str, Any], feed_type: str) -> str | None:
    stored = str(doc.get("currency") or "").strip().upper()
    if feed_type == "diesel_lake_zambia":
        return stored if stored in _LAKE_ZAMBIA_CURRENCIES else "USD"
    if feed_type in ("diesel_gbp", "diesel_lake_tunduma"):
        return None
    return _DIESEL_FEED_CURRENCY.get(feed_type)


def safe_float_value(value: Any) -> float:
    try:
        if value in (None, "", "None"):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def as_naive_datetime(value: datetime | None) -> datetime | None:
    """Strip tzinfo so Mongo tz-aware dates compare safely with filter bounds."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def as_date_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return as_naive_datetime(value) or datetime.min
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


def row_sort_date(row: dict[str, Any]) -> datetime:
    value = row.get("date")
    if isinstance(value, datetime):
        return as_naive_datetime(value) or datetime.min
    return datetime.min


def truck_row_in_date_range(
    row: dict[str, Any],
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> bool:
    raw = row.get("date")
    row_date = as_naive_datetime(raw) if isinstance(raw, datetime) else None
    if row_date is None or row_date == datetime.min:
        return date_from is None and date_to is None
    bound_from = as_naive_datetime(date_from)
    bound_to = as_naive_datetime(date_to)
    if bound_from is not None and row_date < bound_from:
        return False
    if bound_to is not None and row_date > bound_to:
        return False
    return True


def normalized_row(
    *,
    source_group: str,
    source: str,
    date_value: Any,
    truck_value: str,
    description: str,
    reference: str = "",
    currency: str = "",
    amount: Any = None,
    liters: Any = None,
    rate: Any = None,
    station: str = "",
    receipt_status: str = "",
    upload_description: str = "",
    item_name: str = "",
) -> dict[str, Any]:
    amt = safe_float_value(amount) if amount not in (None, "") else None
    ltrs = safe_float_value(liters) if liters not in (None, "") else None
    unit_rate = safe_float_value(rate) if rate not in (None, "") else None
    return {
        "source_group": source_group,
        "source": source,
        "date": as_date_value(date_value),
        "truck_value": truck_value or "—",
        "description": description or "—",
        "reference": reference or "—",
        "currency": currency or "",
        "amount": amt,
        "liters": ltrs,
        "rate": unit_rate,
        "station": station or "—",
        "receipt_status": receipt_status or "—",
        "upload_description": upload_description or "—",
        "item_name": item_name or "",
    }


def normalize_truck_overview_sources(
    source: Union[str, Sequence[str], None],
) -> Optional[set[str]]:
    if source is None or source == "" or source == "all":
        return None
    if isinstance(source, str):
        keys = [source]
    else:
        keys = [str(k) for k in source]
    if not keys or "all" in keys:
        return None
    wanted = {k for k in keys if k in TRUCK_OVERVIEW_SOURCES and k != "all"}
    if "diesel_imports" in wanted:
        wanted |= set(DIESEL_STATION_SOURCE_GROUPS)
        wanted.discard("diesel_imports")
    return wanted or None


def normalize_truck_overview_currency(currency: str = "") -> str:
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


def truck_row_matches_currency(row: dict[str, Any], currency: str = "") -> bool:
    wanted = normalize_truck_overview_currency(currency)
    if not wanted:
        return True
    return normalize_truck_overview_currency(row.get("currency") or "") == wanted


def filter_truck_overview_rows(
    rows: list[dict[str, Any]],
    source: Union[str, Sequence[str], None] = "all",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    currency: str = "",
) -> list[dict[str, Any]]:
    wanted = normalize_truck_overview_sources(source)
    filtered = [
        row
        for row in rows
        if (wanted is None or row["source_group"] in wanted)
        and truck_row_in_date_range(row, date_from, date_to)
        and truck_row_matches_currency(row, currency)
    ]
    filtered.sort(key=row_sort_date)
    return filtered


def empty_overview_summary() -> dict[str, Any]:
    return {
        "record_count": 0,
        "source_count": 0,
        "tzs_total": 0.0,
        "usd_total": 0.0,
        "zmw_total": 0.0,
        "liters_total": 0.0,
    }


def summarize_overview_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tzs_total = 0.0
    usd_total = 0.0
    zmw_total = 0.0
    liters_total = 0.0
    seen_sources: set[str] = set()
    for row in rows:
        seen_sources.add(row.get("source", ""))
        amount = row.get("amount")
        row_currency = normalize_truck_overview_currency(row.get("currency") or "")
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


def parse_source_param(raw: str | None) -> Union[str, list[str]]:
    """Accept ``all``, a single key, or comma-separated keys."""
    text = (raw or "").strip()
    if not text or text.lower() == "all":
        return "all"
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return "all"
    if len(parts) == 1:
        return parts[0]
    return parts


def master_rows_from_docs(
    docs: Sequence[dict[str, Any]],
    diesel_items: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        category = str(doc.get("category_name") or "")
        item = str(doc.get("item") or "")
        description = str(doc.get("description") or "")
        is_diesel = is_diesel_cash_item(category, diesel_items) or is_diesel_cash_item(
            item, diesel_items
        )
        rows.append(
            normalized_row(
                source_group="diesel_cash" if is_diesel else "master",
                source="Diesel Cash" if is_diesel else "Master Expenses",
                date_value=doc.get("date"),
                truck_value=str(doc.get("truck_number") or ""),
                description=description
                or item
                or category
                or "Verified transaction",
                reference=str(
                    doc.get("memo")
                    or doc.get("do_number")
                    or doc.get("lpo_do")
                    or category
                    or ""
                ),
                currency=str(doc.get("currency") or ""),
                item_name=item or category or "",
                amount=doc.get("amount"),
                liters=(
                    parse_liters_from_description(description) if is_diesel else None
                ),
                station=str(doc.get("ownership") or ""),
                receipt_status=str(doc.get("receipt_status") or ""),
            )
        )
    return rows


def diesel_rows_from_docs(
    feed_type: str,
    label: str,
    docs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    group = DIESEL_FEED_SOURCE_GROUP.get(feed_type, "diesel_imports")
    rows: list[dict[str, Any]] = []
    for doc in docs:
        rows.append(
            normalized_row(
                source_group=group,
                source=label,
                date_value=doc.get("transaction_date") or doc.get("date"),
                truck_value=str(doc.get("truck_no") or ""),
                description=str(
                    doc.get("destinations")
                    or doc.get("clients_name")
                    or "Diesel import"
                ),
                reference=str(
                    doc.get("lpo_no")
                    or doc.get("do_sdo_no")
                    or doc.get("sheet_label")
                    or ""
                ),
                currency=diesel_record_currency(doc, feed_type) or "TZS",
                amount=diesel_line_total(doc.get("ltrs"), doc.get("price_per_ltr")),
                liters=doc.get("ltrs"),
                rate=doc.get("price_per_ltr"),
                station=str(doc.get("diesel_at") or ""),
                upload_description=diesel_display_label(doc),
            )
        )
    return rows


def toll_rows_from_docs(docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        rows.append(
            normalized_row(
                source_group="toll_plaza",
                source="Toll Plaza",
                date_value=doc.get("transaction_date") or doc.get("toll_date"),
                truck_value=str(doc.get("vehicle_reg") or ""),
                description=str(doc.get("toll_plaza") or "Toll transaction"),
                reference=str(
                    doc.get("receipt_no")
                    or doc.get("card_no")
                    or doc.get("device")
                    or ""
                ),
                currency="ZMW",
                amount=doc.get("tender_amount"),
                station=str(doc.get("cashier_name") or ""),
            )
        )
    return rows


def parking_congo_rows_from_docs(
    docs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        rows.append(
            normalized_row(
                source_group="parking_congo",
                source="Parking Congo",
                date_value=doc.get("transaction_date") or doc.get("payment_date"),
                truck_value=str(doc.get("vehicle_no") or ""),
                description=str(
                    doc.get("transaction_details")
                    or doc.get("transaction_type")
                    or "Parking transaction"
                ),
                reference=str(doc.get("ledger_id") or doc.get("direction") or ""),
                currency="USD",
                amount=doc.get("amount"),
                station=str(doc.get("cashier") or ""),
            )
        )
    return rows


def zambia_rows_from_docs(docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        debit = safe_float_value(doc.get("debit"))
        credit = safe_float_value(doc.get("credit"))
        amount = debit if debit else (-credit if credit else None)
        rows.append(
            normalized_row(
                source_group="zambia_parking",
                source="Zambia Parking",
                date_value=doc.get("transaction_date") or doc.get("date"),
                truck_value=str(doc.get("plate_num") or ""),
                description=str(
                    doc.get("heading_to")
                    or doc.get("type")
                    or "Zambia parking transaction"
                ),
                reference=str(doc.get("ticket_no") or doc.get("sheet_label") or ""),
                currency="ZMW",
                amount=amount,
            )
        )
    return rows


def separate_expense_rows_from_docs(
    docs: Sequence[dict[str, Any]],
    *,
    expense_type: str,
    source_label: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        rows.append(
            normalized_row(
                source_group=expense_type,
                source=source_label,
                date_value=doc.get("expense_date") or doc.get("date_str"),
                truck_value=str(doc.get("truck_no") or ""),
                description=str(
                    doc.get("description") or f"{source_label} expense"
                ),
                reference=str(
                    doc.get("lpo_no") or doc.get("sheet_label") or ""
                ),
                currency="USD",
                amount=doc.get("amount_usd"),
            )
        )
    return rows


def afritrack_rows_from_docs(docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        rows.append(
            normalized_row(
                source_group="afritrack",
                source="Afritrack",
                date_value=doc.get("import_date"),
                truck_value=str(doc.get("truck") or ""),
                description=str(doc.get("remarks") or "Afritrack schedule row"),
                reference=str(
                    doc.get("period") or doc.get("source_filename") or ""
                ),
                currency="USD",
                amount=doc.get("total_invoice"),
                rate=doc.get("rate_per_day"),
            )
        )
    return rows


def rahntech_rows_from_docs(docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        rows.append(
            normalized_row(
                source_group="rahntech",
                source="RahnTech",
                date_value=doc.get("transaction_date") or doc.get("sales_date"),
                truck_value=str(doc.get("truck_number") or ""),
                description=str(
                    doc.get("driver_name")
                    or doc.get("device_number")
                    or "RahnTech transaction"
                ),
                reference=str(
                    doc.get("trip_number") or doc.get("do_number") or ""
                ),
            )
        )
    return rows


def comesa_rows_from_docs(docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        rows.append(
            normalized_row(
                source_group="comesa",
                source="COMESA",
                date_value=doc.get("import_date"),
                truck_value=str(doc.get("truck_reg") or ""),
                description=str(doc.get("name") or "COMESA cover"),
                reference=str(doc.get("card_no") or doc.get("month") or ""),
                currency="TZS",
                amount=doc.get("premium"),
            )
        )
    return rows


def third_party_rows_from_docs(
    docs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        name = str(doc.get("name") or "Third Party cover")
        status = doc.get("status")
        if status:
            name = f"{name} [{status}]"
        rows.append(
            normalized_row(
                source_group="third_party",
                source="Third Party Covers",
                date_value=doc.get("import_date"),
                truck_value=str(doc.get("reg_no") or ""),
                description=name,
                reference=str(doc.get("month") or ""),
                currency="TZS",
                amount=doc.get("total_premium"),
            )
        )
    return rows


def recon_rows_from_docs(docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        table = doc.get("table", "bonds")
        source_label = (
            "SM Burhani Bonds" if table == "bonds" else "SM Burhani RPA"
        )
        rows.append(
            normalized_row(
                source_group="sm_burhani",
                source=source_label,
                date_value=doc.get("t1_date") or doc.get("import_date"),
                truck_value=str(doc.get("truck_and_trailer") or ""),
                description=str(
                    doc.get("consignment")
                    or doc.get("importer")
                    or "Reconciliation entry"
                ),
                reference=str(
                    doc.get("prn_number")
                    or doc.get("entry_reg_no")
                    or doc.get("station")
                    or ""
                ),
                currency="USD",
                amount=doc.get("charge"),
                station=str(doc.get("station") or ""),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Fuel overview (shared display / filter helpers)
# ---------------------------------------------------------------------------

FUEL_OVERVIEW_SOURCES = {
    "all",
    "diesel_cash",
    *DIESEL_STATION_SOURCE_GROUPS,
}

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


def normalize_fuel_overview_sources(
    source: Union[str, Sequence[str], None],
) -> Optional[set[str]]:
    if source is None or source == "" or source == "all":
        return None
    if isinstance(source, str):
        keys = [source]
    else:
        keys = [str(k) for k in source]
    if not keys or "all" in keys:
        return None
    wanted = {k for k in keys if k in FUEL_OVERVIEW_SOURCES and k != "all"}
    return wanted or None


def filter_fuel_overview_rows(
    rows: list[dict[str, Any]],
    source: Union[str, Sequence[str], None] = "all",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    currency: str = "",
    *,
    sort_rows: bool = True,
) -> list[dict[str, Any]]:
    wanted = normalize_fuel_overview_sources(source)
    filtered = [
        row
        for row in rows
        if (wanted is None or row["source_group"] in wanted)
        and truck_row_in_date_range(row, date_from, date_to)
        and truck_row_matches_currency(row, currency)
    ]
    if sort_rows:
        filtered.sort(key=row_sort_date)
    return filtered


def overview_amount_triple(
    row: dict[str, Any],
) -> tuple[float | None, float | None, float | None]:
    amount = row.get("amount")
    if amount in (None, ""):
        return None, None, None
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return None, None, None
    cur = normalize_truck_overview_currency(row.get("currency") or "")
    if cur == "TZS":
        return val, None, None
    if cur == "USD":
        return None, val, None
    if cur == "ZMW":
        return None, None, val
    return None, None, None


def _fmt_overview_num(value: Any, *, decimals: int = 0) -> str:
    if value in (None, "", 0, 0.0):
        return "—"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(val) < 1e-12:
        return "—"
    return f"{val:,.{decimals}f}"


def _fmt_overview_currency_cell(currency: str, value: Any) -> str:
    if value is None:
        return "—"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "—"
    decimals = 2 if currency == "USD" else 0
    return f"{val:,.{decimals}f}"


def overview_row_display(row: dict[str, Any], field: str) -> str:
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
        tzs, _usd, _zmw = overview_amount_triple(row)
        return _fmt_overview_currency_cell("TZS", tzs)
    if field == "usd":
        _tzs, usd, _zmw = overview_amount_triple(row)
        return _fmt_overview_currency_cell("USD", usd)
    if field == "zmw":
        _tzs, _usd, zmw = overview_amount_triple(row)
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


def apply_overview_column_filters(
    rows: list[dict[str, Any]],
    column_filters: dict[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    if not column_filters:
        return list(rows)
    out: list[dict[str, Any]] = []
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


def overview_sort_key(row: dict[str, Any], field: str) -> Any:
    if field == "date":
        return row_sort_date(row)
    if field in ("liters", "rate"):
        val = row.get(field)
        if val in (None, ""):
            return float("-inf")
        return safe_float_value(val)
    if field in ("tzs", "usd", "zmw"):
        cur_map = {"tzs": "TZS", "usd": "USD", "zmw": "ZMW"}
        wanted = cur_map[field]
        if normalize_truck_overview_currency(row.get("currency") or "") != wanted:
            return float("-inf")
        amount = row.get("amount")
        if amount in (None, ""):
            return float("-inf")
        return safe_float_value(amount)
    if field == "truck_value":
        return str(row.get("truck_value") or "").upper()
    text = overview_row_display(row, field)
    return text.lower() if text != "—" else ""


def sort_overview_rows(
    rows: list[dict[str, Any]], sort_field: str, sort_asc: bool
) -> list[dict[str, Any]]:
    field = (sort_field or "date").strip() or "date"
    return sorted(
        rows, key=lambda row: overview_sort_key(row, field), reverse=not sort_asc
    )


def finalize_fuel_overview_rows(
    rows: list[dict[str, Any]],
    *,
    column_filters: dict[str, Sequence[str]] | None = None,
    sort_field: str = "date",
    sort_asc: bool = True,
) -> list[dict[str, Any]]:
    rows = apply_overview_column_filters(rows, column_filters)
    return sort_overview_rows(rows, sort_field, sort_asc)


def fuel_column_filter_cache(
    rows: list[dict[str, Any]],
    *,
    column_filters: dict[str, Sequence[str]] | None = None,
    fields: Sequence[str] = FUEL_OVERVIEW_FILTER_FIELDS,
) -> dict[str, list[str]]:
    active = {
        str(k): [str(v) for v in vals]
        for k, vals in (column_filters or {}).items()
        if vals
    }
    out: dict[str, list[str]] = {}
    for field in fields:
        cascaded = {k: v for k, v in active.items() if k != field}
        scoped = apply_overview_column_filters(rows, cascaded or None)
        values = {overview_row_display(row, field) for row in scoped}
        values.discard("")
        out[field] = sorted(values)
    return out


def parse_fuel_column_filters_json(raw: str | None) -> dict[str, list[str]]:
    import json

    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    allowed = set(FUEL_OVERVIEW_FILTER_FIELDS)
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        field = str(key)
        if field not in allowed:
            continue
        if isinstance(value, str):
            vals = [value.strip()] if value.strip() else []
        elif isinstance(value, (list, tuple)):
            vals = [str(v).strip() for v in value if str(v).strip()]
        else:
            continue
        if vals:
            out[field] = vals
    return out


def fuel_cash_rows_from_docs(
    docs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in docs:
        description = str(doc.get("description") or "")
        item = str(doc.get("item") or "")
        category = str(doc.get("category_name") or "")
        rows.append(
            normalized_row(
                source_group="diesel_cash",
                source="Diesel Cash",
                date_value=doc.get("date"),
                truck_value=str(doc.get("truck_number") or ""),
                description=description or item or category or "Diesel cash",
                reference=str(
                    doc.get("memo")
                    or doc.get("do_number")
                    or doc.get("lpo_do")
                    or category
                    or ""
                ),
                currency=str(doc.get("currency") or ""),
                amount=doc.get("amount"),
                liters=parse_liters_from_description(description),
                station=str(doc.get("ownership") or ""),
                receipt_status=str(doc.get("receipt_status") or ""),
            )
        )
    return rows
