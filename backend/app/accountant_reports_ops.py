"""Category / supplier report query helpers (Phase 4.3)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Sequence

from bson import ObjectId

from .accountant_master_ops import (
    MASTER_SORT_ALLOWED,
    append_master_column_filters,
    merge_and_clauses,
)
from .accountant_ops import append_text_filters


def category_report_reference_year(
    date_from: datetime | None,
    date_to: datetime | None,
) -> int:
    if date_from is not None:
        return date_from.year
    if date_to is not None:
        return date_to.year
    return date.today().year


def build_named_report_query(
    name: str,
    *,
    match_field: str,
    description: str = "",
    descriptions: str | Sequence[str] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: dict[str, Sequence[str]] | None = None,
    cashier_ids: Sequence[ObjectId] | None = None,
) -> dict[str, Any]:
    """Verified / not-trashed master rows matching one catalog name field."""
    import re

    value = (name or "").strip()
    query: dict[str, Any] = {
        "verified": True,
        "deletion_requested": {"$ne": True},
        "trashed": {"$ne": True},
        match_field: {
            "$regex": f"^{re.escape(value)}$",
            "$options": "i",
        },
    }
    and_clauses: list[dict[str, Any]] = []
    desc_arg: object = descriptions if descriptions else description
    if desc_arg:
        append_text_filters(and_clauses, description=desc_arg)  # type: ignore[arg-type]
    if date_from is not None or date_to is not None:
        date_clause: dict[str, Any] = {}
        if date_from is not None:
            date_clause["$gte"] = date_from.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        if date_to is not None:
            date_clause["$lte"] = date_to.replace(
                hour=23, minute=59, second=59, microsecond=0
            )
        and_clauses.append({"date": date_clause})
    ref_year = category_report_reference_year(date_from, date_to)
    append_master_column_filters(
        and_clauses,
        column_filters,
        year=ref_year,
        cashier_ids=cashier_ids,
    )
    return merge_and_clauses(query, and_clauses)


def build_category_report_query(
    category: str,
    *,
    description: str = "",
    descriptions: str | Sequence[str] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: dict[str, Sequence[str]] | None = None,
    cashier_ids: Sequence[ObjectId] | None = None,
) -> dict[str, Any]:
    return build_named_report_query(
        category,
        match_field="category_name",
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
        cashier_ids=cashier_ids,
    )


def build_payee_report_query(
    payee: str,
    *,
    description: str = "",
    descriptions: str | Sequence[str] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: dict[str, Sequence[str]] | None = None,
    cashier_ids: Sequence[ObjectId] | None = None,
) -> dict[str, Any]:
    return build_named_report_query(
        payee,
        match_field="payee",
        description=description,
        descriptions=descriptions,
        date_from=date_from,
        date_to=date_to,
        column_filters=column_filters,
        cashier_ids=cashier_ids,
    )


def category_report_sort_clauses(
    sort_field: str = "date",
    sort_asc: bool = True,
) -> list[tuple[str, int]]:
    field = sort_field if sort_field in MASTER_SORT_ALLOWED else "date"
    direction = 1 if sort_asc else -1
    clauses: list[tuple[str, int]] = []
    if field == "truck_number":
        clauses.append(("truck_sort_key", direction))
    clauses.append((field, direction))
    clauses.append(("created_at", direction))
    return clauses


def category_report_primary_sort(
    sort_field: str = "date",
    sort_asc: bool = True,
) -> tuple[str, int]:
    """Primary sort key for opening-balance aggregation (matches desktop)."""
    clauses = category_report_sort_clauses(sort_field, sort_asc)
    return clauses[0] if clauses else ("date", 1 if sort_asc else -1)


def currency_totals_group() -> dict[str, Any]:
    return {
        "_id": None,
        "count": {"$sum": 1},
        "tzs_total": {
            "$sum": {
                "$cond": [
                    {"$in": [{"$toUpper": "$currency"}, ["TZS", "TSH", "TZ"]]},
                    "$amount",
                    0,
                ]
            }
        },
        "usd_total": {
            "$sum": {
                "$cond": [
                    {"$eq": [{"$toUpper": "$currency"}, "USD"]},
                    "$amount",
                    0,
                ]
            }
        },
    }
