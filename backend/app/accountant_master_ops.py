"""Master Expenses query helpers (Phase 4.1)."""

from __future__ import annotations

import calendar
import json
import re
from datetime import date, datetime
from typing import Any, Sequence

from bson import ObjectId

from .transaction_ops import normalize_multi_filter

MASTER_SORT_ALLOWED = frozenset(
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
        "ref_float",
        "receipt_status",
    }
)

MASTER_COL_FIELDS = frozenset(
    {
        "date",
        "item",
        "description",
        "truck_number",
        "memo",
        "ref_float",
        "tzs",
        "usd",
        "receipt_status",
        "ownership",
        "approver",
        "cashier",
    }
)

_RECEIPT_FILTER_MAP = {
    "received": ["received"],
    "pending": ["pending"],
    "missing": ["missing", "no_receipt"],
    "no_receipt": ["missing", "no_receipt"],
    "no receipt": ["missing", "no_receipt"],
}

TRUCK_SORT_FIELDS = frozenset({"truck_number"})


def master_period_clause(start: datetime, end: datetime) -> dict[str, Any]:
    return {"date": {"$gte": start, "$lte": end}}


def master_base_match(start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "verified": True,
        "deletion_requested": {"$ne": True},
        "trashed": {"$ne": True},
        **master_period_clause(start, end),
    }


def master_year_bounds(year: int | None, month: int = 0) -> tuple[datetime, datetime, int]:
    _year = year or date.today().year
    if month and 1 <= month <= 12:
        last_day = calendar.monthrange(_year, month)[1]
        start = datetime(_year, month, 1)
        end = datetime(_year, month, last_day, 23, 59, 59)
    else:
        start = datetime(_year, 1, 1)
        end = datetime(_year, 12, 31, 23, 59, 59)
    return start, end, _year


def sum_amount_if_currency(*codes: str) -> dict[str, Any]:
    return {
        "$sum": {
            "$cond": [
                {
                    "$in": [
                        {"$toUpper": {"$ifNull": ["$currency", ""]}},
                        list(codes),
                    ]
                },
                "$amount",
                0,
            ]
        }
    }


def ledger_sort_clauses(
    sort_field: str,
    sort_asc: bool,
    *,
    default: str = "date",
) -> list[tuple[str, int]]:
    field = sort_field if sort_field in MASTER_SORT_ALLOWED else default
    direction = 1 if sort_asc else -1
    clauses: list[tuple[str, int]] = []
    if field in TRUCK_SORT_FIELDS:
        clauses.append(("truck_sort_key", direction))
    clauses.append((field, direction))
    if "created_at" not in {c[0] for c in clauses}:
        clauses.append(("created_at", -1))
    return clauses


def master_short_name(name: str) -> str:
    parts = (name or "").strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1][0]}."
    return (name or "").strip() or "—"


def parse_master_filter_date(value: str, year: int) -> datetime | None:
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


def parse_master_filter_amount(value: str) -> float | None:
    text = (value or "").strip().replace(",", "").replace(" ", "")
    if not text or text == "—":
        return None
    for prefix in ("TZS", "TSH", "TZ", "USD", "ZMW", "ZK"):
        if text.upper().startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    try:
        return float(text)
    except ValueError:
        return None


def receipt_statuses_for_filter(values: Sequence[str]) -> list[str]:
    statuses: list[str] = []
    seen: set[str] = set()
    for raw in values:
        key = (raw or "").strip().lower().replace("_", " ")
        mapped = _RECEIPT_FILTER_MAP.get(key)
        if mapped is None:
            compact = key.replace(" ", "_")
            mapped = _RECEIPT_FILTER_MAP.get(compact)
        if mapped is None and key in {"received", "pending", "missing", "no_receipt"}:
            mapped = [key]
        for st in mapped or []:
            if st not in seen:
                seen.add(st)
                statuses.append(st)
    return statuses


def master_exact_ors(field: str, values: Sequence[str]) -> dict[str, Any] | None:
    vals = [v for v in normalize_multi_filter(values) if v != "—"]
    if not vals:
        return None
    return {
        "$or": [
            {field: {"$regex": f"^{re.escape(v)}$", "$options": "i"}} for v in vals
        ]
    }


def merge_and_clauses(query: dict[str, Any], and_clauses: list) -> dict[str, Any]:
    if not and_clauses:
        return query
    out = dict(query)
    existing = out.pop("$and", None)
    merged = list(existing or []) + list(and_clauses)
    if len(merged) == 1 and "$or" not in out and not existing:
        only = merged[0]
        if len(only) == 1 and next(iter(only)) not in out:
            out.update(only)
            return out
    out["$and"] = merged
    return out


