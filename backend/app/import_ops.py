"""Daily + master Excel import helpers (Phase 7.1).

Excel parsing stays on the desktop. These helpers build verified transaction
docs, enforce idempotent inserts, and share query/pipeline builders used by
the import API routes.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime
from typing import Any, Optional, Sequence, Tuple

from bson import ObjectId
from pymongo.errors import BulkWriteError

from .accountant_master_ops import stamp_truck_sort_key
from .accountant_ops import append_text_filters

_MONTH_LABELS = [
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
]

_indexes_ensured = False
IMPORT_BATCH_SIZE = 500


def reset_import_index_cache() -> None:
    global _indexes_ensured
    _indexes_ensured = False


def month_label(dt: datetime) -> str:
    return f"{_MONTH_LABELS[dt.month]} {str(dt.year)[-2:]}"


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip().upper()


def daily_import_row_key(payload: dict[str, Any]) -> str:
    """Stable hash for one daily→master row within an upload batch."""
    parts = [
        _norm(payload.get("serial")),
        _norm(payload.get("date")),
        _norm(payload.get("description")),
        _norm(payload.get("truck_number")),
        _norm(payload.get("amount")),
        _norm(payload.get("item") or payload.get("category_name")),
        _norm(payload.get("lpo_do")),
        _norm(payload.get("do_number")),
        _norm(payload.get("currency") or "TZS"),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:40]


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is not None:
                return parsed.replace(tzinfo=None)
            return parsed
        except ValueError:
            pass
    raise ValueError(f"Invalid date value: {value!r}")


def _optional_object_id(value: Any) -> Optional[ObjectId]:
    if value is None or value == "":
        return None
    if isinstance(value, ObjectId):
        return value
    return ObjectId(str(value))


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def verified_doc_from_daily_payload(
    payload: dict[str, Any],
    *,
    verified_by: Optional[ObjectId] = None,
    day_order: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build a verified master transaction doc from a staged daily-import payload."""
    stamp = now or datetime.utcnow()
    dt = _as_datetime(payload["date"])
    item_name = (payload.get("category_name") or payload.get("item") or "").strip()
    ref_float = (payload.get("ref_float") or "").strip()
    primary = payload.get("import_primary_date")
    if primary is not None and primary != "":
        primary = _as_datetime(primary)
    else:
        primary = None

    amount_usd = _optional_float(payload.get("amount_usd"))
    category_id = _optional_object_id(payload.get("category_id"))

    doc: dict[str, Any] = {
        "date": dt,
        "description": (payload.get("description") or "").strip(),
        "truck_number": (payload.get("truck_number") or "").strip().upper(),
        "amount": float(payload.get("amount") or 0),
        "currency": (payload.get("currency") or "TZS").strip().upper() or "TZS",
        "category_id": category_id,
        "category_name": item_name or None,
        "category_confidence": 1.0 if item_name else 0.0,
        "item": item_name,
        "lpo_do": (payload.get("lpo_do") or "").upper(),
        "do_number": (payload.get("do_number") or "").upper(),
        "memo": (payload.get("memo") or "").strip(),
        "receipt_status": payload.get("receipt_status") or "",
        "notes_flag": ref_float == "REFUND TO FLOAT",
        "ref_float": ref_float,
        "ownership": (payload.get("ownership") or "").upper(),
        "approver": (payload.get("approver") or "").upper(),
        "payee": " ".join((payload.get("payee") or "").strip().upper().split()),
        "cheque": "",
        "reported_date": None,
        # Attribute the batch to the importing accountant so rows show in
        # Table / Merged register like normal system-created entries.
        "cashier_id": verified_by,
        "verified": True,
        "verified_by": verified_by,
        "verified_at": stamp,
        "rejection_reason": None,
        "edited_after_verification": False,
        "last_edited_at": None,
        "last_edited_by": None,
        "rejected": False,
        "discarded": False,
        "deletion_requested": False,
        "deletion_requested_at": None,
        "deletion_requested_by": None,
        "trashed": False,
        "trashed_at": None,
        "trashed_by": None,
        "original_transaction_id": None,
        "day_order": day_order,
        "register_status": "submitted",
        "month": month_label(dt),
        "year": dt.year,
        "created_at": stamp,
        "possible_duplicate": bool(payload.get("possible_duplicate")),
        "date_discrepancy": bool(payload.get("date_discrepancy")),
        "attachments": [],
    }
    if amount_usd is not None:
        doc["amount_usd"] = amount_usd

    daily_import_id = str(payload.get("daily_import_id") or "").strip()
    if daily_import_id:
        doc["daily_import_id"] = daily_import_id
    daily_import_source = str(payload.get("daily_import_source") or "").strip()
    if daily_import_source:
        doc["daily_import_source"] = daily_import_source
    if primary is not None:
        doc["import_primary_date"] = primary

    stamp_truck_sort_key(doc)
    return doc


