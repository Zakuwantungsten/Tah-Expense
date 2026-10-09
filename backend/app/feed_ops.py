"""Imported feeds + separate expenses helpers (Phase 8.1)."""

from __future__ import annotations

import calendar
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from .accountant_master_ops import truck_sort_key
from .excel_dates import format_excel_date, normalize_date_fields, parse_excel_date
from .reconciliation_ops import truck_field_search_regex

# ---------------------------------------------------------------------------
# Feed registry
# ---------------------------------------------------------------------------

COLLECTION_IMPORTED = "imported_feeds"
COLLECTION_SEPARATE = "separate_expenses"

TRUCK_DOC_FIELDS = (
    "truck_number",
    "truck_no",
    "vehicle_reg",
    "vehicle_no",
    "plate_num",
    "truck_reg",
    "reg_no",
    "truck",
)

TOLL_SORT_ALLOWED = frozenset(
    {
        "transaction_date",
        "toll_date",
        "toll_plaza",
        "client_name",
        "card_no",
        "vehicle_reg",
        "vehicle_class",
        "tender_amount",
        "receipt_no",
        "device",
        "lane",
        "cashier_name",
        "import_date",
    }
)

PARKING_CONGO_SORT_ALLOWED = frozenset(
    {
        "transaction_date",
        "payment_date",
        "ledger_id",
        "transaction_type",
        "amount",
        "running_balance",
        "cashier",
        "vehicle_no",
        "direction",
        "gate_in",
        "transaction_details",
        "import_date",
        "sn",
    }
)

RAHNTECH_SORT_ALLOWED = frozenset(
    {
        "transaction_date",
        "sales_date",
        "trip_number",
        "device_number",
        "truck_number",
        "driver_name",
        "do_number",
        "import_date",
        "sn",
    }
)

ZAMBIA_SORT_ALLOWED = frozenset(
    {
        "transaction_date",
        "date",
        "transaction_type",
        "plate_num",
        "ticket_no",
        "debit",
        "credit",
        "balance",
        "heading_to",
        "import_date",
        "row_index",
        "type",
    }
)

DIESEL_SORT_ALLOWED = frozenset(
    {
        "transaction_date",
        "date",
        "lpo_no",
        "do_sdo_no",
        "diesel_at",
        "ownership",
        "clients_name",
        "destinations",
        "truck_no",
        "ltrs",
        "price_per_ltr",
        "total_amount",
        "upload_label",
        "import_date",
    }
)

KIMVI_SORT_ALLOWED = frozenset(
    {
        "expense_date",
        "date_str",
        "truck_no",
        "description",
        "amount_usd",
        "created_at",
        "row_index",
        "serial_no",
        "lpo_no",
    }
)

AFRITRACK_SORT_ALLOWED = frozenset(
    {
        "import_date",
        "period",
        "row_index",
        "truck",
        "days",
        "non_trans_days",
        "trans_days",
        "rate_per_day",
        "total_tahmeed",
        "total_invoice",
        "variance",
        "remarks",
    }
)

COMESA_SORT_ALLOWED = frozenset(
    {
        "name",
        "card_no",
        "valid_from",
        "valid_to",
        "truck_reg",
        "premium",
        "month",
    }
)

THIRD_PARTY_SORT_ALLOWED = frozenset(
    {
        "name",
        "reg_no",
        "premium",
        "vat",
        "total_premium",
        "month",
        "status",
    }
)

TRUCK_SORT_FIELDS = frozenset(TRUCK_DOC_FIELDS)

_IMPORT_UPPER_SKIP = frozenset(
    {
        "feed_type",
        "upload_id",
        "source_filename",
        "upload_label",
        "content_hash",
        "import_date",
        "created_at",
        "transaction_date",
        "expense_date",
        "expense_type",
        "row_index",
        "_id",
        "installation_label",
        "total_payable_label",
        "skipped_row_id",
    }
)

_BLANK_TRUCK_TOKENS = frozenset(
    {"", "-", "–", "—", "−", ".", "n/a", "na", "none"}
)


@dataclass(frozen=True)
class FeedSpec:
    """How a URL feed slug maps onto a Mongo collection + discriminator."""

    slug: str
    collection: str
    disc_field: str  # feed_type | expense_type
    disc_value: str
    truck_field: str
    has_uploads: bool = True
    sort_allowed: frozenset[str] = frozenset()
    default_sort: str = "import_date"
    amount_field: str = "amount"
    legacy_date_field: str = ""
    search_other_fields: tuple[str, ...] = ()


