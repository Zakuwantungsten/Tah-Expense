"""Shared accountant verify-inbox query helpers (Phase 3)."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from typing import Any, Sequence

from bson import ObjectId

from .transaction_ops import normalize_multi_filter

UNVERIFIED_INBOX_SORT = [("date", -1), ("day_order", 1), ("_id", 1)]
EDITED_INBOX_SORT = [("last_edited_at", -1), ("date", -1)]


def to_day_key(value: Any) -> str | None:
    """Normalize a stored date/datetime to ``YYYY-MM-DD`` (UTC-naive calendar day)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    # Accept ISO timestamps / date-only strings.
    try:
        if "T" in text or " " in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def format_available_dates(raw_values: Sequence[Any]) -> list[dict[str, str]]:
    """Unique expense days from distinct() values, newest first."""
    days = sorted(
        {key for key in (to_day_key(v) for v in raw_values) if key},
        reverse=True,
    )
    return [{"date": day} for day in days]


def inbox_base_query(*, rejected: bool = False) -> dict[str, Any]:
    if rejected:
        return {
            "rejected": True,
            "discarded": {"$ne": True},
            "trashed": {"$ne": True},
        }
    return {
        "verified": False,
        "register_status": "submitted",
        "rejected": {"$ne": True},
        "trashed": {"$ne": True},
        "deletion_requested": {"$ne": True},
    }


def _plate_flex_body(compact: str) -> str:
    return r"\s*".join(re.escape(ch) for ch in compact)


def inbox_search_clause(search: str) -> dict[str, Any] | None:
    """Plate-aware truck search, else substring across inbox text fields."""
    text = (search or "").strip()
    if not text:
        return None
    digits = re.match(r"^T?\s*(\d+)$", text, re.I)
    if digits:
        body = _plate_flex_body(digits.group(1))
        return {
            "truck_number": {
                "$regex": rf"^T?\s*{body}(?=\s*[A-Z]|\s*/|$)",
                "$options": "i",
            }
        }
    plate = re.match(r"^T\s*\d+\s*[A-Z]{2,3}$", text, re.I)
    if plate:
        compact = re.sub(r"\s+", "", text.upper())
        body = _plate_flex_body(compact)
        return {
            "truck_number": {
                "$regex": rf"^{body}(?=\s*/|$)",
                "$options": "i",
            }
        }
    escaped = re.escape(text)
    fields = ("truck_number", "description", "item", "category_name")
    return {
        "$or": [{field: {"$regex": escaped, "$options": "i"}} for field in fields]
    }


def append_text_filters(
    and_clauses: list[dict[str, Any]],
    *,
    search: str = "",
    item: str | Sequence[str] | None = "",
    description: str | Sequence[str] | None = "",
) -> None:
    clause = inbox_search_clause(search)
    if clause:
        and_clauses.append(clause)

    descs = normalize_multi_filter(description)
    if descs:
        if isinstance(description, str) and "," not in description:
            and_clauses.append(
                {
                    "description": {
                        "$regex": re.escape(descs[0]),
                        "$options": "i",
                    },
                }
            )
        else:
            and_clauses.append(
                {
                    "$or": [
                        {
                            "description": {
                                "$regex": f"^{re.escape(d)}$",
                                "$options": "i",
                            }
                        }
                        for d in descs
                    ]
                }
            )

    items = normalize_multi_filter(item)
    if items:
        item_ors: list[dict[str, Any]] = []
        for it in items:
            esc = re.escape(it)
            item_ors.append({"item": {"$regex": f"^{esc}$", "$options": "i"}})
            item_ors.append(
                {"category_name": {"$regex": f"^{esc}$", "$options": "i"}}
            )
        and_clauses.append({"$or": item_ors})


