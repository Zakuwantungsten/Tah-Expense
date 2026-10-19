"""Cashier transaction HTTP routes (Phase 2)."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request, Response, status
from pymongo import UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError

from .audit_ops import record_operation_event
from .dependencies import Authenticated, require_roles
from .errors import ApiError
from .schemas import (
    ApplyDescriptionMappingRequest,
    BulkIdsRequest,
    BulkTransactionPatchRequest,
    DayOrderRequest,
    DiscardDraftDayRequest,
    DuplicateCheckBulkRequest,
    DuplicateCheckRequest,
    PendingEditRequest,
    RequestDeleteBody,
    ResubmitRejectedRequest,
    SubmitDayRequest,
    TransactionBulkCreateRequest,
    TransactionCreate,
    TransactionIdsRequest,
    TransactionPatch,
)
from .security import utcnow
from .serialization import json_safe, object_id
from .attachments_api import attachments as attachments_router
from .cashier_overview_ops import compute_overview_charts
from .import_ops import (
    daily_uploads_pipeline,
    null_daily_import_id_filter,
    unpack_daily_uploads_page,
)
from .mongo_async import aggregate_list
from .transaction_ops import (
    DRAFT_BASE_MATCH,
    OPEN_REGISTER_MATCH,
    REGISTER_DAY_EXPR,
    browse_match,
    filter_pending_edit_clones,
    register_day_clause,
    register_day_range_clause,
)

_log = logging.getLogger("tahmeed.transactions")

transactions = APIRouter(prefix="/transactions", tags=["transactions"])
transactions.include_router(attachments_router)

_REGISTER_ROLES = ("admin", "cashier", "accountant")
RegisterActor = Annotated[dict[str, Any], Depends(require_roles(*_REGISTER_ROLES))]

_client_row_index_ensured = False


def _normalize_user_oid(value: Any) -> ObjectId | None:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return value
    try:
        return object_id(str(value))
    except ValueError:
        return None


async def _cashier_names_map(db: Any, cashier_ids: list[ObjectId]) -> dict[str, str]:
    """Bulk user display names keyed by ObjectId string (Browse Phase 1)."""
    oids = [oid for oid in cashier_ids if oid is not None]
    if not oids:
        return {}
    try:
        docs = await db.users.find(
            {"_id": {"$in": list(set(oids))}},
            {"_id": 1, "full_name": 1, "username": 1},
        ).to_list(length=None)
    except Exception:
        return {str(oid): "Unknown cashier" for oid in set(oids)}
    found: dict[str, str] = {}
    for doc in docs:
        name = (doc.get("full_name") or "").strip()
        if not name:
            name = (doc.get("username") or "").strip()
        found[str(doc["_id"])] = name or "Unknown cashier"
    return {
        str(oid): found.get(str(oid), "Unknown cashier")
        for oid in set(oids)
    }


async def _ensure_client_row_id_index(db: Any) -> None:
    """Best-effort unique index for idempotent register creates (safe to call often)."""
    global _client_row_index_ensured
    if _client_row_index_ensured:
        return
    try:
        await db.transactions.create_index(
            [("cashier_id", 1), ("client_row_id", 1)],
            name="uniq_cashier_client_row",
            unique=True,
            partialFilterExpression={
                "client_row_id": {"$type": "string"},
                "cashier_id": {"$exists": True},
            },
        )
    except Exception:
        # Pre-existing duplicates or permissions — find-before-insert still works;
        # migrate.py is the authoritative conflict reporter.
        pass
    _client_row_index_ensured = True

_OBJECT_ID_FIELDS = {
    "category_id",
    "cashier_id",
    "verified_by",
    "last_edited_by",
    "deletion_requested_by",
    "trashed_by",
    "restore_requested_by",
    "original_transaction_id",
    "duplicate_of_id",
}
_DATETIME_FIELDS = {
    "date",
    "reported_date",
    "verified_at",
    "last_edited_at",
    "deletion_requested_at",
    "trashed_at",
    "restore_requested_at",
    "created_at",
    "import_primary_date",
}


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _valid_id(value: str) -> ObjectId:
    try:
        return object_id(value)
    except ValueError:
        raise ApiError(422, "invalid_id", "Resource id is invalid") from None


def _parse_optional_oid(value: Any) -> ObjectId | None:
    if value is None or value == "":
        return None
    if isinstance(value, ObjectId):
        return value
    try:
        return object_id(str(value))
    except ValueError:
        raise ApiError(422, "invalid_id", f"Invalid object id: {value}") from None


def _coerce_write_fields(values: dict[str, Any]) -> dict[str, Any]:
    out = dict(values)
    for field in _OBJECT_ID_FIELDS:
        if field in out:
            out[field] = _parse_optional_oid(out[field])
    for field in _DATETIME_FIELDS:
        if field not in out:
            continue
        raw = out[field]
        if raw is None or isinstance(raw, datetime):
            continue
        if isinstance(raw, str):
            try:
                out[field] = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            except ValueError as exc:
                raise ApiError(
                    422, "invalid_datetime", f"Invalid datetime for {field}"
                ) from exc
    return out


def _create_document(body: TransactionCreate, actor: dict[str, Any]) -> dict[str, Any]:
    raw = body.model_dump(exclude_none=False)
    for key in (
        "daily_import_id",
        "daily_import_source",
        "import_primary_date",
        "amount_usd",
        "client_row_id",
        "duplicate_of_id",
    ):
        if raw.get(key) in (None, ""):
            raw.pop(key, None)
    doc = _coerce_write_fields(raw)
    if not doc.get("cashier_id"):
        doc["cashier_id"] = actor.get("_id")
    doc.setdefault("created_at", utcnow().replace(tzinfo=None))
    doc.setdefault("verified", False)
    doc.setdefault("rejected", False)
    doc.setdefault("discarded", False)
    doc.setdefault("deletion_requested", False)
    doc.setdefault("trashed", False)
    doc.setdefault("restore_requested", False)
    doc.setdefault("register_status", "draft")
    doc.setdefault("attachments", [])
    return doc


@transactions.post("", status_code=201)
async def create_transaction(
    body: TransactionCreate,
    request: Request,
    actor: RegisterActor,
    response: Response,
) -> dict:
    """Create a register row.

    When ``client_row_id`` is present, create is idempotent for that cashier:
    a retry after timeout / lost ACK returns the existing document (200) instead
    of inserting a duplicate expense.
    """
    doc = _create_document(body, actor)
    db = _database(request)
    await _ensure_client_row_id_index(db)
    status_key, saved = await _create_one_transaction_doc(
        db=db,
        doc=doc,
        actor=actor,
        request=request,
        record_event=True,
    )
    if status_key == "already_exists":
        response.status_code = status.HTTP_200_OK
    return json_safe(saved)


@transactions.get("")
async def list_transactions_by_ids(
    request: Request,
    _user: Authenticated,
    ids: str = Query("", description="Comma-separated ObjectId hex strings"),
) -> dict:
    raw_ids = [part.strip() for part in ids.split(",") if part.strip()]
    if not raw_ids:
        return {"items": [], "total": 0}
    oids = [_valid_id(value) for value in raw_ids]
    docs = await _database(request).transactions.find({"_id": {"$in": oids}}).to_list(
        length=len(oids)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@transactions.get("/by-date")
async def transactions_by_date(
    request: Request,
    actor: RegisterActor,
    target_date: date = Query(..., alias="date"),
    cashier_id: str | None = None,
    merged: bool = False,
) -> dict:
    query: dict[str, Any] = {
        **register_day_clause(target_date),
        "rejected": {"$ne": True},
        "deletion_requested": {"$ne": True},
        "trashed": {"$ne": True},
    }
    if not merged:
        if cashier_id:
            query["cashier_id"] = _valid_id(cashier_id)
        else:
            query["cashier_id"] = actor["_id"]
    cursor = (
        _database(request)
        .transactions.find(query)
        .sort([("day_order", 1), ("created_at", 1)])
    )
    docs = await cursor.to_list(length=None)
    docs = filter_pending_edit_clones(docs)
    return {"items": json_safe(docs), "total": len(docs)}


@transactions.get("/search")
async def search_transactions(
    request: Request,
    _actor: RegisterActor,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str = "",
    truck: str = "",
    limit: int = Query(500, ge=1, le=2000),
) -> dict:
    query: dict[str, Any] = {
        "trashed": {"$ne": True},
        "deletion_requested": {"$ne": True},
    }
    if date_from or date_to:
        date_filter: dict[str, Any] = {}
        if date_from:
            date_filter["$gte"] = datetime(date_from.year, date_from.month, date_from.day)
        if date_to:
            date_filter["$lte"] = datetime(
                date_to.year, date_to.month, date_to.day, 23, 59, 59
            )
        query["date"] = date_filter
    if keyword.strip():
        query["description"] = {
            "$regex": re.escape(keyword.strip()),
            "$options": "i",
        }
    if truck.strip():
        query["truck_number"] = {
            "$regex": re.escape(truck.strip()),
            "$options": "i",
        }
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort([("date", -1), ("created_at", -1)])
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@transactions.get("/overview-stats")
async def overview_stats(
    request: Request,
    actor: RegisterActor,
    cashier_id: str | None = None,
) -> dict:
    """Month/today KPIs for Cashier Overview — all cashiers by default.

    Optional ``cashier_id`` narrows the aggregate; otherwise Overview mirrors
    Merged browse and includes every non-trashed row for the period.
    """
    _ = actor  # auth via RegisterActor
    db = _database(request)
    today = date.today()
    today_start = datetime(today.year, today.month, today.day)
    today_end = datetime(today.year, today.month, today.day, 23, 59, 59)
    month_start = datetime(today.year, today.month, 1)
    scope = _valid_id(cashier_id) if cashier_id else None

    def _money_group(match: dict[str, Any]) -> list[dict[str, Any]]:
        scoped: dict[str, Any] = {**match, "trashed": {"$ne": True}}
        if scope is not None:
            scoped["cashier_id"] = scope
        return [
            {"$match": scoped},
            {
                "$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "tzs_total": {"$sum": "$amount"},
                    "receipt_received": {
                        "$sum": {
                            "$cond": [{"$eq": ["$receipt_status", "received"]}, 1, 0]
                        }
                    },
                    "receipt_pending": {
                        "$sum": {
                            "$cond": [{"$eq": ["$receipt_status", "pending"]}, 1, 0]
                        }
                    },
                    "receipt_missing": {
                        "$sum": {
                            "$cond": [{"$eq": ["$receipt_status", "missing"]}, 1, 0]
                        }
                    },
                    "verified": {
                        "$sum": {"$cond": [{"$eq": ["$verified", True]}, 1, 0]}
                    },
                    "unverified": {
                        "$sum": {"$cond": [{"$eq": ["$verified", False]}, 1, 0]}
                    },
                }
            },
        ]

    today_res = await aggregate_list(db.transactions, 
        _money_group({"date": {"$gte": today_start, "$lte": today_end}})
    , 1)
    month_res = await aggregate_list(db.transactions, 
        _money_group({"date": {"$gte": month_start}})
    , 1)
    recent_match: dict[str, Any] = {"trashed": {"$ne": True}}
    if scope is not None:
        recent_match["cashier_id"] = scope
    recent_docs = (
        await db.transactions.find(recent_match)
        .sort([("date", -1), ("created_at", -1)])
        .limit(8)
        .to_list(8)
    )
    empty = {
        "count": 0,
        "tzs_total": 0.0,
        "receipt_received": 0,
        "receipt_pending": 0,
        "receipt_missing": 0,
        "verified": 0,
        "unverified": 0,
    }

    def _strip_id(row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out.pop("_id", None)
        return out

    return json_safe(
        {
            "today": _strip_id(today_res[0]) if today_res else empty,
            "month": _strip_id(month_res[0]) if month_res else empty,
            "recent": recent_docs,
        }
    )


@transactions.get("/overview-charts")
async def overview_charts(
    request: Request,
    actor: RegisterActor,
    year: int | None = None,
    top_items: int = Query(4, ge=1, le=12),
    top_payees: int = Query(5, ge=1, le=12),
    cashier_id: str | None = None,
) -> dict:
    """Item donut + daily money/counts + named-payee series for cashier Overview.

    Aggregates all cashiers by default (same as Merged browse). Pass
    ``cashier_id`` only when intentionally narrowing to one owner.
    """
    _ = actor  # auth via RegisterActor
    fy = int(year or date.today().year)
    year_start = datetime(fy, 1, 1)
    year_end = datetime(fy, 12, 31, 23, 59, 59)
    match: dict[str, Any] = {
        "date": {"$gte": year_start, "$lte": year_end},
        "trashed": {"$ne": True},
        "discarded": {"$ne": True},
    }
    if cashier_id:
        match["cashier_id"] = _valid_id(cashier_id)
    docs = (
        await _database(request)
        .transactions.find(
            match,
            {
                "date": 1,
                "amount": 1,
                "currency": 1,
                "item": 1,
                "category_name": 1,
                "payee": 1,
                "trashed": 1,
                "discarded": 1,
            },
        )
        .to_list(length=50_000)
    )
    return json_safe(
        compute_overview_charts(
            docs,
            year=fy,
            top_items=top_items,
            top_payees=top_payees,
        )
    )


@transactions.get("/daily-uploads")
async def cashier_daily_uploads(
    request: Request,
    actor: RegisterActor,
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
    cashier_id: str | None = None,
) -> dict:
    """Excel upload batches for Browse → Uploads.

    Shared list (all cashiers) by default — same idea as Merged browse.
    Optional ``cashier_id`` narrows; phantom null-id heal stays own-only for cashiers.
    Composite: ``{items, total, cashier_names}``. ``total`` is the full batch
    count (not page length). Each item includes ``total_tzs`` / ``total_usd``.
    """
    scope: ObjectId | None = _valid_id(cashier_id) if cashier_id else None
    db = _database(request)
    heal_filter: dict[str, Any] = null_daily_import_id_filter()
    heal_scope = scope
    if heal_scope is None and str(actor.get("role") or "").lower() == "cashier":
        heal_scope = _actor_cashier_id(actor, None)
    if heal_scope is not None:
        heal_filter = {"$and": [heal_filter, {"cashier_id": heal_scope}]}
    await db.transactions.update_many(
        heal_filter,
        {
            "$unset": {
                "daily_import_id": "",
                "daily_import_source": "",
                "import_primary_date": "",
            }
        },
    )
    facet_rows = await aggregate_list(
        db.transactions,
        daily_uploads_pipeline(limit=limit, skip=skip, cashier_id=scope),
        length=1,
    )
    rows, total = unpack_daily_uploads_page(facet_rows)
    cashier_oids = [
        oid
        for oid in (_normalize_user_oid(row.get("cashier_id")) for row in rows)
        if oid is not None
    ]
    names = await _cashier_names_map(db, cashier_oids)
    # Composite payload. Clients that still expect a bare list keep working
    # via ``payload if list else payload["items"]``.
    return json_safe(
        {
            "items": rows,
            "total": total,
            "cashier_names": names,
        }
    )


@transactions.get("/by-category")
async def transactions_by_category(
    request: Request,
    _actor: RegisterActor,
    category_name: str = Query(...),
    cashier_id: str | None = None,
    description: str = "",
    limit: int = Query(500, ge=1, le=2000),
) -> dict:
    query: dict[str, Any] = {"category_name": category_name}
    if cashier_id:
        query["cashier_id"] = _valid_id(cashier_id)
    if description.strip():
        query["description"] = {
            "$regex": re.escape(description.strip()),
            "$options": "i",
        }
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort([("date", -1), ("created_at", -1)])
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@transactions.get("/daily-summaries")
async def daily_summaries(
    request: Request,
    _actor: RegisterActor,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str = "",
    truck: str = "",
    category_name: str = "",
    descriptions: str = "",
    daily_import_id: str = "",
    limit: int = Query(365, ge=1, le=2000),
) -> dict:
    uid = (daily_import_id or "").strip()
    match = browse_match(
        date_from=date_from if uid else None,
        date_to=date_to if uid else None,
        keyword=keyword,
        truck=truck,
        category_name=category_name,
        descriptions=descriptions,
        daily_import_id=daily_import_id,
    )
    if not uid and (date_from or date_to):
        range_clause = register_day_range_clause(date_from, date_to)
        if range_clause:
            if "$and" in match:
                match["$and"] = [range_clause, *match["$and"]]
            elif match:
                match = {"$and": [range_clause, match]}
            else:
                match = range_clause

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {
                    "year": {"$year": REGISTER_DAY_EXPR},
                    "month": {"$month": REGISTER_DAY_EXPR},
                    "day": {"$dayOfMonth": REGISTER_DAY_EXPR},
                },
                "entries_count": {"$sum": 1},
                "total_tzs": {"$sum": "$amount"},
                "total_refund": {
                    "$sum": {"$cond": [{"$eq": ["$notes_flag", True]}, "$amount", 0]}
                },
                "cashier_ids": {"$addToSet": "$cashier_id"},
                "draft_count": {
                    "$sum": {"$cond": [{"$eq": ["$register_status", "draft"]}, 1, 0]}
                },
                "submitted_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$or": [
                                    {"$eq": ["$register_status", "submitted"]},
                                    {"$eq": [{"$type": "$register_status"}, "missing"]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {"$sort": {"_id.year": -1, "_id.month": -1, "_id.day": -1}},
        {"$limit": limit},
    ]
    db = _database(request)
    docs = await aggregate_list(db.transactions, pipeline, length=limit)
    items = []
    all_cashier_oids: list[ObjectId] = []
    for doc in docs:
        group = doc["_id"]
        cashier_ids = [
            cid for cid in (doc.get("cashier_ids") or []) if cid is not None
        ]
        for cid in cashier_ids:
            oid = _normalize_user_oid(cid)
            if oid is not None:
                all_cashier_oids.append(oid)
        items.append(
            {
                "date": date(group["year"], group["month"], group["day"]).isoformat(),
                "entries_count": doc["entries_count"],
                "total_tzs": doc["total_tzs"],
                "total_refund": doc["total_refund"],
                "cashier_ids": cashier_ids,
                "draft_count": int(doc.get("draft_count") or 0),
                "submitted_count": int(doc.get("submitted_count") or 0),
            }
        )
    names = await _cashier_names_map(db, all_cashier_oids)
    return json_safe(
        {"items": items, "total": len(items), "cashier_names": names}
    )


@transactions.get("/flat")
async def transactions_flat(
    request: Request,
    _actor: RegisterActor,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str = "",
    category_name: str = "",
    descriptions: str = "",
    daily_import_id: str = "",
    limit: int = Query(1000, ge=1, le=5000),
) -> dict:
    match = browse_match(
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
        category_name=category_name,
        descriptions=descriptions,
        daily_import_id=daily_import_id,
    )
    db = _database(request)
    docs = (
        await db.transactions.find(match)
        .sort([("date", -1), ("created_at", -1)])
        .limit(limit)
        .to_list(length=limit)
    )
    # Real match count (not page length) + names for this page — one RTT.
    total = await db.transactions.count_documents(match)
    cashier_oids: list[ObjectId] = []
    for doc in docs:
        for key in ("cashier_id", "last_edited_by"):
            oid = _normalize_user_oid(doc.get(key))
            if oid is not None:
                cashier_oids.append(oid)
    names = await _cashier_names_map(db, cashier_oids)
    return {
        "items": json_safe(docs),
        "total": int(total),
        "cashier_names": names,
    }


@transactions.get("/browse/items")
async def browse_items(
    request: Request,
    _actor: RegisterActor,
    date_from: date | None = None,
    date_to: date | None = None,
    descriptions: str = "",
) -> dict:
    match = browse_match(
        date_from=date_from,
        date_to=date_to,
        descriptions=descriptions or [],
    )
    db = _database(request)
    items = await db.transactions.distinct("item", match)
    cats = await db.transactions.distinct("category_name", match)
    names = sorted({*(v for v in items if v), *(v for v in cats if v)}, key=str.lower)
    return {"items": names, "total": len(names)}


@transactions.get("/browse/descriptions")
async def browse_descriptions(
    request: Request,
    _actor: RegisterActor,
    date_from: date | None = None,
    date_to: date | None = None,
    category_name: str = "",
) -> dict:
    match = browse_match(
        date_from=date_from,
        date_to=date_to,
        category_name=category_name or [],
    )
    vals = await _database(request).transactions.distinct("description", match)
    names = sorted((v for v in vals if v), key=str.lower)
    return {"items": names, "total": len(names)}


async def _browse_month_items(db: Any) -> list[dict[str, int]]:
    pipeline = [
        {
            "$group": {
                "_id": {
                    "year": {"$year": "$date"},
                    "month": {"$month": "$date"},
                }
            }
        },
        {"$sort": {"_id.year": -1, "_id.month": -1}},
    ]
    docs = await aggregate_list(db.transactions, pipeline, length=None)
    return [
        {"year": int(doc["_id"]["year"]), "month": int(doc["_id"]["month"])}
        for doc in docs
        if doc.get("_id")
    ]


def _browse_years_from_months(months: list[dict[str, int]]) -> list[int]:
    years = [int(row["year"]) for row in months if row.get("year") is not None]
    today_y = date.today().year
    for year in (today_y + 1, today_y, today_y - 1):
        if year not in years:
            years.append(year)
    return sorted(set(years), reverse=True)


@transactions.get("/meta/months")
async def available_months(request: Request, _actor: RegisterActor) -> dict:
    items = await _browse_month_items(_database(request))
    return {"items": items, "total": len(items)}


@transactions.get("/meta/years")
async def available_years(request: Request, _actor: RegisterActor) -> dict:
    months = await _browse_month_items(_database(request))
    years = _browse_years_from_months(months)
    return {"items": years, "total": len(years)}


@transactions.get("/meta/bootstrap")
async def browse_meta_bootstrap(request: Request, _actor: RegisterActor) -> dict:
    """Browse Phase 3 — years + months in one RTT for period combos."""
    months = await _browse_month_items(_database(request))
    years = _browse_years_from_months(months)
    return {
        "years": years,
        "months": months,
        "total_years": len(years),
        "total_months": len(months),
    }


@transactions.get("/descriptions")
async def search_descriptions(
    request: Request,
    _actor: RegisterActor,
    prefix: str = "",
    limit: int = Query(12, ge=1, le=5000),
) -> dict:
    value = (prefix or "").strip()
    pipeline: list[dict[str, Any]] = [
        {"$match": {"description": {"$type": "string", "$ne": ""}}},
        {
            "$group": {
                "_id": {"$toUpper": "$description"},
                "n": {"$sum": 1},
            }
        },
    ]
    if value:
        pipeline.append(
            {"$match": {"_id": {"$regex": f"^{re.escape(value.upper())}"}}}
        )
    pipeline.extend(
        [
            {"$sort": {"n": -1, "_id": 1}},
            {"$limit": limit},
        ]
    )
    docs = await aggregate_list(_database(request).transactions, pipeline, length=limit)
    items = [str(doc.get("_id") or "").strip() for doc in docs if doc.get("_id")]
    items = [name for name in items if name]
    return {"items": items, "total": len(items)}


@transactions.get("/resolve-item")
async def resolve_item_name(
    request: Request,
    _actor: RegisterActor,
    description: str = "",
) -> dict:
    key = (description or "").strip()
    if not key:
        return {"item": None, "payee": None}
    db = _database(request)
    mapping = await db.description_mappings.find_one(
        {"description": {"$regex": f"^{re.escape(key)}$", "$options": "i"}}
    )
    if mapping:
        name = (mapping.get("category_name") or "").strip()
        payee = (mapping.get("payee") or "").strip()
        if name or payee:
            return {
                "item": name or None,
                "payee": payee or None,
            }

    desc_match = {"description": {"$regex": f"^{re.escape(key)}$", "$options": "i"}}
    has_item = {
        "$or": [
            {"item": {"$type": "string", "$ne": ""}},
            {"category_name": {"$type": "string", "$ne": ""}},
        ]
    }
    projection = {"item": 1, "category_name": 1, "payee": 1}

    async def _first(extra: dict[str, Any]) -> dict[str, Any] | None:
        docs = (
            await db.transactions.find({**desc_match, **has_item, **extra}, projection)
            .sort([("date", -1), ("created_at", -1)])
            .limit(1)
            .to_list(length=1)
        )
        return docs[0] if docs else None

    doc = await _first({"verified": True})
    if not doc:
        doc = await _first({})
    if not doc:
        return {"item": None, "payee": None}
    return {
        "item": (doc.get("item") or doc.get("category_name") or "").strip() or None,
        # History may carry a payee, but mapping remains the source of truth.
        "payee": (doc.get("payee") or "").strip() or None,
    }


def _parse_ids(raw_ids: list[str]) -> list[ObjectId]:
    return [_valid_id(value) for value in raw_ids if value.strip()]


def _actor_cashier_id(actor: dict[str, Any], override: str | None) -> ObjectId:
    if override:
        return _valid_id(override)
    actor_id = actor.get("_id")
    if isinstance(actor_id, ObjectId):
        return actor_id
    if actor_id:
        return _valid_id(str(actor_id))
    raise ApiError(422, "cashier_required", "cashier_id is required")


@transactions.get("/cashier/rejected")
async def cashier_rejected(
    request: Request,
    actor: RegisterActor,
    cashier_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> dict:
    scope = _actor_cashier_id(actor, cashier_id)
    docs = (
        await _database(request)
        .transactions.find(
            {
                "cashier_id": scope,
                "rejected": True,
                "discarded": {"$ne": True},
            }
        )
        .sort([("date", -1), ("created_at", -1)])
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@transactions.get("/cashier/discarded")
async def cashier_discarded(
    request: Request,
    actor: RegisterActor,
    cashier_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> dict:
    scope = _actor_cashier_id(actor, cashier_id)
    docs = (
        await _database(request)
        .transactions.find({"cashier_id": scope, "discarded": True})
        .sort([("date", -1), ("created_at", -1)])
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@transactions.post("/cashier/discard")
async def discard_transactions(
    body: TransactionIdsRequest, request: Request, actor: RegisterActor
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    scope = _actor_cashier_id(actor, body.cashier_id)
    db = _database(request)
    result = await db.transactions.update_many(
        {
            "_id": {"$in": ids},
            "cashier_id": scope,
            "rejected": True,
            "discarded": {"$ne": True},
        },
        {"$set": {"discarded": True}},
    )
    await record_operation_event(
        db,
        action="txn.discard",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(oid) for oid in ids],
        details={"updated": int(result.modified_count)},
    )
    return {"updated": int(result.modified_count)}


@transactions.post("/cashier/restore-discarded")
async def restore_discarded_transactions(
    body: TransactionIdsRequest, request: Request, actor: RegisterActor
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    scope = _actor_cashier_id(actor, body.cashier_id)
    db = _database(request)
    result = await db.transactions.update_many(
        {
            "_id": {"$in": ids},
            "cashier_id": scope,
            "discarded": True,
        },
        {"$set": {"discarded": False}},
    )
    await record_operation_event(
        db,
        action="txn.restore_discarded",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(oid) for oid in ids],
        details={"updated": int(result.modified_count)},
    )
    return {"updated": int(result.modified_count)}


@transactions.post("/cashier/delete-discarded")
async def delete_discarded_transactions(
    body: TransactionIdsRequest, request: Request, actor: RegisterActor
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"deleted": 0}
    scope = _actor_cashier_id(actor, body.cashier_id)
    db = _database(request)
    result = await db.transactions.delete_many(
        {
            "_id": {"$in": ids},
            "cashier_id": scope,
            "discarded": True,
        }
    )
    await record_operation_event(
        db,
        action="txn.delete_discarded",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(oid) for oid in ids],
        details={"deleted": int(result.deleted_count)},
    )
    return {"deleted": int(result.deleted_count)}


@transactions.post("/cashier/resubmit")
async def resubmit_rejected_transactions(
    body: ResubmitRejectedRequest, request: Request, actor: RegisterActor
) -> dict:
    if not body.updates_by_id:
        return {"updated": 0}
    scope = _actor_cashier_id(actor, body.cashier_id)
    db = _database(request)
    now = utcnow().replace(tzinfo=None)
    updated = 0
    for raw_id, field_updates in body.updates_by_id.items():
        payload = _coerce_write_fields(dict(field_updates or {}))
        payload.update(
            {
                "rejected": False,
                "rejection_reason": None,
                "discarded": False,
                "last_edited_at": now,
                "last_edited_by": scope,
            }
        )
        result = await db.transactions.update_one(
            {
                "_id": _valid_id(raw_id),
                "cashier_id": scope,
                "rejected": True,
                "discarded": {"$ne": True},
            },
            {"$set": payload},
        )
        if result.modified_count:
            updated += 1
    await record_operation_event(
        db,
        action="txn.resubmit",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=list(body.updates_by_id.keys())[:100],
        details={"updated": updated},
    )
    return {"updated": updated}


@transactions.get("/drafts/count")
async def count_draft_transactions(
    request: Request,
    actor: RegisterActor,
    cashier_id: str | None = None,
    all_cashiers: bool = False,
) -> dict:
    query: dict[str, Any] = dict(DRAFT_BASE_MATCH)
    if not all_cashiers:
        query["cashier_id"] = _actor_cashier_id(actor, cashier_id)
    total = await _database(request).transactions.count_documents(query)
    return {"count": int(total)}


@transactions.get("/drafts/day-summaries")
async def draft_day_summaries(
    request: Request,
    actor: RegisterActor,
    cashier_id: str | None = None,
    all_cashiers: bool = False,
    limit: int = Query(120, ge=1, le=500),
) -> dict:
    match: dict[str, Any] = dict(DRAFT_BASE_MATCH)
    if not all_cashiers:
        match["cashier_id"] = _actor_cashier_id(actor, cashier_id)
    pipeline = [
        {"$match": match},
        {"$addFields": {"register_day": REGISTER_DAY_EXPR}},
        {
            "$group": {
                "_id": {
                    "year": {"$year": "$register_day"},
                    "month": {"$month": "$register_day"},
                    "day": {"$dayOfMonth": "$register_day"},
                },
                "entries_count": {"$sum": 1},
                "total_tzs": {"$sum": "$amount"},
                "cashier_ids": {"$addToSet": "$cashier_id"},
            }
        },
        {"$sort": {"_id.year": -1, "_id.month": -1, "_id.day": -1}},
        {"$limit": limit},
    ]
    docs = await aggregate_list(_database(request).transactions, pipeline, length=limit)
    items = []
    for doc in docs:
        group = doc["_id"]
        cashier_ids = [
            cid for cid in (doc.get("cashier_ids") or []) if cid is not None
        ]
        items.append(
            {
                "date": date(group["year"], group["month"], group["day"]).isoformat(),
                "entries_count": int(doc.get("entries_count") or 0),
                "total_tzs": float(doc.get("total_tzs") or 0.0),
                "cashier_ids": cashier_ids,
            }
        )
    return json_safe({"items": items, "total": len(items)})


@transactions.get("/open-day-summaries")
async def open_day_summaries(
    request: Request,
    actor: RegisterActor,
    cashier_id: str | None = None,
    all_cashiers: bool = False,
    limit: int = Query(120, ge=1, le=500),
) -> dict:
    """Calendar days with open (unverified) register rows — Day Issues All days."""
    match: dict[str, Any] = dict(OPEN_REGISTER_MATCH)
    if not all_cashiers:
        match["cashier_id"] = _actor_cashier_id(actor, cashier_id)
    pipeline = [
        {"$match": match},
        {"$addFields": {"register_day": REGISTER_DAY_EXPR}},
        {
            "$group": {
                "_id": {
                    "year": {"$year": "$register_day"},
                    "month": {"$month": "$register_day"},
                    "day": {"$dayOfMonth": "$register_day"},
                },
                "entries_count": {"$sum": 1},
                "cashier_ids": {"$addToSet": "$cashier_id"},
            }
        },
        {"$sort": {"_id.year": -1, "_id.month": -1, "_id.day": -1}},
        {"$limit": limit},
    ]
    docs = await aggregate_list(_database(request).transactions, pipeline, length=limit)
    items = []
    for doc in docs:
        group = doc["_id"]
        cashier_ids = [
            cid for cid in (doc.get("cashier_ids") or []) if cid is not None
        ]
        items.append(
            {
                "date": date(group["year"], group["month"], group["day"]).isoformat(),
                "entries_count": int(doc.get("entries_count") or 0),
                "cashier_ids": cashier_ids,
            }
        )
    return json_safe({"items": items, "total": len(items)})


@transactions.get("/drafts")
async def get_draft_transactions(
    request: Request,
    actor: RegisterActor,
    register_date: date = Query(..., alias="date"),
    cashier_id: str | None = None,
    merged: bool = False,
) -> dict:
    query: dict[str, Any] = {
        **register_day_clause(register_date),
        **DRAFT_BASE_MATCH,
    }
    if not merged:
        query["cashier_id"] = _actor_cashier_id(actor, cashier_id)
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort([("day_order", 1), ("created_at", 1)])
        .to_list(length=None)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@transactions.post("/drafts/discard")
async def discard_draft_transactions(
    body: TransactionIdsRequest, request: Request, actor: RegisterActor
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"deleted": 0}
    query: dict[str, Any] = {
        "_id": {"$in": ids},
        **DRAFT_BASE_MATCH,
    }
    if not body.allow_any_cashier:
        query["cashier_id"] = _actor_cashier_id(actor, body.cashier_id)
    db = _database(request)
    result = await db.transactions.delete_many(query)
    await record_operation_event(
        db,
        action="txn.draft_discard",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(oid) for oid in ids],
        details={"deleted": int(result.deleted_count)},
    )
    return {"deleted": int(result.deleted_count)}


@transactions.post("/drafts/discard-day")
async def discard_draft_day(
    body: DiscardDraftDayRequest, request: Request, actor: RegisterActor
) -> dict:
    """Hard-delete all draft rows for a register day (not just selected IDs)."""
    query: dict[str, Any] = {
        **register_day_clause(body.date),
        **DRAFT_BASE_MATCH,
    }
    if not body.allow_any_cashier:
        query["cashier_id"] = _actor_cashier_id(actor, body.cashier_id)
    db = _database(request)
    result = await db.transactions.delete_many(query)
    deleted = int(result.deleted_count)
    await record_operation_event(
        db,
        action="txn.draft_discard_day",
        actor=actor,
        request=request,
        entity_type="register_day",
        entity_ids=[body.date.isoformat()],
        details={"deleted": deleted, "date": body.date.isoformat()},
    )
    return {"deleted": deleted}


@transactions.post("/submit-day")
async def submit_day_for_verify(
    body: SubmitDayRequest, request: Request, actor: RegisterActor
) -> dict:
    db = _database(request)
    result = await db.transactions.update_many(
        {
            "$and": [
                register_day_clause(body.date),
                {"verified": {"$ne": True}},
                {"rejected": {"$ne": True}},
                {"discarded": {"$ne": True}},
                {
                    "$or": [
                        {"register_status": "draft"},
                        {"register_status": {"$exists": False}},
                    ]
                },
            ],
        },
        {"$set": {"register_status": "submitted"}},
    )
    await record_operation_event(
        db,
        action="txn.submit_day",
        actor=actor,
        request=request,
        entity_type="register_day",
        entity_ids=[str(body.date)],
        details={"updated": int(result.modified_count)},
    )
    return {"updated": int(result.modified_count)}


@transactions.post("/day-order", status_code=status.HTTP_204_NO_CONTENT)
async def recount_day_order(
    body: DayOrderRequest, request: Request, actor: RegisterActor
) -> Response:
    if not body.ordered_ids:
        return Response(status_code=204)
    ops = [
        UpdateOne({"_id": _valid_id(raw)}, {"$set": {"day_order": index}})
        for index, raw in enumerate(body.ordered_ids)
    ]
    db = _database(request)
    await db.transactions.bulk_write(ops, ordered=False)
    await record_operation_event(
        db,
        action="txn.day_order",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=list(body.ordered_ids)[:100],
        details={"count": len(body.ordered_ids)},
    )
    return Response(status_code=204)


@transactions.get("/next-day-order")
async def next_day_order(
    request: Request,
    _actor: RegisterActor,
    target_date: date = Query(..., alias="date"),
) -> dict:
    docs = await aggregate_list(_database(request).transactions, 
        [
            {"$match": register_day_clause(target_date)},
            {"$group": {"_id": None, "mx": {"$max": "$day_order"}}},
        ]
    , 1)
    mx = docs[0].get("mx") if docs else None
    if mx is None:
        return {"next": 0}
    try:
        return {"next": int(mx) + 1}
    except (TypeError, ValueError):
        return {"next": 0}


def _duplicate_check_query(
    *,
    days: int,
    truck_number: str = "",
    amount: float = 0.0,
    item: str = "",
    description: str = "",
    exclude_id: str | None = None,
) -> dict[str, Any]:
    cutoff = utcnow().replace(tzinfo=None) - timedelta(days=days)
    query: dict[str, Any] = {
        "date": {"$gte": cutoff},
        "rejected": {"$ne": True},
        "amount": amount,
    }
    if truck_number:
        query["truck_number"] = {
            "$regex": f"^{re.escape(truck_number)}$",
            "$options": "i",
        }
    if item:
        query["item"] = {"$regex": f"^{re.escape(item)}$", "$options": "i"}
    if description:
        query["description"] = {
            "$regex": f"^{re.escape(description)}$",
            "$options": "i",
        }
    if exclude_id:
        query["_id"] = {"$ne": _valid_id(exclude_id)}
    return query


def _ci_eq(actual: Any, expected: str) -> bool:
    """Case-insensitive exact match (same intent as anchored ``$regex`` / ``i``)."""
    return str(actual or "").strip().casefold() == expected.strip().casefold()


def _soft_dup_doc_matches(doc: dict[str, Any], *, candidate: Any) -> bool:
    """In-memory filter matching ``_duplicate_check_query`` field rules."""
    try:
        if float(doc.get("amount") or 0) != float(candidate.amount):
            return False
    except (TypeError, ValueError):
        return False
    exclude_id = getattr(candidate, "exclude_id", None)
    if exclude_id:
        try:
            if doc.get("_id") == _valid_id(exclude_id):
                return False
        except Exception:
            pass
    truck = (getattr(candidate, "truck_number", None) or "").strip()
    if truck and not _ci_eq(doc.get("truck_number"), truck):
        return False
    item = (getattr(candidate, "item", None) or "").strip()
    if item and not _ci_eq(doc.get("item"), item):
        return False
    description = (getattr(candidate, "description", None) or "").strip()
    if description and not _ci_eq(doc.get("description"), description):
        return False
    return True


# Soft-dup pool cap — advisory scan; keeps one find bounded for large days.
_SOFT_DUP_POOL_LIMIT = 5000


@transactions.post("/check-duplicates")
async def check_for_duplicates(
    body: DuplicateCheckRequest, request: Request, _actor: RegisterActor
) -> dict:
    query = _duplicate_check_query(
        days=body.days,
        truck_number=body.truck_number,
        amount=body.amount,
        item=body.item,
        description=body.description,
        exclude_id=body.exclude_id,
    )
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort("date", -1)
        .limit(10)
        .to_list(length=10)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@transactions.post("/check-duplicates/bulk")
async def check_for_duplicates_bulk(
    body: DuplicateCheckBulkRequest, request: Request, _actor: RegisterActor
) -> dict:
    """One RTT soft-duplicate scan for up to 100 register candidates.

    Fetches a single amount-filtered pool for the window, then matches each
    candidate in memory (same case-insensitive exact rules as the single check).
    """
    if not body.candidates:
        return {"results": [], "total": 0}

    db = _database(request)
    cutoff = utcnow().replace(tzinfo=None) - timedelta(days=body.days)
    amounts: list[float] = []
    seen_amounts: set[float] = set()
    for candidate in body.candidates:
        try:
            amount = float(candidate.amount)
        except (TypeError, ValueError):
            continue
        if amount not in seen_amounts:
            seen_amounts.add(amount)
            amounts.append(amount)

    pool: list[dict[str, Any]] = []
    if amounts:
        pool = (
            await db.transactions.find(
                {
                    "date": {"$gte": cutoff},
                    "rejected": {"$ne": True},
                    "amount": {"$in": amounts},
                }
            )
            .sort("date", -1)
            .to_list(length=_SOFT_DUP_POOL_LIMIT)
        )

    results: list[dict[str, Any]] = []
    for index, candidate in enumerate(body.candidates):
        hits: list[dict[str, Any]] = []
        for doc in pool:
            if not _soft_dup_doc_matches(doc, candidate=candidate):
                continue
            hits.append(doc)
            if len(hits) >= 10:
                break
        results.append(
            {"index": index, "items": json_safe(hits), "total": len(hits)}
        )
    return {"results": results, "total": len(results)}


async def _create_one_transaction_doc(
    *,
    db: Any,
    doc: dict[str, Any],
    actor: dict[str, Any],
    request: Request,
    record_event: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Insert or return existing by client_row_id. Returns (status, doc)."""
    client_row_id = doc.get("client_row_id")
    cashier_id = doc.get("cashier_id")
    if isinstance(client_row_id, str) and client_row_id and cashier_id is not None:
        existing = await db.transactions.find_one(
            {"cashier_id": cashier_id, "client_row_id": client_row_id}
        )
        if existing is not None:
            _log.info(
                "txn_idempotent_hit source=lookup cashier_id=%s client_row_id=%s",
                cashier_id,
                client_row_id,
            )
            return "already_exists", existing

    try:
        result = await db.transactions.insert_one(doc)
    except DuplicateKeyError:
        if isinstance(client_row_id, str) and client_row_id and cashier_id is not None:
            existing = await db.transactions.find_one(
                {"cashier_id": cashier_id, "client_row_id": client_row_id}
            )
            if existing is not None:
                _log.info(
                    "txn_idempotent_hit source=duplicate_key cashier_id=%s "
                    "client_row_id=%s",
                    cashier_id,
                    client_row_id,
                )
                return "already_exists", existing
        raise

    doc["_id"] = result.inserted_id
    if record_event:
        await record_operation_event(
            db,
            action="txn.create",
            actor=actor,
            request=request,
            entity_type="transaction",
            entity_ids=[str(doc["_id"])],
        )
    return "created", doc