_FEED_SPECS: dict[str, FeedSpec] = {
    "toll-plaza": FeedSpec(
        slug="toll-plaza",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="toll_plaza",
        truck_field="vehicle_reg",
        sort_allowed=TOLL_SORT_ALLOWED,
        default_sort="transaction_date",
        amount_field="tender_amount",
        legacy_date_field="toll_date",
        search_other_fields=(
            "toll_plaza",
            "receipt_no",
            "cashier_name",
            "client_name",
            "card_no",
        ),
    ),
    "parking-congo": FeedSpec(
        slug="parking-congo",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="parking_congo",
        truck_field="vehicle_no",
        sort_allowed=PARKING_CONGO_SORT_ALLOWED,
        default_sort="transaction_date",
        amount_field="amount",
        legacy_date_field="payment_date",
        search_other_fields=(
            "ledger_id",
            "transaction_type",
            "cashier",
            "direction",
            "gate_in",
            "transaction_details",
        ),
    ),
    "rahntech": FeedSpec(
        slug="rahntech",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="rahntech",
        truck_field="truck_number",
        sort_allowed=RAHNTECH_SORT_ALLOWED,
        default_sort="transaction_date",
        amount_field="amount",
        legacy_date_field="sales_date",
        search_other_fields=(
            "trip_number",
            "device_number",
            "driver_name",
            "do_number",
        ),
    ),
    "zambia-parking": FeedSpec(
        slug="zambia-parking",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="zambia_parking",
        truck_field="plate_num",
        sort_allowed=ZAMBIA_SORT_ALLOWED,
        default_sort="transaction_date",
        amount_field="debit",
        legacy_date_field="date",
        search_other_fields=("ticket_no", "transaction_type", "heading_to"),
    ),
    "comesa": FeedSpec(
        slug="comesa",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="comesa",
        truck_field="truck_reg",
        has_uploads=False,
        sort_allowed=COMESA_SORT_ALLOWED,
        default_sort="month",
        amount_field="premium",
        search_other_fields=("name", "card_no"),
    ),
    "third-party": FeedSpec(
        slug="third-party",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="third_party",
        truck_field="reg_no",
        has_uploads=False,
        sort_allowed=THIRD_PARTY_SORT_ALLOWED,
        default_sort="month",
        amount_field="total_premium",
        search_other_fields=("name", "status"),
    ),
    "diesel-infinity": FeedSpec(
        slug="diesel-infinity",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="diesel_infinity",
        truck_field="truck_no",
        sort_allowed=DIESEL_SORT_ALLOWED,
        default_sort="transaction_date",
        amount_field="total_amount",
        legacy_date_field="date",
        search_other_fields=("lpo_no", "do_sdo_no", "clients_name", "destinations"),
    ),
    "diesel-lake-zambia": FeedSpec(
        slug="diesel-lake-zambia",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="diesel_lake_zambia",
        truck_field="truck_no",
        sort_allowed=DIESEL_SORT_ALLOWED,
        default_sort="transaction_date",
        amount_field="total_amount",
        legacy_date_field="date",
        search_other_fields=("lpo_no", "do_sdo_no", "clients_name", "destinations"),
    ),
    "diesel-lake-tunduma": FeedSpec(
        slug="diesel-lake-tunduma",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="diesel_lake_tunduma",
        truck_field="truck_no",
        sort_allowed=DIESEL_SORT_ALLOWED,
        default_sort="transaction_date",
        amount_field="total_amount",
        legacy_date_field="date",
        search_other_fields=("lpo_no", "do_sdo_no", "clients_name", "destinations"),
    ),
    "diesel-gbp": FeedSpec(
        slug="diesel-gbp",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="diesel_gbp",
        truck_field="truck_no",
        sort_allowed=DIESEL_SORT_ALLOWED,
        default_sort="transaction_date",
        amount_field="total_amount",
        legacy_date_field="date",
        search_other_fields=("lpo_no", "do_sdo_no", "clients_name", "destinations"),
    ),
    "kimvi": FeedSpec(
        slug="kimvi",
        collection=COLLECTION_SEPARATE,
        disc_field="expense_type",
        disc_value="ahmed_kimvi",
        truck_field="truck_no",
        sort_allowed=KIMVI_SORT_ALLOWED,
        default_sort="expense_date",
        amount_field="amount_usd",
        search_other_fields=("description", "serial_no", "lpo_no"),
    ),
    "congo": FeedSpec(
        slug="congo",
        collection=COLLECTION_SEPARATE,
        disc_field="expense_type",
        disc_value="congo_expenses",
        truck_field="truck_no",
        sort_allowed=KIMVI_SORT_ALLOWED,
        default_sort="expense_date",
        amount_field="amount_usd",
        search_other_fields=("description", "serial_no", "lpo_no"),
    ),
    "afritrack": FeedSpec(
        slug="afritrack",
        collection=COLLECTION_IMPORTED,
        disc_field="feed_type",
        disc_value="afritrack",
        truck_field="truck",
        sort_allowed=AFRITRACK_SORT_ALLOWED,
        default_sort="import_date",
        amount_field="total_invoice",
        search_other_fields=("period", "remarks"),
    ),
}

VALID_FEEDS = frozenset(_FEED_SPECS.keys())


def validate_feed(feed: str) -> FeedSpec:
    slug = (feed or "").strip().lower()
    spec = _FEED_SPECS.get(slug)
    if spec is None:
        raise ValueError(f"Invalid feed '{feed}'")
    return spec


def feed_collection(db: Any, spec: FeedSpec) -> Any:
    return getattr(db, spec.collection)


# ---------------------------------------------------------------------------
# Shared query helpers
# ---------------------------------------------------------------------------


def safe_double(field: str) -> dict[str, Any]:
    return {
        "$convert": {
            "input": f"${field}",
            "to": "double",
            "onError": 0.0,
            "onNull": 0.0,
        }
    }


def feed_search_clause(
    search: str,
    truck_fields: Sequence[str],
    other_fields: Sequence[str] = (),
) -> Optional[dict[str, Any]]:
    text = (search or "").strip()
    if not text:
        return None
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


def put_search(
    query: dict[str, Any],
    search: str,
    truck_fields: Sequence[str],
    other_fields: Sequence[str] = (),
) -> None:
    clause = feed_search_clause(search, truck_fields, other_fields)
    if clause:
        query.update(clause)


