"""Shared transaction query helpers for the cashier/accountant APIs."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any, Sequence


REGISTER_DAY_EXPR: dict[str, Any] = {"$ifNull": ["$import_primary_date", "$date"]}

DRAFT_BASE_MATCH: dict[str, Any] = {
    "register_status": "draft",
    "verified": {"$ne": True},
    "rejected": {"$ne": True},
    "discarded": {"$ne": True},
    "deletion_requested": {"$ne": True},
    "trashed": {"$ne": True},
}


def day_bounds(target: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target, time.min)
    end = datetime.combine(target, time.max).replace(microsecond=0)
    return start, end


def register_day_clause(target: date) -> dict[str, Any]:
    """Match rows that belong on this register calendar day.

    Daily-import rows are owned by ``import_primary_date`` even when their
    Excel ``date`` is earlier. Manual/legacy rows without a primary date use
    Excel ``date``.
    """
    start, end = day_bounds(target)
    return {
        "$or": [
            {"import_primary_date": {"$gte": start, "$lte": end}},
            {
                "$and": [
                    {"date": {"$gte": start, "$lte": end}},
                    {"import_primary_date": None},
                ]
            },
        ]
    }


def register_day_range_clause(
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    primary: dict[str, Any] = {}
    excel: dict[str, Any] = {}
    if date_from:
        start = datetime(date_from.year, date_from.month, date_from.day, 0, 0, 0)
        primary["$gte"] = start
        excel["$gte"] = start
    if date_to:
        end = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        primary["$lte"] = end
        excel["$lte"] = end
    if not primary:
        return {}
    return {
        "$or": [
            {"import_primary_date": primary},
            {
                "$and": [
                    {"date": excel},
                    {"import_primary_date": None},
                ]
            },
        ]
    }


def filter_pending_edit_clones(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer open pending-edit clones over their master originals."""
    pending_original_ids = {
        doc.get("original_transaction_id")
        for doc in docs
        if doc.get("original_transaction_id") and not doc.get("verified")
    }
    if not pending_original_ids:
        return docs
    return [doc for doc in docs if doc.get("_id") not in pending_original_ids]


def normalize_multi_filter(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()]
        return [text]
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def browse_match(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str = "",
    truck: str = "",
    category_name: str | Sequence[str] | None = "",
    descriptions: str | Sequence[str] | None = "",
    daily_import_id: str = "",
) -> dict[str, Any]:
    """Shared match clause for browse list / distinct-option queries."""
    match: dict[str, Any] = {}
    and_clauses: list[dict[str, Any]] = []

    uid = (daily_import_id or "").strip()
    if uid:
        match["daily_import_id"] = uid
    elif date_from or date_to:
        date_filter: dict[str, Any] = {}
        if date_from:
            date_filter["$gte"] = datetime(date_from.year, date_from.month, date_from.day)
        if date_to:
            date_filter["$lte"] = datetime(
                date_to.year, date_to.month, date_to.day, 23, 59, 59
            )
        match["date"] = date_filter

    cats = normalize_multi_filter(category_name)
    if cats:
        cat_ors: list[dict[str, Any]] = []
        for name in cats:
            escaped = re.escape(name)
            cat_ors.append({"category_name": {"$regex": f"^{escaped}$", "$options": "i"}})
            cat_ors.append({"item": {"$regex": f"^{escaped}$", "$options": "i"}})
        and_clauses.append({"$or": cat_ors})

    descs = normalize_multi_filter(descriptions)
    if descs:
        if isinstance(descriptions, str) and "," not in descriptions:
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
                                "$regex": f"^{re.escape(desc)}$",
                                "$options": "i",
                            }
                        }
                        for desc in descs
                    ]
                }
            )
    elif keyword.strip():
        kw_re = {"$regex": re.escape(keyword.strip()), "$options": "i"}
        and_clauses.append(
            {
                "$or": [
                    {"description": kw_re},
                    {"truck_number": kw_re},
                    {"memo": kw_re},
                ]
            }
        )

    if truck.strip():
        match["truck_number"] = {"$regex": re.escape(truck.strip()), "$options": "i"}

    if and_clauses:
        match["$and"] = and_clauses
    return match