def _client_row_key(doc: dict[str, Any]) -> tuple[Any, str] | None:
    client_row_id = doc.get("client_row_id")
    cashier_id = doc.get("cashier_id")
    if isinstance(client_row_id, str) and client_row_id and cashier_id is not None:
        return cashier_id, client_row_id
    return None


async def _prefetch_existing_by_client_row_id(
    db: Any, docs: list[dict[str, Any]]
) -> dict[tuple[Any, str], dict[str, Any]]:
    """One (or few) finds for existing ``(cashier_id, client_row_id)`` pairs."""
    by_cashier: dict[Any, set[str]] = {}
    for doc in docs:
        key = _client_row_key(doc)
        if key is None:
            continue
        cashier_id, client_row_id = key
        by_cashier.setdefault(cashier_id, set()).add(client_row_id)

    found: dict[tuple[Any, str], dict[str, Any]] = {}
    for cashier_id, row_ids in by_cashier.items():
        id_list = list(row_ids)
        cursor = db.transactions.find(
            {"cashier_id": cashier_id, "client_row_id": {"$in": id_list}}
        )
        existing = await cursor.to_list(length=len(id_list))
        for doc in existing:
            crid = doc.get("client_row_id")
            if isinstance(crid, str) and crid:
                found[(cashier_id, crid)] = doc
    return found


