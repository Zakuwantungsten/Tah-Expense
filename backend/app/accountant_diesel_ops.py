"""Diesel Cash query helpers (Phase 4.4)."""

from __future__ import annotations

import calendar
import re
from datetime import datetime
from typing import Any, Sequence

DIESEL_CASH_CATEGORY = "Diesel Cash"
DIESEL_CASH_ITEMS_SETTING = "diesel_cash_items"
DIESEL_CASH_CATEGORIES = ("Diesel Cash",)

DIESEL_CASH_SORT_ALLOWED = frozenset(
    {
        "date",
        "description",
        "truck_number",
        "memo",
        "amount",
        "category_name",
        "item",
        "approver",
        "ownership",
        "receipt_status",
    }
)

MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def normalize_diesel_cash_item_names(raw: Any = None) -> list[str]:
    """Unique item names, order preserved. ``None`` → default; ``[]`` stays empty."""
    if raw is None:
        values: Sequence[Any] = DIESEL_CASH_CATEGORIES
    elif isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = DIESEL_CASH_CATEGORIES
    names: list[str] = []
    seen: set[str] = set()
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


def diesel_cash_name_filter(names: Sequence[str] | None = None) -> dict[str, str] | None:
    """Case-insensitive exact match against the configured diesel item names."""
    cleaned = (
        normalize_diesel_cash_item_names(names)
        if names is not None
        else list(DIESEL_CASH_CATEGORIES)
    )
    if not cleaned:
        return None
    alternation = "|".join(
        re.escape(n) for n in sorted(cleaned, key=len, reverse=True)
    )
    return {"$regex": f"^(?:{alternation})$", "$options": "i"}


def diesel_cash_verified_match(names: Sequence[str] | None = None) -> dict[str, Any]:
    """Verified + item/category_name match (cashier or import)."""
    query: dict[str, Any] = {
        "verified": True,
        "deletion_requested": {"$ne": True},
        "trashed": {"$ne": True},
    }
    name_filter = diesel_cash_name_filter(names)
    if name_filter is None:
        query["category_name"] = {"$in": []}
        return query
    query["$or"] = [
        {"category_name": name_filter},
        {"item": name_filter},
    ]
    return query


def _plate_flex_body(compact: str) -> str:
    return r"\s*".join(re.escape(ch) for ch in compact)


def truck_exact_clause(value: str) -> dict[str, str]:
    """Space-insensitive plate match, optionally followed by ``/trailer``."""
    raw = str(value or "").strip()
    if not raw:
        return {"$regex": "^$", "$options": "i"}
    compact = re.sub(r"\s+", "", raw.upper())
    body = _plate_flex_body(compact)
    return {"$regex": rf"^{body}(?=\s*/|$)", "$options": "i"}


def date_range_clause(
    field: str,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any] | None:
    if date_from is None and date_to is None:
        return None
    rng: dict[str, Any] = {}
    if date_from is not None:
        rng["$gte"] = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
    if date_to is not None:
        rng["$lte"] = date_to.replace(hour=23, minute=59, second=59, microsecond=0)
    return {field: rng}


def build_diesel_cash_query(
    year: int | None,
    month: int = 0,
    search: str = "",
    truck: str = "",
    receipt: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    item_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = diesel_cash_verified_match(item_names)

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
            start = max(
                start,
                date_from.replace(hour=0, minute=0, second=0, microsecond=0),
            )
        if date_to is not None:
            end = min(
                end,
                date_to.replace(hour=23, minute=59, second=59, microsecond=0),
            )
        query["date"] = {"$gte": start, "$lte": end}
    else:
        clause = date_range_clause("date", date_from, date_to)
        if clause:
            query.update(clause)

    if search.strip():
        query["description"] = {
            "$regex": re.escape(search.strip()),
            "$options": "i",
        }
    if truck.strip():
        query["truck_number"] = truck_exact_clause(truck)
    if receipt.strip() and receipt != "all":
        query["receipt_status"] = receipt.strip()
    return query


def diesel_cash_sort_clauses(
    sort_field: str = "date",
    sort_asc: bool = False,
) -> list[tuple[str, int]]:
    field = sort_field if sort_field in DIESEL_CASH_SORT_ALLOWED else "date"
    direction = 1 if sort_asc else -1
    clauses: list[tuple[str, int]] = []
    if field == "truck_number":
        clauses.append(("truck_sort_key", direction))
    clauses.append((field, direction))
    if "created_at" not in {c[0] for c in clauses}:
        clauses.append(("created_at", -1))
    return clauses
