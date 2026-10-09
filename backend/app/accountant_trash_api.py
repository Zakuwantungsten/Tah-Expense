"""Accountant trash + verified lookup HTTP routes (Phase 3.5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from .accountant_ops import (
    TRASH_SORT,
    VERIFIED_SORT,
    build_trash_query,
    build_verified_query,
)
from .dependencies import require_roles
from .errors import ApiError
from .schemas import BulkIdsRequest
from .security import utcnow
from .serialization import json_safe, object_id

trash = APIRouter(tags=["accountant"])

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


def _trash_filters(
    *,
    search: str,
    truck: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> dict[str, Any]:
    return build_trash_query(
        search=search,
        truck=truck,
        date_from=_parse_optional_dt(date_from),
        date_to=_parse_optional_dt(date_to),
    )


@trash.get("/trash")
async def list_trash(
    request: Request,
    _manager: Manager,
    search: str = "",
    truck: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict:
    query = _trash_filters(
        search=search, truck=truck, date_from=date_from, date_to=date_to
    )
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort(list(TRASH_SORT))
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@trash.get("/trash/count")
async def count_trash(
    request: Request,
    _manager: Manager,
    search: str = "",
    truck: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    query = _trash_filters(
        search=search, truck=truck, date_from=date_from, date_to=date_to
    )
    total = await _database(request).transactions.count_documents(query)
    return {"count": int(total)}


@trash.get("/counts/trash")
async def trash_badge_count(request: Request, _manager: Manager) -> dict:
    total = await _database(request).transactions.count_documents({"trashed": True})
    return {"count": int(total)}


@trash.post("/trash/bulk-restore")
async def bulk_restore_from_trash(
    body: BulkIdsRequest, request: Request, _manager: Manager
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    result = await _database(request).transactions.update_many(
        {"_id": {"$in": ids}, "trashed": True},
        {"$set": {"trashed": False, "trashed_at": None, "trashed_by": None}},
    )
    return {"updated": int(result.modified_count)}


@trash.post("/trash/bulk-delete")
async def bulk_permanently_delete_trashed(
    body: BulkIdsRequest, request: Request, manager: Manager
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"deleted": 0}
    result = await _database(request).transactions.delete_many(
        {"_id": {"$in": ids}, "trashed": True}
    )
    n = int(result.deleted_count)
    if n:
        await _audit(
            request,
            action="txn.trash_purge_bulk",
            actor=manager,
            entity_ids=ids,
            details={"count": n},
        )
    return {"deleted": n}


@trash.post("/trash/{tx_id}/restore")
async def restore_from_trash(tx_id: str, request: Request, _manager: Manager) -> dict:
    oid = _valid_id(tx_id)
    result = await _database(request).transactions.update_one(
        {"_id": oid, "trashed": True},
        {"$set": {"trashed": False, "trashed_at": None, "trashed_by": None}},
    )
    return {"ok": result.modified_count == 1}


@trash.delete("/trash/{tx_id}")
async def permanently_delete_trashed(
    tx_id: str, request: Request, manager: Manager
) -> dict:
    oid = _valid_id(tx_id)
    result = await _database(request).transactions.delete_one(
        {"_id": oid, "trashed": True}
    )
    ok = result.deleted_count == 1
    if ok:
        await _audit(
            request,
            action="txn.trash_purge",
            actor=manager,
            entity_ids=[oid],
        )
    return {"ok": ok}


@trash.get("/verified")
async def list_verified(
    request: Request,
    _manager: Manager,
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    category: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict:
    query = build_verified_query(year=year, month=month, category=category)
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort(list(VERIFIED_SORT))
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}