@transactions.post("/bulk-create")
async def bulk_create_transactions(
    body: TransactionBulkCreateRequest,
    request: Request,
    actor: RegisterActor,
) -> dict:
    """Create many register drafts in one request (per-item idempotent).

    Prefetches existing ``client_row_id``s, then ``insert_many`` for the rest.
    Duplicate-key races still map to ``already_exists`` (zero double-insert).
    """
    if not body.items:
        return {"results": [], "created": 0, "already_exists": 0, "errors": 0}

    db = _database(request)
    await _ensure_client_row_id_index(db)

    slots: list[dict[str, Any] | None] = [None] * len(body.items)
    prepared: list[tuple[int, dict[str, Any]]] = []

    for index, item in enumerate(body.items):
        try:
            prepared.append((index, _create_document(item, actor)))
        except Exception as exc:
            slots[index] = {
                "index": index,
                "status": "error",
                "transaction": None,
                "error": str(exc) or type(exc).__name__,
            }

    existing_map = await _prefetch_existing_by_client_row_id(
        db, [doc for _i, doc in prepared]
    )

    to_insert: list[tuple[int, dict[str, Any]]] = []
    for index, doc in prepared:
        key = _client_row_key(doc)
        if key is not None and key in existing_map:
            slots[index] = {
                "index": index,
                "status": "already_exists",
                "transaction": json_safe(existing_map[key]),
                "error": None,
            }
            continue
        doc.setdefault("_id", ObjectId())
        to_insert.append((index, doc))

    async def _resolve_duplicate(index: int, doc: dict[str, Any]) -> None:
        key = _client_row_key(doc)
        existing = existing_map.get(key) if key is not None else None
        if existing is None and key is not None:
            existing = await db.transactions.find_one(
                {"cashier_id": key[0], "client_row_id": key[1]}
            )
            if existing is not None:
                existing_map[key] = existing
        if existing is not None:
            slots[index] = {
                "index": index,
                "status": "already_exists",
                "transaction": json_safe(existing),
                "error": None,
            }
            return
        slots[index] = {
            "index": index,
            "status": "error",
            "transaction": None,
            "error": "duplicate_key_without_existing_row",
        }

    if to_insert:
        docs_only = [doc for _i, doc in to_insert]
        try:
            await db.transactions.insert_many(docs_only, ordered=False)
            for index, doc in to_insert:
                slots[index] = {
                    "index": index,
                    "status": "created",
                    "transaction": json_safe(doc),
                    "error": None,
                }
        except BulkWriteError as exc:
            details = exc.details or {}
            err_by_index = {
                int(err["index"]): err
                for err in (details.get("writeErrors") or [])
                if err.get("index") is not None
            }
            for batch_i, (index, doc) in enumerate(to_insert):
                err = err_by_index.get(batch_i)
                if err is None:
                    slots[index] = {
                        "index": index,
                        "status": "created",
                        "transaction": json_safe(doc),
                        "error": None,
                    }
                    continue
                if int(err.get("code") or 0) == 11000:
                    await _resolve_duplicate(index, doc)
                    continue
                slots[index] = {
                    "index": index,
                    "status": "error",
                    "transaction": None,
                    "error": str(err.get("errmsg") or err) or "bulk_write_error",
                }
        except Exception as exc:
            for index, _doc in to_insert:
                if slots[index] is not None:
                    continue
                slots[index] = {
                    "index": index,
                    "status": "error",
                    "transaction": None,
                    "error": str(exc) or type(exc).__name__,
                }

    results = [
        slot
        if slot is not None
        else {
            "index": i,
            "status": "error",
            "transaction": None,
            "error": "not processed",
        }
        for i, slot in enumerate(slots)
    ]
    n_created = sum(1 for r in results if r["status"] == "created")
    n_exists = sum(1 for r in results if r["status"] == "already_exists")
    n_errors = sum(1 for r in results if r["status"] == "error")
    created_ids = [
        str(r["transaction"]["_id"])
        for r in results
        if r["status"] == "created"
        and isinstance(r.get("transaction"), dict)
        and r["transaction"].get("_id")
    ]

    if created_ids:
        await record_operation_event(
            db,
            action="txn.bulk_create",
            actor=actor,
            request=request,
            entity_type="transaction",
            entity_ids=created_ids,
        )

    _log.info(
        "txn_bulk_create created=%s already_exists=%s errors=%s items=%s "
        "cashier_id=%s",
        n_created,
        n_exists,
        n_errors,
        len(body.items),
        actor.get("_id"),
    )

    return {
        "results": results,
        "created": n_created,
        "already_exists": n_exists,
        "errors": n_errors,
    }