def append_master_column_filters(
    and_clauses: list,
    column_filters: dict[str, Sequence[str]] | None,
    *,
    year: int,
    cashier_ids: Sequence[ObjectId] | None = None,
) -> None:
    if not column_filters:
        return
    for field, raw_vals in column_filters.items():
        if field not in MASTER_COL_FIELDS:
            continue
        vals = [v for v in normalize_multi_filter(raw_vals) if v != "—"]
        if not vals:
            continue
        if field == "date":
            day_ors: list = []
            for v in vals:
                day = parse_master_filter_date(v, year)
                if day is None:
                    continue
                day_ors.append(
                    {
                        "date": {
                            "$gte": day,
                            "$lte": day.replace(hour=23, minute=59, second=59),
                        },
                    }
                )
            if day_ors:
                and_clauses.append({"$or": day_ors})
            continue
        if field == "item":
            item_ors: list = []
            for it in vals:
                esc = re.escape(it)
                item_ors.append({"item": {"$regex": f"^{esc}$", "$options": "i"}})
                item_ors.append(
                    {"category_name": {"$regex": f"^{esc}$", "$options": "i"}}
                )
            and_clauses.append({"$or": item_ors})
            continue
        if field == "receipt_status":
            statuses = receipt_statuses_for_filter(vals)
            if statuses:
                and_clauses.append({"receipt_status": {"$in": statuses}})
            continue
        if field == "tzs":
            amounts = [
                a
                for a in (parse_master_filter_amount(v) for v in vals)
                if a is not None
            ]
            if amounts:
                and_clauses.append(
                    {
                        "currency": {
                            "$in": ["TZS", "TSH", "TZ", "tzs", "tsh", "tz"]
                        },
                        "amount": {"$in": amounts},
                    }
                )
            continue
        if field == "usd":
            amounts = [
                a
                for a in (parse_master_filter_amount(v) for v in vals)
                if a is not None
            ]
            if amounts:
                and_clauses.append(
                    {
                        "currency": {"$in": ["USD", "usd"]},
                        "amount": {"$in": amounts},
                    }
                )
            continue
        if field == "ref_float":
            ref_ors: list = []
            for v in vals:
                if v.strip().upper() == "REFUND TO FLOAT":
                    ref_ors.append({"notes_flag": True})
                    ref_ors.append(
                        {
                            "ref_float": {
                                "$regex": f"^{re.escape(v)}$",
                                "$options": "i",
                            },
                        }
                    )
                else:
                    ref_ors.append(
                        {
                            "ref_float": {
                                "$regex": f"^{re.escape(v)}$",
                                "$options": "i",
                            },
                        }
                    )
            if ref_ors:
                and_clauses.append({"$or": ref_ors})
            continue
        if field == "cashier":
            if cashier_ids:
                and_clauses.append({"cashier_id": {"$in": list(cashier_ids)}})
            continue
        clause = master_exact_ors(field, vals)
        if clause:
            and_clauses.append(clause)


def _plate_flex_body(compact: str) -> str:
    return r"\s*".join(re.escape(ch) for ch in compact)


def master_search_clause(search: str) -> dict[str, Any] | None:
    """Plate-aware truck search or substring on truck/description only."""
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
    return {
        "$or": [
            {"truck_number": {"$regex": escaped, "$options": "i"}},
            {"description": {"$regex": escaped, "$options": "i"}},
        ]
    }


def build_master_query(
    *,
    year: int | None,
    month: int,
    search: str,
    truck: str,
    category: str,
    receipt: str,
    description: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    column_filters: dict[str, Sequence[str]] | None = None,
    cashier_ids: Sequence[ObjectId] | None = None,
) -> dict[str, Any]:
    start, end, _year = master_year_bounds(year, month)
    if date_from is not None:
        df = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
        start = max(start, df)
    if date_to is not None:
        dt = date_to.replace(hour=23, minute=59, second=59, microsecond=0)
        end = min(end, dt)

    query: dict[str, Any] = master_base_match(start, end)
    and_clauses: list = []

    clause = master_search_clause(search)
    if clause:
        and_clauses.append(clause)
    if description.strip():
        and_clauses.append(
            {
                "description": {
                    "$regex": re.escape(description.strip()),
                    "$options": "i",
                },
            }
        )
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

    append_master_column_filters(
        and_clauses,
        column_filters,
        year=_year,
        cashier_ids=cashier_ids,
    )
    return merge_and_clauses(query, and_clauses)


def exclude_supplier_payments(
    query: dict[str, Any], supplier_names_lower: Sequence[str]
) -> dict[str, Any]:
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
    return merge_and_clauses(query, [clause])