def date_range_clause(
    field: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    if date_from is None and date_to is None:
        return None
    rng: dict[str, Any] = {}
    if date_from is not None:
        rng["$gte"] = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
    if date_to is not None:
        rng["$lte"] = date_to.replace(hour=23, minute=59, second=59, microsecond=0)
    return {field: rng}


def ledger_sort_clauses(
    sort_field: str,
    sort_asc: bool,
    allowed: frozenset[str] | set[str],
    *,
    default: str = "date",
    tie_break: Sequence[tuple[str, int]] = (("created_at", -1),),
) -> list[tuple[str, int]]:
    field = sort_field if sort_field in allowed else default
    direction = 1 if sort_asc else -1
    clauses: list[tuple[str, int]] = []
    if field in TRUCK_SORT_FIELDS:
        clauses.append(("truck_sort_key", direction))
    clauses.append((field, direction))
    for tb_field, tb_dir in tie_break:
        if tb_field not in {c[0] for c in clauses}:
            clauses.append((tb_field, tb_dir))
    return clauses


def stamp_feed_truck_sort_key(doc: dict[str, Any]) -> None:
    for name in TRUCK_DOC_FIELDS:
        val = doc.get(name)
        if val is not None and str(val).strip():
            doc["truck_sort_key"] = truck_sort_key(str(val))
            return
    doc["truck_sort_key"] = truck_sort_key("")


def uppercase_import_text(doc: dict[str, Any]) -> None:
    for key, val in list(doc.items()):
        if key in _IMPORT_UPPER_SKIP:
            continue
        if isinstance(val, str):
            doc[key] = val.upper()


def disc_match(spec: FeedSpec) -> dict[str, Any]:
    return {spec.disc_field: spec.disc_value}


# ---------------------------------------------------------------------------
# Toll / calendar date filters
# ---------------------------------------------------------------------------


def toll_month_regex_patterns(year: int, month: int) -> list[str]:
    abbr = calendar.month_abbr[month]
    full = calendar.month_name[month]
    y, m2 = str(year), f"{month:02d}"
    return [
        rf"{y}-{m2}",
        rf"{y}/{m2}",
        rf"{m2}/{y}",
        rf"{m2}-{y}",
        rf"\b{abbr}\b.*{y}",
        rf"\b{full}\b.*{y}",
        rf"{y}.*\b{abbr}\b",
        rf"{y}.*\b{full}\b",
    ]


def toll_date_filter(year: int, month: int) -> dict[str, Any]:
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1)
            if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {
        "$or": [
            {"transaction_date": {"$gte": start, "$lt": end}},
            {
                "$and": [
                    {
                        "$or": [
                            {"transaction_date": {"$exists": False}},
                            {"transaction_date": None},
                        ]
                    },
                    {
                        "$or": [
                            {"toll_date": {"$regex": p, "$options": "i"}}
                            for p in legacy
                        ]
                    },
                ]
            },
        ]
    }


def transaction_date_year_filter(year: int, month: int = 0) -> dict[str, Any]:
    """Simple calendar filter on ``transaction_date`` (non-toll feeds)."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1)
            if month == 12
            else datetime(year, month + 1, 1)
        )
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
    return {"transaction_date": {"$gte": start, "$lt": end}}


def _legacy_tx_date_filter(
    year: int,
    month: int,
    *,
    legacy_field: str,
) -> dict[str, Any]:
    """Calendar filter on transaction_date with legacy string-date fallback."""
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1)
            if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]

    return {
        "$or": [
            {"transaction_date": {"$gte": start, "$lt": end}},
            {
                "$and": [
                    {
                        "$or": [
                            {"transaction_date": {"$exists": False}},
                            {"transaction_date": None},
                        ]
                    },
                    {
                        "$or": [
                            {legacy_field: {"$regex": p, "$options": "i"}}
                            for p in legacy
                        ]
                    },
                ]
            },
        ]
    }


def toll_plaza_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [{"feed_type": "toll_plaza"}]
    search_clause = feed_search_clause(
        search,
        ("vehicle_reg",),
        ("toll_plaza", "receipt_no", "cashier_name", "client_name", "card_no"),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(toll_date_filter(year, month))
    clause = date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def parking_congo_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [{"feed_type": "parking_congo"}]
    search_clause = feed_search_clause(
        search,
        ("vehicle_no",),
        (
            "ledger_id",
            "transaction_type",
            "cashier",
            "transaction_details",
            "direction",
        ),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(
            _legacy_tx_date_filter(year, month, legacy_field="payment_date")
        )
    clause = date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def parking_congo_upload_query(upload_id: str, search: str = "") -> dict[str, Any]:
    query: dict[str, Any] = {"feed_type": "parking_congo", "upload_id": upload_id}
    put_search(
        query,
        search,
        ("vehicle_no",),
        (
            "ledger_id",
            "transaction_type",
            "cashier",
            "transaction_details",
            "direction",
        ),
    )
    return query


def parking_congo_uploads_pipeline() -> list[dict[str, Any]]:
    return [
        {"$match": {"feed_type": "parking_congo"}},
        {
            "$group": {
                "_id": "$upload_id",
                "source_filename": {"$first": "$source_filename"},
                "import_date": {"$first": "$import_date"},
                "record_count": {"$sum": 1},
                "min_date": {"$min": "$payment_date"},
                "max_date": {"$max": "$payment_date"},
            }
        },
        {"$sort": {"import_date": -1}},
    ]


def parking_congo_totals_pipeline(query: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "count": {"$sum": 1},
                "amount": {"$sum": safe_double("amount")},
            }
        },
    ]


def empty_parking_totals() -> dict[str, Any]:
    return {"count": 0, "amount": 0.0}


def parking_congo_deposit_clause() -> dict[str, Any]:
    return {
        "$or": [
            {"is_deposit": True},
            {"transaction_type": {"$regex": r"^deposit$", "$options": "i"}},
        ]
    }


def parking_congo_deposits_query(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [
        {"feed_type": "parking_congo"},
        parking_congo_deposit_clause(),
    ]
    if search.strip():
        s = re.escape(search.strip())
        clauses.append(
            {
                "$or": [
                    {"ledger_id": {"$regex": s, "$options": "i"}},
                    {"cashier": {"$regex": s, "$options": "i"}},
                    {"transaction_details": {"$regex": s, "$options": "i"}},
                    {"source_filename": {"$regex": s, "$options": "i"}},
                    {"payment_date": {"$regex": s, "$options": "i"}},
                ]
            }
        )
    clause = date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    return {"$and": clauses}


def toll_upload_query(upload_id: str, search: str = "") -> dict[str, Any]:
    query: dict[str, Any] = {"feed_type": "toll_plaza", "upload_id": upload_id}
    put_search(
        query,
        search,
        ("vehicle_reg",),
        ("toll_plaza", "receipt_no", "cashier_name", "client_name", "card_no"),
    )
    return query


def toll_uploads_pipeline() -> list[dict[str, Any]]:
    return [
        {"$match": {"feed_type": "toll_plaza"}},
        {
            "$group": {
                "_id": "$upload_id",
                "source_filename": {"$first": "$source_filename"},
                "import_date": {"$first": "$import_date"},
                "record_count": {"$sum": 1},
                "total_zmw": {"$sum": safe_double("tender_amount")},
                "min_toll_date": {"$min": "$toll_date"},
                "max_toll_date": {"$max": "$toll_date"},
            }
        },
        {"$sort": {"import_date": -1}},
    ]


def toll_totals_pipeline(query: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "count": {"$sum": 1},
                "total_zmw": {"$sum": safe_double("tender_amount")},
            }
        },
    ]


def empty_toll_totals() -> dict[str, Any]:
    return {"count": 0, "total_zmw": 0.0}


def existing_feed_keys_query(keys: Sequence[str]) -> dict[str, Any]:
    return {
        "$or": [
            {"receipt_no": {"$in": list(keys)}},
            {"serial": {"$in": list(keys)}},
            {"ticket_no": {"$in": list(keys)}},
            {"lpo_no": {"$in": list(keys)}},
            {"ledger_id": {"$in": list(keys)}},
            {"trip_number": {"$in": list(keys)}},
        ]
    }


def collect_existing_feed_keys(docs: Sequence[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for doc in docs:
        for field in (
            "receipt_no",
            "serial",
            "ticket_no",
            "lpo_no",
            "ledger_id",
            "trip_number",
        ):
            v = doc.get(field)
            if v:
                found.add(str(v))
    return found


# ---------------------------------------------------------------------------
# Save / normalize imported feed rows
# ---------------------------------------------------------------------------


def _clear_blank_tokens(doc: dict[str, Any], *fields: str) -> None:
    for key in fields:
        val = str(doc.get(key, "") or "").strip()
        if val.lower() in _BLANK_TRUCK_TOKENS:
            doc[key] = ""


def prepare_imported_feed_doc(
    rec: dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Normalize one staged imported_feeds row before insert."""
    stamp = now or datetime.utcnow()
    doc = dict(rec)
    doc.pop("_raw", None)
    doc["import_date"] = stamp
    feed_type = str(doc.get("feed_type") or "")

    if feed_type == "toll_plaza":
        normalize_date_fields(doc, "toll_date", store_as="transaction_date")
    elif feed_type == "parking_congo":
        normalize_date_fields(doc, "payment_date", store_as="transaction_date")
        tt = str(doc.get("transaction_type", "") or "").strip().lower()
        if tt == "deposit" or doc.get("is_deposit"):
            doc["is_deposit"] = True
            _clear_blank_tokens(doc, "vehicle_no", "direction", "gate_in")
    elif feed_type == "rahntech":
        normalize_date_fields(doc, "sales_date", store_as="transaction_date")
    elif feed_type == "zambia_parking":
        if isinstance(doc.get("transaction_date"), datetime):
            pretty = format_excel_date(doc["transaction_date"], "%d %b %Y")
            if pretty:
                doc["date"] = pretty
        else:
            normalize_date_fields(
                doc, "date", "transaction_date", store_as="transaction_date"
            )
    elif feed_type.startswith("diesel_"):
        normalize_date_fields(doc, "date", store_as="transaction_date")
        apply_diesel_computed_fields(doc)
        if feed_type == "diesel_lake_zambia":
            cur = str(doc.get("currency") or "").strip().upper()
            doc["currency"] = cur if cur in {"USD", "ZMW", "TZS"} else "USD"

    uppercase_import_text(doc)
    stamp_feed_truck_sort_key(doc)
    return doc