@transactions.get("/trash")
async def list_cashier_trash(
    request: Request,
    actor: RegisterActor,
    search: str = "",
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict:
    """Soft-deleted rows owned by the cashier (own soft-trash + accountant-confirmed)."""
    db = _database(request)
    query: dict[str, Any] = {
        "trashed": True,
        "cashier_id": actor.get("_id"),
    }
    if search.strip():
        esc = re.escape(search.strip())
        query["$or"] = [
            {"description": {"$regex": esc, "$options": "i"}},
            {"item": {"$regex": esc, "$options": "i"}},
            {"truck_number": {"$regex": esc, "$options": "i"}},
            {"category_name": {"$regex": esc, "$options": "i"}},
        ]
    docs = (
        await db.transactions.find(query)
        .sort([("trashed_at", -1), ("date", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    total = await db.transactions.count_documents(query)
    return {"items": json_safe(docs), "total": int(total)}


@transactions.get("/trash/count")
async def count_cashier_trash(request: Request, actor: RegisterActor) -> dict:
    total = await _database(request).transactions.count_documents(
        {"trashed": True, "cashier_id": actor.get("_id")}
    )
    return {"count": int(total)}


def _cashier_trash_ids(body: BulkIdsRequest) -> list:
    return [_valid_id(str(raw).strip()) for raw in body.ids if str(raw).strip()]


@transactions.post("/trash/bulk-request-restore")
async def bulk_request_restore_from_trash(
    body: BulkIdsRequest,
    request: Request,
    actor: RegisterActor,
) -> dict:
    """Request accountant restore — verified-path trash only."""
    ids = _cashier_trash_ids(body)
    if not ids:
        return {"updated": 0}
    cashier_id = actor.get("_id")
    now = utcnow().replace(tzinfo=None)
    result = await _database(request).transactions.update_many(
        {
            "_id": {"$in": ids},
            "trashed": True,
            "verified": True,
            "cashier_id": cashier_id,
            "restore_requested": {"$ne": True},
        },
        {
            "$set": {
                "restore_requested": True,
                "restore_requested_at": now,
                "restore_requested_by": cashier_id,
            }
        },
    )
    n = int(result.modified_count)
    if n:
        await record_operation_event(
            _database(request),
            action="txn.request_restore_bulk",
            actor=actor,
            request=request,
            entity_type="transaction",
            entity_ids=[str(oid) for oid in ids],
            details={"count": n},
        )
    return {"updated": n}


@transactions.post("/trash/bulk-restore-own")
async def bulk_restore_own_unverified_trash(
    body: BulkIdsRequest,
    request: Request,
    actor: RegisterActor,
) -> dict:
    """Cashier immediately restores own draft/submitted soft-trashed rows."""
    ids = _cashier_trash_ids(body)
    if not ids:
        return {"updated": 0}
    cashier_id = actor.get("_id")
    result = await _database(request).transactions.update_many(
        {
            "_id": {"$in": ids},
            "trashed": True,
            "verified": {"$ne": True},
            "cashier_id": cashier_id,
        },
        {
            "$set": {
                "trashed": False,
                "trashed_at": None,
                "trashed_by": None,
                "restore_requested": False,
                "restore_requested_at": None,
                "restore_requested_by": None,
            }
        },
    )
    n = int(result.modified_count)
    if n:
        await record_operation_event(
            _database(request),
            action="txn.restore_own_trash_bulk",
            actor=actor,
            request=request,
            entity_type="transaction",
            entity_ids=[str(oid) for oid in ids],
            details={"count": n},
        )
    return {"updated": n}


@transactions.post("/trash/bulk-purge-own")
async def bulk_purge_own_unverified_trash(
    body: BulkIdsRequest,
    request: Request,
    actor: RegisterActor,
) -> dict:
    """Cashier permanently deletes own draft/submitted soft-trashed rows."""
    ids = _cashier_trash_ids(body)
    if not ids:
        return {"deleted": 0}
    cashier_id = actor.get("_id")
    db = _database(request)
    result = await db.transactions.delete_many(
        {
            "_id": {"$in": ids},
            "trashed": True,
            "verified": {"$ne": True},
            "cashier_id": cashier_id,
        }
    )
    n = int(result.deleted_count)
    if n:
        await record_operation_event(
            db,
            action="txn.purge_own_trash_bulk",
            actor=actor,
            request=request,
            entity_type="transaction",
            entity_ids=[str(oid) for oid in ids],
            details={"count": n},
        )
    return {"deleted": n}


def _normalize_description_key(value: str) -> str:
    return " ".join((value or "").strip().upper().split())


@transactions.post("/bulk-patch")
async def bulk_patch_transactions(
    body: BulkTransactionPatchRequest,
    request: Request,
    actor: RegisterActor,
) -> dict:
    """Apply many in-place register edits in one ``bulk_write`` + one audit.

    Used by Daily Register Save for dirty draft / unverified rows. Verified
    pending-edit clones stay on the per-row pending-edit path.
    """
    ops: list[UpdateOne] = []
    ids: list[ObjectId] = []
    for item in body.items:
        oid = _valid_id(item.id)
        values = _coerce_write_fields(dict(item.values or {}))
        if not values:
            continue
        ops.append(UpdateOne({"_id": oid}, {"$set": values}))
        ids.append(oid)
    if not ops:
        return {"updated": 0, "matched": 0, "requested": len(body.items)}

    db = _database(request)
    result = await db.transactions.bulk_write(ops, ordered=False)
    updated = int(getattr(result, "modified_count", 0) or 0)
    matched = int(getattr(result, "matched_count", 0) or 0)
    await record_operation_event(
        db,
        action="txn.bulk_update",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(oid) for oid in ids[:40]],
        details={
            "updated": updated,
            "matched": matched,
            "requested": len(body.items),
        },
    )
    return {"updated": updated, "matched": matched, "requested": len(body.items)}


@transactions.post("/apply-description-mapping")
async def apply_description_mapping(
    body: ApplyDescriptionMappingRequest,
    request: Request,
    actor: RegisterActor,
) -> dict:
    """Stamp Payee + Item onto matching open rows in one ``update_many``.

    Cashiers (and accountants using the register) share this path. Cashiers are
    always scoped to their own rows; accountants/admins may stamp any open rows
    or optionally narrow with ``cashier_id``.
    """
    keys = {
        _normalize_description_key(raw)
        for raw in body.description_keys
        if _normalize_description_key(raw)
    }
    if not keys:
        raise ApiError(422, "empty_keys", "At least one description key is required")

    clear = bool(body.clear)
    if clear:
        category_id = None
        payee = ""
        item_name = ""
    else:
        category_id = _valid_id(body.category_id)
        payee = " ".join((body.payee or "").strip().upper().split())
        item_name = " ".join((body.category_name or "").strip().split())
        if not item_name:
            raise ApiError(422, "empty_item", "Item / category name is required")

    parsed_dates: list[date] = []
    for raw in body.dates or []:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            parsed_dates.append(date.fromisoformat(text[:10]))
        except ValueError as exc:
            raise ApiError(422, "invalid_date", f"Invalid date: {raw}") from exc

    role = str(actor.get("role") or "").lower()
    actor_id = actor.get("_id")
    scope_cashier: ObjectId | None = None
    if role == "cashier":
        if not isinstance(actor_id, ObjectId):
            try:
                actor_id = object_id(str(actor_id))
            except ValueError as exc:
                raise ApiError(500, "invalid_actor", "Authenticated user id is invalid") from exc
        scope_cashier = actor_id
    elif body.cashier_id:
        scope_cashier = _valid_id(body.cashier_id)

    query: dict[str, Any] = dict(OPEN_REGISTER_MATCH)
    if scope_cashier is not None:
        query["cashier_id"] = scope_cashier
    if parsed_dates:
        if len(parsed_dates) == 1:
            query = {"$and": [query, register_day_clause(parsed_dates[0])]}
        else:
            query = {
                "$and": [
                    query,
                    {"$or": [register_day_clause(day) for day in parsed_dates]},
                ]
            }

    db = _database(request)
    candidates = await db.transactions.find(
        query, {"_id": 1, "description": 1}
    ).to_list(length=50_000)
    ids = [
        doc["_id"]
        for doc in candidates
        if _normalize_description_key(str(doc.get("description") or "")) in keys
    ]
    if not ids:
        return {"updated": 0, "matched": 0}

    result = await db.transactions.update_many(
        {"_id": {"$in": ids}},
        {
            "$set": {
                "payee": payee,
                "item": item_name,
                "category_name": item_name,
                "category_id": category_id,
            }
        },
    )
    updated = int(result.modified_count)
    matched = int(result.matched_count)
    await record_operation_event(
        db,
        action="txn.bulk_mapping_clear" if clear else "txn.bulk_mapping_apply",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(oid) for oid in ids[:40]],
        details={
            "updated": updated,
            "matched": matched,
            "description_keys": sorted(keys)[:40],
            "client_job_id": body.client_job_id,
            "payee": payee,
            "item": item_name,
            "clear": clear,
            "dates": [day.isoformat() for day in parsed_dates][:40],
        },
    )
    return {"updated": updated, "matched": matched}


@transactions.patch("/{tx_id}")
async def patch_transaction(
    tx_id: str, body: TransactionPatch, request: Request, actor: RegisterActor
) -> dict:
    oid = _valid_id(tx_id)
    values = _coerce_write_fields(dict(body.values))
    if not values:
        raise ApiError(422, "empty_patch", "No fields to update")
    db = _database(request)
    result = await db.transactions.update_one({"_id": oid}, {"$set": values})
    if result.matched_count == 0:
        raise ApiError(404, "not_found", "Transaction not found")
    doc = await db.transactions.find_one({"_id": oid})
    await record_operation_event(
        db,
        action="txn.update",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(oid)],
        details={"fields": sorted(values.keys())[:40]},
    )
    return json_safe(doc)


@transactions.delete("/{tx_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    tx_id: str, request: Request, actor: RegisterActor
) -> Response:
    oid = _valid_id(tx_id)
    db = _database(request)
    result = await db.transactions.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise ApiError(404, "not_found", "Transaction not found")
    await record_operation_event(
        db,
        action="txn.delete",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(oid)],
    )
    return Response(status_code=204)


@transactions.post("/{tx_id}/request-delete")
async def request_or_delete_transaction(
    tx_id: str,
    request: Request,
    actor: RegisterActor,
    body: RequestDeleteBody | None = None,
) -> dict:
    oid = _valid_id(tx_id)
    db = _database(request)
    doc = await db.transactions.find_one({"_id": oid})
    if not doc:
        return {"result": "not_found"}

    # Cashiers may only delete / trash their own rows (Merged view safety).
    # Accountants and admins may act on any cashier's register row.
    role = str(actor.get("role") or "").lower()
    actor_id = actor.get("_id")
    owner_id = doc.get("cashier_id")
    if role == "cashier" and owner_id is not None and actor_id is not None:
        if str(owner_id) != str(actor_id):
            raise ApiError(
                403,
                "forbidden",
                "You can only delete your own register entries.",
            )

    # Pending-edit clones are disposable — hard-delete the clone only.
    is_pending_edit = bool(doc.get("original_transaction_id")) and not doc.get("verified")
    if is_pending_edit:
        await db.transactions.delete_one({"_id": oid})
        await record_operation_event(
            db,
            action="txn.delete",
            actor=actor,
            request=request,
            entity_type="transaction",
            entity_ids=[str(oid)],
            details={"via": "request_delete", "pending_edit_clone": True},
        )
        return {"result": "deleted"}

    # Attribute trash / deletion request to the acting user (not a spoofed body id).
    if body and body.cashier_id and role in ("admin", "accountant"):
        actor_id = _valid_id(body.cashier_id) or actor_id
    actor_id = actor_id or doc.get("cashier_id")
    now = utcnow().replace(tzinfo=None)

    # Draft / submitted but unverified → Cashier Trash (soft), no accountant gate.
    if not doc.get("verified"):
        if doc.get("trashed"):
            return {"result": "already_trashed"}
        await db.transactions.update_one(
            {"_id": oid},
            {
                "$set": {
                    "trashed": True,
                    "trashed_at": now,
                    "trashed_by": actor_id,
                    "deletion_requested": False,
                    "deletion_requested_at": None,
                    "deletion_requested_by": None,
                    "restore_requested": False,
                    "restore_requested_at": None,
                    "restore_requested_by": None,
                }
            },
        )
        await record_operation_event(
            db,
            action="txn.trash",
            actor=actor,
            request=request,
            entity_type="transaction",
            entity_ids=[str(oid)],
            details={
                "via": "request_delete",
                "unverified": True,
                "register_status": doc.get("register_status") or "draft",
            },
        )
        return {"result": "trashed"}

    # Verified → accountant must confirm (Verify → Deleted), then Trash.
    await db.transactions.update_one(
        {"_id": oid},
        {
            "$set": {
                "deletion_requested": True,
                "deletion_requested_at": now,
                "deletion_requested_by": actor_id,
            }
        },
    )
    await record_operation_event(
        db,
        action="txn.request_delete",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(oid)],
    )
    return {"result": "deletion_requested"}