def verified_doc_from_master_payload(
    payload: dict[str, Any],
    *,
    upload_id: str,
    source_filename: str,
    verified_by: Optional[ObjectId] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build a verified master transaction doc from a staged master-import row."""
    stamp = now or datetime.utcnow()
    dt = _as_datetime(payload["date"])
    item_name = (payload.get("category_name") or payload.get("item") or "").strip()
    amount_usd = _optional_float(payload.get("amount_usd"))
    category_id = _optional_object_id(payload.get("category_id"))
    memo = (payload.get("memo") or "").strip()
    notes = (payload.get("notes") or "").strip()
    if notes and notes not in memo:
        memo = " | ".join(p for p in (memo, notes) if p)

    receipt_raw = payload.get("receipt_status") or payload.get("receipt_raw") or ""
    receipt_status = normalize_receipt(str(receipt_raw))

    doc: dict[str, Any] = {
        "date": dt,
        "description": (payload.get("description") or "").strip(),
        "truck_number": (payload.get("truck_number") or "").strip().upper(),
        "amount": float(payload.get("amount") or 0),
        "currency": (payload.get("currency") or "TZS").strip().upper() or "TZS",
        "category_id": category_id,
        "category_name": item_name or None,
        "category_confidence": 1.0 if item_name else 0.0,
        "item": item_name,
        "lpo_do": (payload.get("lpo_do") or "").upper(),
        "do_number": (payload.get("do_number") or "").upper(),
        "memo": memo,
        "receipt_status": receipt_status,
        "notes_flag": bool(payload.get("notes") or payload.get("notes_flag")),
        "ref_float": (payload.get("ref_float") or "").strip(),
        "ownership": (payload.get("ownership") or "").upper(),
        "approver": (payload.get("approver") or "").upper(),
        "payee": (payload.get("payee") or "").strip(),
        "cheque": (payload.get("cheque") or "").strip(),
        "reported_date": None,
        "cashier_id": None,
        "verified": True,
        "verified_by": verified_by,
        "verified_at": stamp,
        "rejection_reason": None,
        "edited_after_verification": False,
        "last_edited_at": None,
        "last_edited_by": None,
        "rejected": False,
        "discarded": False,
        "deletion_requested": False,
        "deletion_requested_at": None,
        "deletion_requested_by": None,
        "trashed": False,
        "trashed_at": None,
        "trashed_by": None,
        "original_transaction_id": None,
        "day_order": None,
        "register_status": "submitted",
        "month": month_label(dt),
        "year": dt.year,
        "created_at": stamp,
        "possible_duplicate": False,
        "date_discrepancy": False,
        "attachments": [],
        "master_import_id": upload_id,
        "master_import_source": source_filename,
    }
    if amount_usd is not None:
        doc["amount_usd"] = amount_usd
    serial = payload.get("serial")
    if serial is not None and serial != "":
        doc["master_serial"] = int(serial)

    stamp_truck_sort_key(doc)
    return doc


def normalize_receipt(val: str) -> str:
    s = (val or "").strip().lower()
    if not s:
        return "pending"
    if "no receipt" in s or s in {"missing", "no", "n/a"}:
        return "missing"
    if "received" in s or s in {"yes", "receipt", "rcvd"}:
        return "received"
    return "pending"


async def ensure_import_indexes(db: Any) -> None:
    """Create unique indexes used for idempotent imports (safe to call often)."""
    global _indexes_ensured
    if _indexes_ensured:
        return
    try:
        await db.transactions.create_index(
            [("daily_import_id", 1), ("import_row_key", 1)],
            name="uniq_daily_import_row",
            unique=True,
            partialFilterExpression={
                "daily_import_id": {"$type": "string"},
                "import_row_key": {"$type": "string"},
            },
        )
        await db.transactions.create_index(
            [("master_import_source", 1), ("master_serial", 1)],
            name="uniq_master_import_serial",
            unique=True,
            partialFilterExpression={
                "master_import_source": {"$type": "string"},
                "master_serial": {"$exists": True},
            },
        )
        await db.imported_feeds.create_index(
            [("skipped_row_id", 1)],
            name="uniq_feed_skipped_row_id",
            unique=True,
            sparse=True,
        )
        await db.separate_expenses.create_index(
            [("skipped_row_id", 1)],
            name="uniq_sep_skipped_row_id",
            unique=True,
            sparse=True,
        )
    except Exception:
        # Index build may fail on pre-existing duplicates — inserts still use
        # soft dedupe; migrate.py is the authoritative conflict reporter.
        pass
    _indexes_ensured = True


async def insert_many_idempotent(
    collection: Any,
    docs: Sequence[dict[str, Any]],
    *,
    session: Any = None,
) -> Tuple[int, int]:
    """Insert docs ignoring duplicate-key conflicts.

    Returns ``(inserted_count, duplicate_skipped)``.
    """
    if not docs:
        return 0, 0
    kwargs: dict[str, Any] = {"ordered": False}
    if session is not None:
        kwargs["session"] = session
    try:
        result = await collection.insert_many(list(docs), **kwargs)
        return len(result.inserted_ids), 0
    except BulkWriteError as exc:
        details = exc.details or {}
        inserted = int(details.get("nInserted") or 0)
        errors = details.get("writeErrors") or []
        dupes = sum(1 for err in errors if err.get("code") == 11000)
        other = [err for err in errors if err.get("code") != 11000]
        if other:
            raise
        return inserted, dupes


def build_issue_query(
    *,
    search: str = "",
    truck: str = "",
    cashier_id: Any = None,
    date_from: Any = None,
    date_to: Any = None,
    item: str = "",
    description: str = "",
) -> dict[str, Any]:
    issue_or = {
        "$or": [
            {"possible_duplicate": True},
            {"date_discrepancy": True},
        ]
    }
    and_clauses: list[dict[str, Any]] = [issue_or]
    append_text_filters(
        and_clauses,
        search=search,
        item=item,
        description=description,
    )

    query: dict[str, Any] = {
        "verified": {"$ne": True},
        "rejected": {"$ne": True},
        "$and": and_clauses,
    }
    if (truck or "").strip():
        query["truck_number"] = {
            "$regex": re.escape(truck.strip()),
            "$options": "i",
        }
    if cashier_id:
        query["cashier_id"] = (
            cashier_id
            if isinstance(cashier_id, ObjectId)
            else ObjectId(str(cashier_id))
        )
    if date_from or date_to:
        df: dict[str, Any] = {}
        if date_from:
            df["$gte"] = date_from
        if date_to:
            df["$lte"] = date_to
        query["date"] = df
    return query


def doc_money_parts(doc: dict[str, Any]) -> tuple[float, float]:
    """Return (tzs, usd) for one transaction doc — mirrors desktop ``money_parts``.

    Used by upload-batch totals and test fakes so TZS-only, USD-only, and
    dual-currency rows each contribute correctly.
    """
    cur = str(doc.get("currency") or "TZS").upper()
    amt = float(doc.get("amount") or 0.0)
    amount_usd = doc.get("amount_usd")
    if amount_usd is not None:
        usd = float(amount_usd or 0.0)
        if cur == "USD" and abs(amt - usd) < 1e-9:
            return 0.0, usd
        return amt, usd
    if cur == "USD":
        return 0.0, amt
    return amt, 0.0


def _tzs_amount_expr() -> dict[str, Any]:
    """Per-row TZS contribution for ``$sum`` (see ``doc_money_parts``)."""
    return {
        "$let": {
            "vars": {
                "cur": {"$toUpper": {"$ifNull": ["$currency", "TZS"]}},
                "amt": {"$ifNull": ["$amount", 0]},
                "usd": "$amount_usd",
            },
            "in": {
                "$cond": [
                    {"$ne": ["$$usd", None]},
                    {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$$cur", "USD"]},
                                    {"$eq": ["$$amt", "$$usd"]},
                                ]
                            },
                            0,
                            "$$amt",
                        ]
                    },
                    {
                        "$cond": [
                            {"$eq": ["$$cur", "USD"]},
                            0,
                            "$$amt",
                        ]
                    },
                ]
            },
        }
    }


def _usd_amount_expr() -> dict[str, Any]:
    """Per-row USD contribution for ``$sum`` (see ``doc_money_parts``)."""
    return {
        "$let": {
            "vars": {
                "cur": {"$toUpper": {"$ifNull": ["$currency", "TZS"]}},
                "amt": {"$ifNull": ["$amount", 0]},
                "usd": "$amount_usd",
            },
            "in": {
                "$cond": [
                    {"$ne": ["$$usd", None]},
                    {"$ifNull": ["$$usd", 0]},
                    {
                        "$cond": [
                            {"$eq": ["$$cur", "USD"]},
                            "$$amt",
                            0,
                        ]
                    },
                ]
            },
        }
    }


def daily_uploads_pipeline(
    *,
    limit: int = 100,
    skip: int = 0,
    cashier_id: Any | None = None,
) -> list[dict[str, Any]]:
    """Group transactions into upload batches with page + true total.

    Final stage is ``$facet`` → one doc ``{items, meta:[{total}]}``.
    Use :func:`unpack_daily_uploads_page` to unwrap.
    """
    match: dict[str, Any] = {"daily_import_id": {"$type": "string", "$ne": ""}}
    if cashier_id is not None:
        match["cashier_id"] = cashier_id
    page_limit = max(1, int(limit))
    page_skip = max(0, int(skip))
    return [
        {"$match": match},
        {
            "$group": {
                "_id": "$daily_import_id",
                "source_filename": {"$first": "$daily_import_source"},
                "count": {"$sum": 1},
                "total_tzs": {"$sum": _tzs_amount_expr()},
                "total_usd": {"$sum": _usd_amount_expr()},
                "primary_date": {"$first": "$import_primary_date"},
                "min_date": {"$min": "$date"},
                "max_date": {"$max": "$date"},
                "created_at": {"$min": "$created_at"},
                "cashier_id": {"$first": "$cashier_id"},
                "outlier_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": ["$import_primary_date", None]},
                                    {
                                        "$ne": [
                                            {
                                                "$dateToString": {
                                                    "format": "%Y-%m-%d",
                                                    "date": "$date",
                                                }
                                            },
                                            {
                                                "$dateToString": {
                                                    "format": "%Y-%m-%d",
                                                    "date": "$import_primary_date",
                                                }
                                            },
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "duplicate_count": {
                    "$sum": {"$cond": ["$possible_duplicate", 1, 0]}
                },
            }
        },
        {"$sort": {"created_at": -1}},
        {
            "$facet": {
                "items": [
                    {"$skip": page_skip},
                    {"$limit": page_limit},
                ],
                "meta": [{"$count": "total"}],
            }
        },
    ]


def unpack_daily_uploads_page(
    facet_rows: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], int]:
    """Unwrap ``daily_uploads_pipeline`` facet result → (items, total)."""
    if not facet_rows:
        return [], 0
    row = facet_rows[0] or {}
    items = list(row.get("items") or [])
    meta = row.get("meta") or []
    if not meta:
        return items, 0
    return items, int((meta[0] or {}).get("total") or 0)


def null_daily_import_id_filter() -> dict[str, Any]:
    return {
        "$or": [
            {"daily_import_id": None},
            {"daily_import_id": ""},
            {"daily_import_id": {"$type": "null"}},
        ]
    }


def new_upload_id() -> str:
    return str(uuid.uuid4())


async def commit_daily_to_master(
    db: Any,
    payloads: Sequence[dict[str, Any]],
    *,
    verified_by: Optional[ObjectId] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Insert daily-import rows as verified Master Expenses.

    Rows without an item are skipped. Each inserted doc carries
    ``import_row_key`` so a retry of the same upload cannot duplicate rows.
    """
    await ensure_import_indexes(db)
    stamp = now or datetime.utcnow()
    inserted = 0
    duplicates = 0
    skipped_no_item = 0
    batch: list[dict[str, Any]] = []
    upload_id = ""
    source = ""
    min_date: Optional[datetime] = None
    max_date: Optional[datetime] = None
    row_index = 0

    for raw in payloads:
        payload = dict(raw)
        item_name = (payload.get("category_name") or payload.get("item") or "").strip()
        if not item_name:
            skipped_no_item += 1
            continue
        if not upload_id:
            upload_id = str(payload.get("daily_import_id") or new_upload_id())
        if not source:
            source = str(payload.get("daily_import_source") or "")
        payload["daily_import_id"] = upload_id
        if source:
            payload["daily_import_source"] = source
        doc = verified_doc_from_daily_payload(
            payload,
            verified_by=verified_by,
            day_order=row_index,
            now=stamp,
        )
        row_index += 1
        dt = doc.get("date")
        if isinstance(dt, datetime):
            if min_date is None or dt < min_date:
                min_date = dt
            if max_date is None or dt > max_date:
                max_date = dt
        doc["import_row_key"] = daily_import_row_key(payload)
        batch.append(doc)
        if len(batch) >= IMPORT_BATCH_SIZE:
            n, d = await insert_many_idempotent(db.transactions, batch)
            inserted += n
            duplicates += d
            batch.clear()

    if batch:
        n, d = await insert_many_idempotent(db.transactions, batch)
        inserted += n
        duplicates += d

    return {
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "skipped_no_item": skipped_no_item,
        "upload_id": upload_id,
        "source": source,
        "min_date": min_date,
        "max_date": max_date,
    }


async def commit_master_import(
    db: Any,
    rows: Sequence[dict[str, Any]],
    *,
    source_filename: str,
    verified_by: Optional[ObjectId] = None,
    skip_duplicates: bool = True,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Insert staged master-import rows as verified transactions."""
    await ensure_import_indexes(db)
    stamp = now or datetime.utcnow()
    upload_id = new_upload_id()
    source = (source_filename or "").strip() or "master.xlsx"
    inserted = 0
    duplicates = 0
    batch: list[dict[str, Any]] = []

    existing_serials: set[int] = set()
    if skip_duplicates:
        serials = [
            int(r["serial"])
            for r in rows
            if r.get("serial") is not None and r.get("serial") != ""
        ]
        if serials:
            cursor = db.transactions.find(
                {
                    "master_import_source": source,
                    "master_serial": {"$in": serials},
                },
                {"master_serial": 1},
            )
            docs = await cursor.to_list(length=None)
            existing_serials = {
                int(d["master_serial"]) for d in docs if "master_serial" in d
            }

    for row in rows:
        serial = row.get("serial")
        if (
            skip_duplicates
            and serial is not None
            and serial != ""
            and int(serial) in existing_serials
        ):
            duplicates += 1
            continue
        doc = verified_doc_from_master_payload(
            row,
            upload_id=upload_id,
            source_filename=source,
            verified_by=verified_by,
            now=stamp,
        )
        batch.append(doc)
        if len(batch) >= IMPORT_BATCH_SIZE:
            n, d = await insert_many_idempotent(db.transactions, batch)
            inserted += n
            duplicates += d
            batch.clear()

    if batch:
        n, d = await insert_many_idempotent(db.transactions, batch)
        inserted += n
        duplicates += d

    return {
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "upload_id": upload_id,
        "source": source,
    }