def build_inbox_query(
    *,
    search: str = "",
    truck: str = "",
    cashier_id: ObjectId | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    edited: bool | None = None,
    item: str | Sequence[str] | None = "",
    description: str | Sequence[str] | None = "",
) -> dict[str, Any]:
    query: dict[str, Any] = dict(inbox_base_query(rejected=False))
    if edited is True:
        query["edited_after_verification"] = True
    elif edited is False:
        query["edited_after_verification"] = {"$ne": True}

    and_clauses: list[dict[str, Any]] = []
    append_text_filters(
        and_clauses, search=search, item=item, description=description
    )
    if and_clauses:
        query["$and"] = and_clauses

    if truck.strip():
        query["truck_number"] = {
            "$regex": re.escape(truck.strip()),
            "$options": "i",
        }
    if cashier_id is not None:
        query["cashier_id"] = cashier_id
    if date_from or date_to:
        df: dict[str, Any] = {}
        if date_from is not None:
            df["$gte"] = date_from
        if date_to is not None:
            df["$lte"] = date_to
        # Day picker filters by expense/register date on every queue.
        query["date"] = df
    return query


REJECTED_SORT = [("date", -1), ("created_at", -1)]


def build_rejected_query(
    *,
    search: str = "",
    truck: str = "",
    cashier_id: ObjectId | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    item: str | Sequence[str] | None = "",
    description: str | Sequence[str] | None = "",
) -> dict[str, Any]:
    query: dict[str, Any] = dict(inbox_base_query(rejected=True))
    and_clauses: list[dict[str, Any]] = []
    append_text_filters(
        and_clauses, search=search, item=item, description=description
    )
    if and_clauses:
        query["$and"] = and_clauses
    if truck.strip():
        query["truck_number"] = {
            "$regex": re.escape(truck.strip()),
            "$options": "i",
        }
    if cashier_id is not None:
        query["cashier_id"] = cashier_id
    if date_from or date_to:
        df: dict[str, Any] = {}
        if date_from is not None:
            df["$gte"] = date_from
        if date_to is not None:
            df["$lte"] = date_to
        query["date"] = df
    return query


def apply_cascade_filters(
    base: dict[str, Any],
    *,
    items: Sequence[str] | None = None,
    descriptions: Sequence[str] | None = None,
) -> dict[str, Any]:
    query = dict(base)
    and_clauses: list[dict[str, Any]] = []
    append_text_filters(
        and_clauses,
        item=list(items or []),
        description=list(descriptions or []),
    )
    if and_clauses:
        existing = query.pop("$and", None)
        if existing:
            and_clauses = list(existing) + and_clauses
        query["$and"] = and_clauses
    return query


def pending_inbox_count_query() -> dict[str, Any]:
    return dict(inbox_base_query(rejected=False))


def edited_count_query() -> dict[str, Any]:
    query = dict(inbox_base_query(rejected=False))
    query["edited_after_verification"] = True
    return query


DELETION_BASE_MATCH: dict[str, Any] = {
    "deletion_requested": True,
    "trashed": {"$ne": True},
}

DELETION_SORT = [("deletion_requested_at", -1), ("date", -1)]


def build_deletion_query(
    *,
    search: str = "",
    truck: str = "",
    cashier_id: ObjectId | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    item: str | Sequence[str] | None = "",
    description: str | Sequence[str] | None = "",
) -> dict[str, Any]:
    query: dict[str, Any] = dict(DELETION_BASE_MATCH)
    and_clauses: list[dict[str, Any]] = []
    append_text_filters(
        and_clauses, search=search, item=item, description=description
    )
    if and_clauses:
        query["$and"] = and_clauses
    if truck.strip():
        query["truck_number"] = {
            "$regex": re.escape(truck.strip()),
            "$options": "i",
        }
    if cashier_id is not None:
        query["cashier_id"] = cashier_id
    if date_from or date_to:
        df: dict[str, Any] = {}
        if date_from is not None:
            df["$gte"] = date_from
        if date_to is not None:
            df["$lte"] = date_to
        query["date"] = df
    return query


def deletion_items_query(descriptions: Sequence[str] | None = None) -> dict[str, Any]:
    """Distinct-item filter for deletion meta (substring description match)."""
    base: dict[str, Any] = dict(DELETION_BASE_MATCH)
    descs = normalize_multi_filter(list(descriptions or []))
    if descs:
        base["$or"] = [
            {"description": {"$regex": re.escape(d), "$options": "i"}} for d in descs
        ]
    return base