def years_from_transaction_dates(docs: Sequence[dict[str, Any]]) -> list[int]:
    years: set[int] = set()
    for doc in docs:
        yr = doc.get("_id")
        if isinstance(yr, int) and 1990 <= yr <= 2100:
            years.add(yr)
    return sorted(years, reverse=True)


def years_from_legacy_dates(values: Sequence[Any]) -> list[int]:
    years: set[int] = set()
    for val in values:
        parsed = parse_excel_date(val)
        if parsed and 1990 <= parsed.year <= 2100:
            years.add(parsed.year)
    return sorted(years, reverse=True)


# ---------------------------------------------------------------------------
# RahnTech
# ---------------------------------------------------------------------------


def rahntech_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [{"feed_type": "rahntech"}]
    search_clause = feed_search_clause(
        search,
        ("truck_number",),
        ("driver_name", "trip_number", "device_number", "do_number", "sales_date"),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_legacy_tx_date_filter(year, month, legacy_field="sales_date"))
    clause = date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def rahntech_upload_query(upload_id: str, search: str = "") -> dict[str, Any]:
    query: dict[str, Any] = {"feed_type": "rahntech", "upload_id": upload_id}
    put_search(
        query,
        search,
        ("truck_number",),
        ("driver_name", "trip_number", "device_number", "do_number"),
    )
    return query


def rahntech_uploads_pipeline() -> list[dict[str, Any]]:
    return [
        {
            "$match": {
                "feed_type": "rahntech",
                "upload_id": {"$exists": True, "$ne": ""},
            }
        },
        {
            "$group": {
                "_id": "$upload_id",
                "source_filename": {"$first": "$source_filename"},
                "import_date": {"$first": "$import_date"},
                "record_count": {"$sum": 1},
                "min_sales_date": {"$min": "$sales_date"},
                "max_sales_date": {"$max": "$sales_date"},
            }
        },
        {"$sort": {"import_date": -1}},
    ]


def rahntech_totals_pipeline(query: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"$match": query},
        {"$group": {"_id": None, "count": {"$sum": 1}}},
    ]


def empty_rahntech_totals() -> dict[str, Any]:
    return {"count": 0}


# ---------------------------------------------------------------------------
# Zambia parking
# ---------------------------------------------------------------------------


def _zambia_hash_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value).strip().upper()