@transactions.post("/{tx_id}/request-restore")
async def request_restore_from_trash(
    tx_id: str,
    request: Request,
    actor: RegisterActor,
    body: RequestDeleteBody | None = None,
) -> dict:
    """Cashier asks accountant to restore a trashed expense."""
    oid = _valid_id(tx_id)
    db = _database(request)
    cashier_id = actor.get("_id")
    if body and body.cashier_id:
        cashier_id = _valid_id(body.cashier_id)
    doc = await db.transactions.find_one(
        {
            "_id": oid,
            "trashed": True,
            "verified": True,
            "cashier_id": cashier_id,
        }
    )
    if not doc:
        return {"result": "not_found"}
    if doc.get("restore_requested"):
        return {"result": "already_requested"}
    await db.transactions.update_one(
        {
            "_id": oid,
            "trashed": True,
            "verified": True,
            "cashier_id": cashier_id,
        },
        {
            "$set": {
                "restore_requested": True,
                "restore_requested_at": utcnow().replace(tzinfo=None),
                "restore_requested_by": cashier_id,
            }
        },
    )
    await record_operation_event(
        db,
        action="txn.request_restore",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(oid)],
    )
    return {"result": "restore_requested"}


@transactions.post("/{tx_id}/cancel-restore")
async def cancel_restore_request(
    tx_id: str,
    request: Request,
    actor: RegisterActor,
) -> dict:
    """Cashier withdraws a pending restore request (row stays in trash)."""
    oid = _valid_id(tx_id)
    result = await _database(request).transactions.update_one(
        {
            "_id": oid,
            "trashed": True,
            "cashier_id": actor.get("_id"),
            "restore_requested": True,
        },
        {
            "$set": {
                "restore_requested": False,
                "restore_requested_at": None,
                "restore_requested_by": None,
            }
        },
    )
    ok = result.modified_count == 1
    if ok:
        await record_operation_event(
            _database(request),
            action="txn.cancel_restore",
            actor=actor,
            request=request,
            entity_type="transaction",
            entity_ids=[str(oid)],
        )
    return {"ok": ok}


