"""Accountant deletion-request HTTP routes (Phase 3.4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from .accountant_ops import (
    DELETION_BASE_MATCH,
    DELETION_SORT,
    build_deletion_query,
    deletion_descriptions_query,
    deletion_items_query,
    format_available_dates,
)
from .dependencies import require_roles
from .errors import ApiError
from .schemas import BulkIdsRequest
from .security import utcnow
from .serialization import json_safe, object_id

deletions = APIRouter(tags=["accountant"])

Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _valid_id(value: str) -> ObjectId:
    try:
        return object_id(value)
    except ValueError:
        raise ApiError(422, "invalid_id", "Resource id is invalid") from None


def _parse_ids(raw_ids: list[str]) -> list[ObjectId]:
    return [_valid_id(str(raw).strip()) for raw in raw_ids if str(raw).strip()]


def _parse_optional_oid(value: str | None) -> ObjectId | None:
    if value is None or not str(value).strip():
        return None
    try:
        return object_id(str(value).strip())
    except ValueError:
        raise ApiError(422, "invalid_id", "cashier_id is invalid") from None


def _parse_optional_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _actor_oid(user: dict[str, Any]) -> ObjectId:
    value = user.get("_id")
    if isinstance(value, ObjectId):
        return value
    try:
        return object_id(str(value))
    except ValueError:
        raise ApiError(500, "invalid_actor", "Authenticated user id is invalid") from None


def _deletion_filters(
    *,
    search: str,
    truck: str,
    cashier_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    item: str,
    description: str,
) -> dict[str, Any]:
    return build_deletion_query(
        search=search,
        truck=truck,
        cashier_id=_parse_optional_oid(cashier_id),
        date_from=_parse_optional_dt(date_from),
        date_to=_parse_optional_dt(date_to),
        item=item,
        description=description,
    )


async def _audit(
    request: Request,
    *,
    action: str,
    actor: dict[str, Any],
    entity_ids: list[ObjectId],
    details: dict[str, Any] | None = None,
) -> None:
    try:
        await _database(request).operation_events.insert_one(
            {
                "ts": utcnow(),
                "action": action,
                "actor_id": str(actor.get("_id") or ""),
                "actor_username": actor.get("username"),
                "actor_role": actor.get("role"),
                "entity_type": "transaction",
                "entity_ids": [str(oid) for oid in entity_ids],
                "outcome": "ok",
                "error": None,
                "request_id": getattr(request.state, "request_id", None)
                or uuid.uuid4().hex,
                "client": "api",
                "details": details or {},
            }
        )
    except Exception:
        pass


@deletions.get("/deletions")
async def list_deletions(
    request: Request,
    _manager: Manager,
    search: str = "",
    truck: str = "",
    cashier_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    item: str = "",
    description: str = "",
    limit: int = Query(25, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict:
    query = _deletion_filters(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
    )
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort(list(DELETION_SORT))
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@deletions.get("/deletions/count")
async def count_deletions(
    request: Request,
    _manager: Manager,
    search: str = "",
    truck: str = "",
    cashier_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    item: str = "",
    description: str = "",
) -> dict:
    query = _deletion_filters(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
    )
    total = await _database(request).transactions.count_documents(query)
    return {"count": int(total)}


@deletions.get("/counts/deletions")
async def deletions_badge_count(request: Request, _manager: Manager) -> dict:
    total = await _database(request).transactions.count_documents(DELETION_BASE_MATCH)
    return {"count": int(total)}


@deletions.get("/deletions/meta/dates")
async def deletion_dates(request: Request, _manager: Manager) -> dict:
    vals = await _database(request).transactions.distinct("date", DELETION_BASE_MATCH)
    return {"items": format_available_dates(vals)}


@deletions.get("/deletions/meta/trucks")
async def deletion_trucks(request: Request, _manager: Manager) -> dict:
    vals = await _database(request).transactions.distinct(
        "truck_number", DELETION_BASE_MATCH
    )
    return {"items": sorted(v for v in vals if v)}


@deletions.get("/deletions/meta/items")
async def deletion_items(
    request: Request,
    _manager: Manager,
    descriptions: str = "",
) -> dict:
    desc_list = [p.strip() for p in descriptions.split(",") if p.strip()]
    base = deletion_items_query(desc_list or None)
    db = _database(request)
    items = await db.transactions.distinct("item", base)
    cats = await db.transactions.distinct("category_name", base)
    names = {*(v for v in items if v), *(v for v in cats if v)}
    return {"items": sorted(names)}


@deletions.get("/deletions/meta/descriptions")
async def deletion_descriptions(
    request: Request,
    _manager: Manager,
    items: str = "",
) -> dict:
    item_list = [p.strip() for p in items.split(",") if p.strip()]
    base = deletion_descriptions_query(item_list or None)
    vals = await _database(request).transactions.distinct("description", base)
    return {"items": sorted(v for v in vals if v)}


@deletions.get("/deletions/meta/cashiers")
async def deletion_cashier_ids(request: Request, _manager: Manager) -> dict:
    vals = await _database(request).transactions.distinct(
        "cashier_id", DELETION_BASE_MATCH
    )
    return {"items": json_safe([v for v in vals if v is not None])}


@deletions.post("/deletions/bulk-confirm")
async def bulk_confirm_deletions(
    body: BulkIdsRequest, request: Request, manager: Manager
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    db = _database(request)
    actor_id = _actor_oid(manager)
    now = utcnow().replace(tzinfo=None)
    await db.transactions.delete_many({"original_transaction_id": {"$in": ids}})
    result = await db.transactions.update_many(
        {
            "_id": {"$in": ids},
            "deletion_requested": True,
            "trashed": {"$ne": True},
        },
        {
            "$set": {
                "trashed": True,
                "trashed_at": now,
                "trashed_by": actor_id,
                "deletion_requested": False,
                "deletion_requested_at": None,
                "deletion_requested_by": None,
            }
        },
    )
    n = int(result.modified_count)
    if n:
        await _audit(
            request,
            action="txn.trash_bulk",
            actor=manager,
            entity_ids=ids,
            details={"count": n},
        )
    return {"updated": n}


@deletions.post("/deletions/bulk-restore")
async def bulk_restore_deletions(
    body: BulkIdsRequest, request: Request, _manager: Manager
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    result = await _database(request).transactions.update_many(
        {
            "_id": {"$in": ids},
            "deletion_requested": True,
            "trashed": {"$ne": True},
        },
        {
            "$set": {
                "deletion_requested": False,
                "deletion_requested_at": None,
                "deletion_requested_by": None,
            }
        },
    )
    return {"updated": int(result.modified_count)}


@deletions.post("/deletions/{tx_id}/confirm")
async def confirm_deletion(
    tx_id: str, request: Request, manager: Manager
) -> dict:
    oid = _valid_id(tx_id)
    db = _database(request)
    doc = await db.transactions.find_one(
        {
            "_id": oid,
            "deletion_requested": True,
            "trashed": {"$ne": True},
        }
    )
    if not doc:
        return {"ok": False}
    actor_id = _actor_oid(manager)
    now = utcnow().replace(tzinfo=None)
    await db.transactions.delete_many({"original_transaction_id": oid})
    result = await db.transactions.update_one(
        {
            "_id": oid,
            "deletion_requested": True,
            "trashed": {"$ne": True},
        },
        {
            "$set": {
                "trashed": True,
                "trashed_at": now,
                "trashed_by": actor_id,
                "deletion_requested": False,
                "deletion_requested_at": None,
                "deletion_requested_by": None,
            }
        },
    )
    ok = result.modified_count == 1
    if ok:
        await _audit(
            request,
            action="txn.trash",
            actor=manager,
            entity_ids=[oid],
        )
    return {"ok": ok}


@deletions.post("/deletions/{tx_id}/restore")
async def restore_deletion(tx_id: str, request: Request, _manager: Manager) -> dict:
    oid = _valid_id(tx_id)
    result = await _database(request).transactions.update_one(
        {
            "_id": oid,
            "deletion_requested": True,
            "trashed": {"$ne": True},
        },
        {
            "$set": {
                "deletion_requested": False,
                "deletion_requested_at": None,
                "deletion_requested_by": None,
            }
        },
    )
    return {"ok": result.modified_count == 1}