def zambia_batch_content_hash(records: Sequence[dict[str, Any]]) -> str:
    lines: list[str] = []
    for rec in records:
        lines.append(
            "|".join(
                [
                    _zambia_hash_field(rec.get("date")),
                    _zambia_hash_field(rec.get("type")),
                    _zambia_hash_field(rec.get("plate_num")),
                    _zambia_hash_field(rec.get("ticket_no")),
                    _zambia_hash_field(rec.get("debit")),
                    _zambia_hash_field(rec.get("credit")),
                    _zambia_hash_field(rec.get("balance")),
                    _zambia_hash_field(rec.get("heading_to")),
                ]
            )
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def norm_sheet_label(label: str) -> str:
    return str(label or "").strip().upper()


def zambia_parking_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    credit_only: bool = False,
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [{"feed_type": "zambia_parking"}]
    if credit_only:
        clauses.append({"credit": {"$gt": 0}})
    search_clause = feed_search_clause(
        search,
        ("plate_num",),
        ("ticket_no", "heading_to", "type", "date"),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_legacy_tx_date_filter(year, month, legacy_field="date"))
    clause = date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def zambia_parking_upload_query(upload_id: str, search: str = "") -> dict[str, Any]:
    query: dict[str, Any] = {"feed_type": "zambia_parking", "upload_id": upload_id}
    put_search(
        query,
        search,
        ("plate_num",),
        ("ticket_no", "heading_to", "type", "date"),
    )
    return query


def zambia_parking_uploads_pipeline() -> list[dict[str, Any]]:
    return [
        {
            "$match": {
                "feed_type": "zambia_parking",
                "upload_id": {"$exists": True, "$ne": ""},
            }
        },
        {"$sort": {"row_index": 1}},
        {
            "$group": {
                "_id": "$upload_id",
                "sheet_label": {"$first": "$sheet_label"},
                "source_filename": {"$first": "$source_filename"},
                "import_date": {"$first": "$import_date"},
                "record_count": {"$sum": 1},
                "total_debit": {"$sum": safe_double("debit")},
                "total_credit": {"$sum": safe_double("credit")},
                "closing_balance": {"$last": safe_double("balance")},
                "min_transaction_date": {"$min": "$transaction_date"},
                "max_transaction_date": {"$max": "$transaction_date"},
            }
        },
        {"$sort": {"import_date": -1}},
    ]


def zambia_parking_totals_pipeline(query: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "count": {"$sum": 1},
                "total_debit": {"$sum": safe_double("debit")},
                "total_credit": {"$sum": safe_double("credit")},
            }
        },
    ]


def empty_zambia_totals() -> dict[str, Any]:
    return {
        "count": 0,
        "total_debit": 0.0,
        "total_credit": 0.0,
        "balance_zmw": 0.0,
    }