def deletion_descriptions_query(items: Sequence[str] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = dict(DELETION_BASE_MATCH)
    item_list = normalize_multi_filter(list(items or []))
    if item_list:
        ors: list[dict[str, Any]] = []
        for it in item_list:
            esc = re.escape(it)
            ors.append({"item": {"$regex": f"^{esc}$", "$options": "i"}})
            ors.append({"category_name": {"$regex": f"^{esc}$", "$options": "i"}})
        base["$or"] = ors
    return base


TRASH_SORT = [("trashed_at", -1), ("date", -1)]
VERIFIED_SORT = [("date", -1), ("created_at", -1)]

RESTORE_BASE_MATCH: dict[str, Any] = {
    "trashed": True,
    "restore_requested": True,
}

RESTORE_SORT = [("restore_requested_at", -1), ("date", -1)]


def build_restore_query(
    *,
    search: str = "",
    truck: str = "",
    cashier_id: ObjectId | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    item: str | Sequence[str] | None = "",
    description: str | Sequence[str] | None = "",
) -> dict[str, Any]:
    """Pending cashier restore requests awaiting accountant approval."""
    query: dict[str, Any] = dict(RESTORE_BASE_MATCH)
    and_clauses: list[dict[str, Any]] = []
    append_text_filters(
        and_clauses, search=search, item=item, description=description
    )
    if and_clauses:
        query["$and"] = and_clauses
    if truck.strip():
        query["truck_number"] = {
            "$regex": re.escape(truck.strip()),
            "$options": "i",
        }
    if cashier_id is not None:
        query["cashier_id"] = cashier_id
    if date_from or date_to:
        df: dict[str, Any] = {}
        if date_from is not None:
            df["$gte"] = date_from
        if date_to is not None:
            df["$lte"] = date_to
        query["date"] = df
    return query


def restore_items_query(descriptions: Sequence[str] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = dict(RESTORE_BASE_MATCH)
    descs = normalize_multi_filter(list(descriptions or []))
    if descs:
        base["$or"] = [
            {"description": {"$regex": re.escape(d), "$options": "i"}} for d in descs
        ]
    return base


def restore_descriptions_query(items: Sequence[str] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = dict(RESTORE_BASE_MATCH)
    item_list = normalize_multi_filter(list(items or []))
    if item_list:
        ors: list[dict[str, Any]] = []
        for it in item_list:
            esc = re.escape(it)
            ors.append({"item": {"$regex": f"^{esc}$", "$options": "i"}})
            ors.append({"category_name": {"$regex": f"^{esc}$", "$options": "i"}})
        base["$or"] = ors
    return base


def build_trash_query(
    *,
    search: str = "",
    truck: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {"trashed": True}
    and_clauses: list[dict[str, Any]] = []
    clause = inbox_search_clause(search)
    if clause:
        and_clauses.append(clause)
    if and_clauses:
        query["$and"] = and_clauses
    if truck.strip():
        query["truck_number"] = {
            "$regex": re.escape(truck.strip()),
            "$options": "i",
        }
    if date_from or date_to:
        df: dict[str, Any] = {}
        if date_from is not None:
            df["$gte"] = date_from
        if date_to is not None:
            df["$lte"] = date_to
        query["trashed_at"] = df
    return query


def build_verified_query(
    *,
    year: int | None = None,
    month: int | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "verified": True,
        "deletion_requested": {"$ne": True},
        "trashed": {"$ne": True},
    }
    if year or month:
        date_filter: dict[str, Any] = {}
        _year = year or date.today().year
        if month:
            last_day = calendar.monthrange(_year, month)[1]
            date_filter["$gte"] = datetime(_year, month, 1)
            date_filter["$lte"] = datetime(_year, month, last_day, 23, 59, 59)
        else:
            date_filter["$gte"] = datetime(_year, 1, 1)
            date_filter["$lte"] = datetime(_year, 12, 31, 23, 59, 59)
        query["date"] = date_filter
    if category and category.strip():
        query["category_name"] = category.strip()
    return query


# Fields cascaded from a pending-edit clone onto the original master row.
RE_APPROVE_CASCADE_FIELDS = frozenset(
    {
        "date",
        "description",
        "truck_number",
        "amount",
        "currency",
        "lpo_do",
        "do_number",
        "memo",
        "receipt_status",
        "notes_flag",
        "ref_float",
        "ownership",
        "approver",
        "payee",
        "cheque",
        "reported_date",
        "category_name",
        "category_id",
        "item",
        "category_confidence",
        "month",
        "year",
    }
)