@transactions.post("/{tx_id}/pending-edit", status_code=201)
async def insert_pending_edit(
    tx_id: str,
    body: PendingEditRequest,
    request: Request,
    actor: RegisterActor,
) -> dict:
    original_id = _valid_id(tx_id)
    db = _database(request)
    original_doc = await db.transactions.find_one({"_id": original_id})
    if not original_doc:
        raise ApiError(404, "not_found", "Original transaction not found")

    cashier_id = (
        _valid_id(body.cashier_id) if body.cashier_id else actor.get("_id")
    )
    now = utcnow().replace(tzinfo=None)
    meta = {
        "original_transaction_id": original_id,
        "edited_after_verification": True,
        "verified": False,
        "verified_by": None,
        "verified_at": None,
        "rejection_reason": None,
        "rejected": False,
        "discarded": False,
        "last_edited_at": now,
        "last_edited_by": cashier_id,
    }
    updates = _coerce_write_fields(dict(body.updates))

    existing = await db.transactions.find_one(
        {
            "original_transaction_id": original_id,
            "verified": False,
            "edited_after_verification": True,
            "rejected": {"$ne": True},
        }
    )
    if existing:
        patch = dict(updates)
        patch.update(meta)
        await db.transactions.update_one({"_id": existing["_id"]}, {"$set": patch})
        doc = await db.transactions.find_one({"_id": existing["_id"]})
        await record_operation_event(
            db,
            action="txn.pending_edit",
            actor=actor,
            request=request,
            entity_type="transaction",
            entity_ids=[str(existing["_id"]), str(original_id)],
            details={"mode": "update"},
        )
        return json_safe(doc)

    pending = dict(original_doc)
    pending.pop("_id", None)
    pending.update(updates)
    pending.update(meta)
    # Clones must not reuse register/import idempotency keys — unique indexes
    # on those fields would reject the insert (409 "already exists").
    for key in (
        "client_row_id",
        "daily_import_id",
        "import_row_key",
        "master_import_source",
        "master_serial",
    ):
        pending.pop(key, None)
    pending["created_at"] = now
    result = await db.transactions.insert_one(pending)
    pending["_id"] = result.inserted_id
    await record_operation_event(
        db,
        action="txn.pending_edit",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(pending["_id"]), str(original_id)],
        details={"mode": "insert"},
    )
    return json_safe(pending)