def finalize_zambia_totals(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return empty_zambia_totals()
    out = dict(row)
    out["balance_zmw"] = float(out.get("total_credit") or 0) - float(
        out.get("total_debit") or 0
    )
    return out


def stamp_zambia_content_hashes(docs: list[dict[str, Any]]) -> None:
    zambia_docs = [d for d in docs if d.get("feed_type") == "zambia_parking"]
    if zambia_docs and not str(zambia_docs[0].get("content_hash") or "").strip():
        batch_hash = zambia_batch_content_hash(zambia_docs)
        for d in zambia_docs:
            d["content_hash"] = batch_hash


def prepare_imported_feed_docs(
    records: Sequence[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    stamp = now or datetime.utcnow()
    docs = [prepare_imported_feed_doc(rec, now=stamp) for rec in records]
    stamp_zambia_content_hashes(docs)
    stamp_diesel_content_hashes(docs)
    return docs


# ---------------------------------------------------------------------------
# Insurance (comesa / third_party) — flat list, no upload batches
# ---------------------------------------------------------------------------


def insurance_query(
    feed_type: str,
    search: str = "",
    month: str = "",
    status: str = "",
) -> dict[str, Any]:
    query: dict[str, Any] = {"feed_type": feed_type}
    put_search(query, search, ("truck_reg", "reg_no"), ("name", "card_no"))
    if month.strip() and month.upper() != "ALL":
        query["month"] = {
            "$regex": re.escape(month.strip()),
            "$options": "i",
        }
    if status.strip() and status.upper() != "ALL":
        query["status"] = status.strip()
    return query


def insurance_totals_pipeline(query: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "premium": {
                    "$sum": {
                        "$toDouble": {"$ifNull": ["$premium", 0]}
                    }
                },
                "vat": {"$sum": {"$toDouble": {"$ifNull": ["$vat", 0]}}},
                "total_premium": {
                    "$sum": {
                        "$toDouble": {"$ifNull": ["$total_premium", 0]}
                    }
                },
                "count": {"$sum": 1},
            }
        },
    ]


def empty_insurance_totals() -> dict[str, Any]:
    return {"premium": 0.0, "vat": 0.0, "total_premium": 0.0, "count": 0}


def insurance_existing_keys_query(
    feed_type: str, keys: Sequence[str]
) -> dict[str, Any]:
    return {"feed_type": feed_type, "dedup_id": {"$in": list(keys)}}


# ---------------------------------------------------------------------------
# Diesel feeds
# ---------------------------------------------------------------------------

def parse_diesel_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in ("none", "n/a", "na", "-", "—"):
        return None
    text = text.replace(" ", "").replace("%", "")
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif text.count(",") > 1:
        text = text.replace(",", "")
    elif "," in text:
        _left, _, right = text.partition(",")
        text = text.replace(",", ".") if len(right) != 3 else text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def diesel_line_total(ltrs: Any, rate: Any) -> float:
    litres = parse_diesel_number(ltrs)
    price = parse_diesel_number(rate)
    if litres is None or price is None:
        return 0.0
    return round(litres * price, 2)


def apply_diesel_computed_fields(rec: dict[str, Any]) -> None:
    rec["sn"] = ""
    litres = parse_diesel_number(rec.get("ltrs"))
    rate = parse_diesel_number(rec.get("price_per_ltr"))
    if litres is not None:
        rec["ltrs"] = litres
    if rate is not None:
        rec["price_per_ltr"] = rate
    rec["total_amount"] = diesel_line_total(litres, rate)


_DIESEL_HASH_FIELDS = (
    "date",
    "lpo_no",
    "do_sdo_no",
    "diesel_at",
    "ownership",
    "clients_name",
    "destinations",
    "truck_no",
    "ltrs",
    "price_per_ltr",
    "total_amount",
)

DIESEL_SLUGS = frozenset(
    {
        "diesel-infinity",
        "diesel-lake-zambia",
        "diesel-lake-tunduma",
        "diesel-gbp",
    }
)


def diesel_computed_amount() -> dict[str, Any]:
    return {"$multiply": [safe_double("ltrs"), safe_double("price_per_ltr")]}


def diesel_batch_content_hash(records: Sequence[dict[str, Any]]) -> str:
    lines: list[str] = []
    for rec in records:
        lines.append(
            "|".join(
                _zambia_hash_field(rec.get(field)) for field in _DIESEL_HASH_FIELDS
            )
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def stamp_diesel_content_hashes(docs: list[dict[str, Any]]) -> None:
    diesel_docs = [
        d for d in docs if str(d.get("feed_type") or "").startswith("diesel_")
    ]
    if diesel_docs and not str(diesel_docs[0].get("content_hash") or "").strip():
        batch_hash = diesel_batch_content_hash(diesel_docs)
        for d in diesel_docs:
            d["content_hash"] = batch_hash


def unique_diesel_file_labels(labels: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in labels:
        label = str(raw or "").strip()
        key = label.lower()
        if label and key not in seen:
            seen.add(key)
            out.append(label)
    out.sort(key=str.lower)
    return out


def _diesel_empty_upload_label_clause() -> dict[str, Any]:
    return {
        "$or": [
            {"upload_label": {"$exists": False}},
            {"upload_label": None},
            {"upload_label": {"$regex": r"^\s*$"}},
        ]
    }


def diesel_file_labels_clause(
    file_labels: Sequence[str] | None = None,
) -> Optional[dict[str, Any]]:
    labels = unique_diesel_file_labels(file_labels or [])
    if not labels:
        return None
    empty = _diesel_empty_upload_label_clause()
    branches: list[dict[str, Any]] = []
    for label in labels:
        exact = {"$regex": f"^{re.escape(label)}$", "$options": "i"}
        branches.append({"upload_label": exact})
        branches.append({"$and": [empty, {"source_filename": exact}]})
    if len(branches) == 1:
        return branches[0]
    return {"$or": branches}


def diesel_all_query(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    file_labels: Sequence[str] | None = None,
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [{"feed_type": feed_type}]
    search_clause = feed_search_clause(
        search,
        ("truck_no",),
        (
            "lpo_no",
            "do_sdo_no",
            "clients_name",
            "destinations",
            "diesel_at",
            "ownership",
            "source_filename",
            "upload_label",
            "date",
        ),
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_legacy_tx_date_filter(year, month, legacy_field="date"))
    clause = date_range_clause("transaction_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    labels_clause = diesel_file_labels_clause(file_labels)
    if labels_clause:
        clauses.append(labels_clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def diesel_upload_query(
    feed_type: str, upload_id: str, search: str = ""
) -> dict[str, Any]:
    query: dict[str, Any] = {"feed_type": feed_type, "upload_id": upload_id}
    put_search(
        query,
        search,
        ("truck_no",),
        (
            "lpo_no",
            "do_sdo_no",
            "clients_name",
            "destinations",
            "diesel_at",
            "upload_label",
            "source_filename",
        ),
    )
    return query


def diesel_uploads_pipeline(feed_type: str) -> list[dict[str, Any]]:
    return [
        {
            "$match": {
                "feed_type": feed_type,
                "upload_id": {"$exists": True, "$ne": ""},
            }
        },
        {
            "$group": {
                "_id": "$upload_id",
                "source_filename": {"$first": "$source_filename"},
                "upload_label": {"$first": "$upload_label"},
                "sheet_label": {"$first": "$sheet_label"},
                "import_date": {"$first": "$import_date"},
                "currency": {"$first": "$currency"},
                "record_count": {"$sum": 1},
                "ltrs": {"$sum": safe_double("ltrs")},
                "total_amount": {"$sum": diesel_computed_amount()},
                "min_date": {"$min": "$date"},
                "max_date": {"$max": "$date"},
            }
        },
        {"$sort": {"import_date": -1}},
    ]


def diesel_totals_pipeline(query: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "count": {"$sum": 1},
                "ltrs": {"$sum": safe_double("ltrs")},
                "total_amount": {"$sum": diesel_computed_amount()},
            }
        },
    ]


def diesel_all_totals_pipeline(query: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"$match": query},
        {
            "$facet": {
                "summary": [
                    {
                        "$group": {
                            "_id": None,
                            "count": {"$sum": 1},
                            "ltrs": {"$sum": safe_double("ltrs")},
                            "total_amount": {"$sum": diesel_computed_amount()},
                        }
                    }
                ],
                "by_currency": [
                    {
                        "$group": {
                            "_id": "$currency",
                            "amount": {"$sum": diesel_computed_amount()},
                        }
                    }
                ],
            }
        },
    ]


def empty_diesel_totals() -> dict[str, Any]:
    return {"count": 0, "ltrs": 0.0, "total_amount": 0.0}


def finalize_diesel_totals(
    facet_row: dict[str, Any] | None,
    *,
    feed_type: str,
) -> dict[str, Any]:
    if not facet_row:
        return empty_diesel_totals()
    summary = (facet_row.get("summary") or [{}])[0] or {}
    out = {
        "count": int(summary.get("count") or 0),
        "ltrs": float(summary.get("ltrs") or 0.0),
        "total_amount": float(summary.get("total_amount") or 0.0),
    }
    if feed_type == "diesel_lake_zambia":
        by_cur: dict[str, float] = {}
        for row in facet_row.get("by_currency") or []:
            cur = str(row.get("_id") or "USD").strip().upper() or "USD"
            by_cur[cur] = by_cur.get(cur, 0.0) + float(row.get("amount") or 0)
        out["amount_by_currency"] = by_cur
    return out


def diesel_file_labels_pipeline(feed_type: str) -> list[dict[str, Any]]:
    return [
        {"$match": {"feed_type": feed_type}},
        {
            "$project": {
                "label": {
                    "$let": {
                        "vars": {
                            "ul": {
                                "$trim": {
                                    "input": {"$ifNull": ["$upload_label", ""]}
                                }
                            },
                            "fn": {
                                "$trim": {
                                    "input": {
                                        "$ifNull": ["$source_filename", ""]
                                    }
                                }
                            },
                        },
                        "in": {
                            "$cond": [
                                {"$ne": ["$$ul", ""]},
                                "$$ul",
                                "$$fn",
                            ]
                        },
                    }
                }
            }
        },
        {"$match": {"label": {"$nin": ["", None]}}},
        {"$group": {"_id": "$label"}},
        {"$sort": {"_id": 1}},
    ]


def diesel_filename_sheet_query(
    feed_type: str,
    source_filename: str,
    sheet_label: str = "",
) -> Optional[dict[str, Any]]:
    fname = str(source_filename or "").strip()
    if not feed_type or not fname:
        return None
    query: dict[str, Any] = {
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


# ---------------------------------------------------------------------------
# Separate expenses (Kimvi / Congo) — USD ledger
# ---------------------------------------------------------------------------


def usd_amount_clause(
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> Optional[dict[str, Any]]:
    if money_in_only:
        return {"amount_usd": {"$lt": 0}}
    if called_out_only:
        return {"$nor": [{"amount_usd": {"$lt": 0}}]}
    return None


def _expense_date_filter(year: int, month: int) -> dict[str, Any]:
    if month >= 1:
        start = datetime(year, month, 1)
        end = (
            datetime(year + 1, 1, 1)
            if month == 12
            else datetime(year, month + 1, 1)
        )
        legacy = toll_month_regex_patterns(year, month)
    else:
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)
        legacy = [str(year)]
    return {
        "$or": [
            {"expense_date": {"$gte": start, "$lt": end}},
            {
                "$and": [
                    {
                        "$or": [
                            {"expense_date": {"$exists": False}},
                            {"expense_date": None},
                        ]
                    },
                    {
                        "$or": [
                            {"date_str": {"$regex": p, "$options": "i"}}
                            for p in legacy
                        ]
                    },
                ]
            },
        ]
    }


def usd_ledger_all_query(
    expense_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [{"expense_type": expense_type}]
    amount_clause = usd_amount_clause(money_in_only, called_out_only)
    if amount_clause:
        clauses.append(amount_clause)
    search_clause = feed_search_clause(
        search, ("truck_no",), ("description", "date_str")
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        clauses.append(_expense_date_filter(year, month))
    clause = date_range_clause("expense_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def usd_ledger_upload_query(
    expense_type: str, upload_id: str, search: str = ""
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "expense_type": expense_type,
        "upload_id": upload_id,
    }
    put_search(query, search, ("truck_no",), ("description", "date_str"))
    return query


def usd_ledger_uploads_pipeline(expense_type: str) -> list[dict[str, Any]]:
    return [
        {
            "$match": {
                "expense_type": expense_type,
                "upload_id": {"$exists": True, "$ne": ""},
            }
        },
        {
            "$group": {
                "_id": "$upload_id",
                "sheet_label": {"$first": "$sheet_label"},
                "source_filename": {"$first": "$source_filename"},
                "import_date": {"$first": "$import_date"},
                "record_count": {"$sum": 1},
                "balance_usd": {"$sum": {"$ifNull": ["$amount_usd", 0]}},
                "money_in": {
                    "$sum": {
                        "$cond": [
                            {"$lt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                            {"$ifNull": ["$amount_usd", 0]},
                            0,
                        ]
                    }
                },
                "money_out": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                            {"$ifNull": ["$amount_usd", 0]},
                            0,
                        ]
                    }
                },
                "min_expense_date": {"$min": "$expense_date"},
                "max_expense_date": {"$max": "$expense_date"},
            }
        },
        {"$sort": {"import_date": -1}},
    ]


def usd_ledger_totals_pipeline(query: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "count": {"$sum": 1},
                "money_in": {
                    "$sum": {
                        "$cond": [
                            {"$lt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                            {"$ifNull": ["$amount_usd", 0]},
                            0,
                        ]
                    }
                },
                "money_out": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$amount_usd", 0]}, 0]},
                            {"$ifNull": ["$amount_usd", 0]},
                            0,
                        ]
                    }
                },
            }
        },
    ]