def parse_column_filters_json(raw: str | None) -> dict[str, list[str]]:
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        field = str(key)
        if field not in MASTER_COL_FIELDS:
            continue
        if isinstance(value, str):
            vals = normalize_multi_filter(value)
        elif isinstance(value, (list, tuple)):
            vals = normalize_multi_filter(list(value))
        else:
            continue
        if vals:
            out[field] = vals
    return out


MASTER_EDIT_FIELDS = frozenset(
    {
        "date",
        "description",
        "truck_number",
        "amount",
        "currency",
        "memo",
        "receipt_status",
        "notes_flag",
        "ref_float",
        "ownership",
        "approver",
        "item",
        "category_name",
        "category_id",
        "month",
        "year",
    }
)

MASTER_EDIT_FILTER = {
    "verified": True,
    "deletion_requested": {"$ne": True},
    "trashed": {"$ne": True},
}

_MONTH_ABBR = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

_CANONICAL_PLATE = re.compile(r"^T(\d+)\s+([A-Z]{2,3})$", re.I)


def master_month_label(dt: datetime) -> str:
    return f"{_MONTH_ABBR[dt.month]} {str(dt.year)[-2:]}"


def normalize_master_receipt(status: str) -> str:
    s = (status or "pending").strip().lower().replace(" ", "_")
    if s in ("no_receipt", "missing", "no", "n/a", "none"):
        return "no_receipt"
    if "received" in s or s in ("yes", "receipt", "rcvd"):
        return "received"
    return "pending"


def truck_sort_key(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "3|"
    m = _CANONICAL_PLATE.match(text.upper().replace("  ", " "))
    if m:
        return f"1|{int(m.group(1)):010d}|{m.group(2)}"
    compact = re.sub(r"\s+", " ", text.upper()).strip()
    m2 = re.match(r"^T\s*(\d+)\s*([A-Z]{2,3})$", compact)
    if m2:
        return f"1|{int(m2.group(1)):010d}|{m2.group(2)}"
    return f"2|{compact}"


def stamp_truck_sort_key(doc: dict[str, Any]) -> None:
    doc["truck_sort_key"] = truck_sort_key(str(doc.get("truck_number") or ""))


def fmt_master_amount(amount: float, *, decimals: int) -> str:
    if decimals <= 0:
        return f"{amount:,.0f}"
    return f"{amount:,.{decimals}f}"


def prepare_master_updates(updates: dict[str, Any]) -> dict[str, Any]:
    """Sanitize accountant Master edits and keep dependent fields in sync."""
    clean: dict[str, Any] = {}
    for key, value in (updates or {}).items():
        if key in MASTER_EDIT_FIELDS:
            clean[key] = value
    if not clean:
        return {}

    if "date" in clean:
        raw_date = clean["date"]
        if isinstance(raw_date, str):
            try:
                raw_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                if raw_date.tzinfo is not None:
                    raw_date = raw_date.replace(tzinfo=None)
                clean["date"] = raw_date
            except ValueError:
                clean.pop("date", None)
                raw_date = None
        if isinstance(raw_date, datetime):
            clean["month"] = master_month_label(raw_date)
            clean["year"] = raw_date.year

    if "item" in clean and "category_name" not in clean:
        clean["category_name"] = clean["item"]
    if "category_name" in clean and "item" not in clean:
        clean["item"] = clean["category_name"]

    if "ref_float" in clean:
        ref = (clean.get("ref_float") or "").strip()
        clean["ref_float"] = ref
        clean["notes_flag"] = ref.upper() == "REFUND TO FLOAT"

    if "receipt_status" in clean:
        clean["receipt_status"] = normalize_master_receipt(
            str(clean.get("receipt_status") or "")
        )

    if "currency" in clean and clean["currency"] is not None:
        cur = str(clean["currency"]).strip().upper() or "TZS"
        clean["currency"] = "TZS" if cur in {"TZS", "TSH", "TZ"} else cur

    if "amount" in clean and clean["amount"] is not None:
        clean["amount"] = float(clean["amount"])

    if "category_id" in clean and clean["category_id"] is not None:
        raw_cid = clean["category_id"]
        if isinstance(raw_cid, str) and ObjectId.is_valid(raw_cid):
            clean["category_id"] = ObjectId(raw_cid)
        elif not isinstance(raw_cid, ObjectId):
            clean.pop("category_id", None)

    for text_key in (
        "description",
        "truck_number",
        "memo",
        "ownership",
        "approver",
        "item",
        "category_name",
    ):
        if text_key in clean and clean[text_key] is not None:
            clean[text_key] = str(clean[text_key]).strip()

    if "truck_number" in clean:
        stamp_truck_sort_key(clean)

    return clean
