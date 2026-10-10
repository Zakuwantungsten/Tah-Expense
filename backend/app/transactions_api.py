"""Cashier transaction HTTP routes (Phase 2)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request, Response, status
from pymongo import UpdateOne

from .audit_ops import record_operation_event
from .dependencies import Authenticated, require_roles
from .errors import ApiError
from .schemas import (
    DayOrderRequest,
    DuplicateCheckRequest,
    PendingEditRequest,
    RequestDeleteBody,
    ResubmitRejectedRequest,
    SubmitDayRequest,
    TransactionCreate,
    TransactionIdsRequest,
    TransactionPatch,
)
from .security import utcnow
from .serialization import json_safe, object_id
from .attachments_api import attachments as attachments_router
from .mongo_async import aggregate_list
from .transaction_ops import (
    DRAFT_BASE_MATCH,
    REGISTER_DAY_EXPR,
    browse_match,
    filter_pending_edit_clones,
    register_day_clause,
    register_day_range_clause,
)

transactions = APIRouter(prefix="/transactions", tags=["transactions"])
transactions.include_router(attachments_router)

_REGISTER_ROLES = ("admin", "cashier", "accountant")
RegisterActor = Annotated[dict[str, Any], Depends(require_roles(*_REGISTER_ROLES))]

_OBJECT_ID_FIELDS = {
    "category_id",
    "cashier_id",
    "verified_by",
    "last_edited_by",
    "deletion_requested_by",
    "trashed_by",
    "original_transaction_id",
}
_DATETIME_FIELDS = {
    "date",
    "reported_date",
    "verified_at",
    "last_edited_at",
    "deletion_requested_at",
    "trashed_at",
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
    for key in ("daily_import_id", "daily_import_source", "import_primary_date", "amount_usd"):
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
    doc.setdefault("register_status", "draft")
    doc.setdefault("attachments", [])
    return doc


@transactions.post("", status_code=201)
async def create_transaction(
    body: TransactionCreate, request: Request, actor: RegisterActor
) -> dict:
    doc = _create_document(body, actor)
    db = _database(request)
    result = await db.transactions.insert_one(doc)
    doc["_id"] = result.inserted_id
    await record_operation_event(
        db,
        action="txn.create",
        actor=actor,
        request=request,
        entity_type="transaction",
        entity_ids=[str(doc["_id"])],
    )
    return json_safe(doc)


@transactions.get("")
async def list_transactions_by_ids(
    request: Request,
    _user: Authenticated,
    ids: str = Query("", description="Comma-separated ObjectId hex strings"),
) -> dict:
    raw_ids = [part.strip() for part in ids.split(",") if part.strip()]
    if not raw_ids:
        return {"items": [], "total": 0}
    if len(raw_ids) > 200:
        raise ApiError(422, "too_many_ids", "At most 200 ids are allowed")
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
async def overview_stats(request: Request, _actor: RegisterActor) -> dict:
    db = _database(request)
    today = date.today()
    today_start = datetime(today.year, today.month, today.day)
    today_end = datetime(today.year, today.month, today.day, 23, 59, 59)
    month_start = datetime(today.year, today.month, 1)

    def _money_group(match: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"$match": match},
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
    recent_docs = (
        await db.transactions.find({})
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
                "entries_count": doc["entries_count"],
                "total_tzs": doc["total_tzs"],
                "total_refund": doc["total_refund"],
                "cashier_ids": cashier_ids,
                "draft_count": int(doc.get("draft_count") or 0),
                "submitted_count": int(doc.get("submitted_count") or 0),
            }
        )
    return json_safe({"items": items, "total": len(items)})


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
    docs = (
        await _database(request)
        .transactions.find(match)
        .sort([("date", -1), ("created_at", -1)])
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


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


@transactions.get("/meta/months")
async def available_months(request: Request, _actor: RegisterActor) -> dict:
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
    docs = await aggregate_list(_database(request).transactions, pipeline, length=None)
    items = [
        {"year": int(doc["_id"]["year"]), "month": int(doc["_id"]["month"])}
        for doc in docs
        if doc.get("_id")
    ]
    return {"items": items, "total": len(items)}


@transactions.get("/meta/years")
async def available_years(request: Request, _actor: RegisterActor) -> dict:
    pipeline = [
        {"$group": {"_id": {"$year": "$date"}}},
        {"$sort": {"_id": -1}},
    ]
    docs = await aggregate_list(_database(request).transactions, pipeline, length=40)
    years = [int(doc["_id"]) for doc in docs if doc.get("_id") is not None]
    today_y = date.today().year
    for year in (today_y + 1, today_y, today_y - 1):
        if year not in years:
            years.append(year)
    years = sorted(set(years), reverse=True)
    return {"items": years, "total": len(years)}


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


@transactions.post("/check-duplicates")
async def check_for_duplicates(
    body: DuplicateCheckRequest, request: Request, _actor: RegisterActor
) -> dict:
    cutoff = utcnow().replace(tzinfo=None) - timedelta(days=body.days)
    query: dict[str, Any] = {
        "date": {"$gte": cutoff},
        "rejected": {"$ne": True},
        "amount": body.amount,
    }
    if body.truck_number:
        query["truck_number"] = {
            "$regex": f"^{re.escape(body.truck_number)}$",
            "$options": "i",
        }
    if body.item:
        query["item"] = {"$regex": f"^{re.escape(body.item)}$", "$options": "i"}
    if body.description:
        query["description"] = {
            "$regex": f"^{re.escape(body.description)}$",
            "$options": "i",
        }
    if body.exclude_id:
        query["_id"] = {"$ne": _valid_id(body.exclude_id)}
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort("date", -1)
        .limit(10)
        .to_list(length=10)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@transactions.get("/{tx_id}")
async def get_transaction(
    tx_id: str, request: Request, _user: Authenticated
) -> dict:
    doc = await _database(request).transactions.find_one({"_id": _valid_id(tx_id)})
    if not doc:
        raise ApiError(404, "not_found", "Transaction not found")
    return json_safe(doc)


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

    is_pending_edit = bool(doc.get("original_transaction_id")) and not doc.get("verified")
    if is_pending_edit or not doc.get("verified"):
        await db.transactions.delete_one({"_id": oid})
        await record_operation_event(
            db,
            action="txn.delete",
            actor=actor,
            request=request,
            entity_type="transaction",
            entity_ids=[str(oid)],
            details={"via": "request_delete", "pending_or_unverified": True},
        )
        return {"result": "deleted"}

    cashier_id = None
    if body and body.cashier_id:
        cashier_id = _valid_id(body.cashier_id)
    await db.transactions.update_one(
        {"_id": oid},
        {
            "$set": {
                "deletion_requested": True,
                "deletion_requested_at": utcnow().replace(tzinfo=None),
                "deletion_requested_by": cashier_id
                or actor.get("_id")
                or doc.get("cashier_id"),
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