def empty_usd_ledger_totals() -> dict[str, Any]:
    return {"count": 0, "money_in": 0.0, "money_out": 0.0, "balance_usd": 0.0}


def finalize_usd_ledger_totals(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return empty_usd_ledger_totals()
    out = dict(row)
    out["balance_usd"] = float(out.get("money_in") or 0) + float(
        out.get("money_out") or 0
    )
    return out


def prepare_separate_expense_docs(
    records: Sequence[dict[str, Any]],
    *,
    expense_type: str,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    stamp = now or datetime.utcnow()
    docs: list[dict[str, Any]] = []
    for rec in records:
        doc = dict(rec)
        doc.pop("_raw", None)
        doc["expense_type"] = expense_type
        doc["import_date"] = stamp
        doc["created_at"] = stamp
        if not isinstance(doc.get("expense_date"), datetime):
            normalize_date_fields(
                doc,
                "date_str",
                "expense_date",
                store_as="expense_date",
                display_fmt="%d %b %Y",
            )
            if isinstance(doc.get("expense_date"), datetime):
                doc["date_str"] = format_excel_date(
                    doc["expense_date"], "%d %b %Y"
                )
        uppercase_import_text(doc)
        stamp_feed_truck_sort_key(doc)
        docs.append(doc)
    return docs


# ---------------------------------------------------------------------------
# Afritrack
# ---------------------------------------------------------------------------


def afritrack_all_query(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [{"feed_type": "afritrack"}]
    search_clause = feed_search_clause(
        search, ("truck",), ("remarks", "period", "source_filename")
    )
    if search_clause:
        clauses.append(search_clause)
    if year > 0:
        if month >= 1:
            start = datetime(year, month, 1)
            end = (
                datetime(year + 1, 1, 1)
                if month == 12
                else datetime(year, month + 1, 1)
            )
        else:
            start = datetime(year, 1, 1)
            end = datetime(year + 1, 1, 1)
        clauses.append({"import_date": {"$gte": start, "$lt": end}})
    clause = date_range_clause("import_date", date_from, date_to)
    if clause:
        clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def afritrack_upload_query(upload_id: str, search: str = "") -> dict[str, Any]:
    query: dict[str, Any] = {"feed_type": "afritrack", "upload_id": upload_id}
    put_search(query, search, ("truck",), ("remarks", "period"))
    return query


def afritrack_uploads_pipeline() -> list[dict[str, Any]]:
    return [
        {
            "$match": {
                "feed_type": "afritrack",
                "upload_id": {"$exists": True, "$ne": ""},
            }
        },
        {
            "$group": {
                "_id": "$upload_id",
                "source_filename": {"$first": "$source_filename"},
                "import_date": {"$first": "$import_date"},
                "period": {"$first": "$period"},
                "record_count": {"$sum": 1},
                "total_tahmeed": {"$sum": safe_double("total_tahmeed")},
                "total_invoice": {"$sum": safe_double("total_invoice")},
                "total_variance": {"$sum": safe_double("variance")},
                "installation_tahmeed": {"$first": "$installation_tahmeed"},
                "installation_invoice": {"$first": "$installation_invoice"},
                "balance_mar": {"$first": "$balance_mar"},
                "vat_rate": {"$first": "$vat_rate"},
                "installation_label": {"$first": "$installation_label"},
                "total_payable_label": {"$first": "$total_payable_label"},
                "statement_tahmeed": {"$first": "$statement_tahmeed"},
                "statement_invoice": {"$first": "$statement_invoice"},
            }
        },
        {"$sort": {"import_date": -1}},
    ]


def afritrack_totals_pipeline(query: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "count": {"$sum": 1},
                "total_tahmeed": {"$sum": safe_double("total_tahmeed")},
                "total_invoice": {"$sum": safe_double("total_invoice")},
                "total_variance": {"$sum": safe_double("variance")},
            }
        },
    ]


def empty_afritrack_totals() -> dict[str, Any]:
    return {
        "count": 0,
        "total_tahmeed": 0.0,
        "total_invoice": 0.0,
        "total_variance": 0.0,
    }


# ---------------------------------------------------------------------------
# Skipped import rows (fleet gate park-for-follow-up)
# ---------------------------------------------------------------------------

COLLECTION_SKIPPED = "skipped_import_rows"

RECON_SKIPPED_FEED_KEYS = frozenset({"rpa_schedule", "bonds", "sm_burhani"})
SEPARATE_SKIPPED_FEED_KEYS = frozenset({"congo_expenses", "ahmed_kimvi"})


def skipped_import_search_clause(term: str) -> dict[str, Any]:
    """Build ``$or`` search across parked-row metadata and nested record fields."""
    rx = {"$regex": re.escape(term), "$options": "i"}
    clauses: list[dict[str, Any]] = [
        {"truck_value": rx},
        {"original_truck": rx},
        {"source_filename": rx},
        {"sheet_label": rx},
        {"target_upload_id": rx},
        {"reason": rx},
        {"record.receipt_no": rx},
        {"record.ledger_id": rx},
        {"record.lpo_no": rx},
        {"record.serial": rx},
        {"record.toll_plaza": rx},
        {"record.vehicle_reg": rx},
        {"record.vehicle_no": rx},
        {"record.plate_num": rx},
        {"record.client_name": rx},
        {"record.cashier_name": rx},
        {"record.description": rx},
        {"record.transaction_details": rx},
    ]
    if term.isdigit():
        clauses.append({"source_row": int(term)})
    return {"$or": clauses}


def skipped_import_query(feed_key: str, search: str = "") -> dict[str, Any]:
    query: dict[str, Any] = {"feed_key": feed_key}
    term = (search or "").strip()
    if term:
        query.update(skipped_import_search_clause(term))
    return query


def prepare_skipped_import_docs(
    docs: Sequence[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    stamp = now or datetime.utcnow()
    payload: list[dict[str, Any]] = []
    for doc in docs:
        item = dict(doc)
        item["skipped_at"] = stamp
        payload.append(item)
    return payload


def apply_skipped_truck_update(
    doc: dict[str, Any], truck_value: str
) -> dict[str, Any]:
    """Return ``$set`` fields after editing the parked truck value."""
    field = doc.get("truck_field") or "truck_no"
    record = dict(doc.get("record") or {})
    record[field] = truck_value
    return {"truck_value": truck_value, "record": record}


def group_skipped_records_for_reupload(
    docs: Sequence[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build feed_key → records ready to insert into the original upload batch."""
    feed_records: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        rec = dict(doc.get("record") or {})
        rec.pop("_raw", None)
        upload_id = doc.get("target_upload_id") or rec.get("upload_id") or ""
        rec["upload_id"] = upload_id
        rec["skipped_row_id"] = str(doc["_id"])
        if doc.get("source_filename") and not rec.get("source_filename"):
            rec["source_filename"] = doc["source_filename"]
        if doc.get("sheet_label") and not rec.get("sheet_label"):
            rec["sheet_label"] = doc["sheet_label"]
        field = doc.get("truck_field")
        if field:
            rec[field] = doc.get("truck_value") or rec.get(field, "")
        feed_key = str(doc.get("feed_key") or "")
        feed_records.setdefault(feed_key, []).append(rec)
    return feed_records
