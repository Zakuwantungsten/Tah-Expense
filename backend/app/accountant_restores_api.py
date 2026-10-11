"""Accountant restore-request HTTP routes (cashier trash → Verify)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from .accountant_ops import (
    RESTORE_BASE_MATCH,
    RESTORE_SORT,
    build_restore_query,
    format_available_dates,
    restore_descriptions_query,
    restore_items_query,
)
from .dependencies import require_roles
from .errors import ApiError
from .schemas import BulkIdsRequest
from .security import utcnow
from .serialization import json_safe, object_id

restores = APIRouter(tags=["accountant"])

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


def _restore_filters(
    *,
    search: str,
    truck: str,
    cashier_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    item: str,
    description: str,
) -> dict[str, Any]:
    return build_restore_query(
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


_CLEAR_RESTORE = {
    "restore_requested": False,
    "restore_requested_at": None,
    "restore_requested_by": None,
}

_CLEAR_TRASH = {
    "trashed": False,
    "trashed_at": None,
    "trashed_by": None,
    **_CLEAR_RESTORE,
}


@restores.get("/restores")
async def list_restores(
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
    query = _restore_filters(
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
        .sort(list(RESTORE_SORT))
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@restores.get("/restores/count")
async def count_restores(
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
    query = _restore_filters(
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


@restores.get("/counts/restores")
async def restores_badge_count(request: Request, _manager: Manager) -> dict:
    total = await _database(request).transactions.count_documents(RESTORE_BASE_MATCH)
    return {"count": int(total)}


@restores.get("/restores/meta/dates")
async def restore_dates(request: Request, _manager: Manager) -> dict:
    vals = await _database(request).transactions.distinct("date", RESTORE_BASE_MATCH)
    return {"items": format_available_dates(vals)}


@restores.get("/restores/meta/trucks")
async def restore_trucks(request: Request, _manager: Manager) -> dict:
    vals = await _database(request).transactions.distinct(
        "truck_number", RESTORE_BASE_MATCH
    )
    return {"items": sorted(v for v in vals if v)}


@restores.get("/restores/meta/cashiers")
async def restore_cashier_ids(request: Request, _manager: Manager) -> dict:
    vals = await _database(request).transactions.distinct(
        "cashier_id", RESTORE_BASE_MATCH
    )
    return {"items": json_safe([v for v in vals if v is not None])}


@restores.get("/restores/meta/items")
async def restore_items(
    request: Request,
    _manager: Manager,
    descriptions: str = "",
) -> dict:
    desc_list = [p.strip() for p in descriptions.split(",") if p.strip()]
    query = restore_items_query(desc_list or None)
    vals = await _database(request).transactions.distinct("item", query)
    cats = await _database(request).transactions.distinct("category_name", query)
    merged = sorted({*(v for v in vals if v), *(v for v in cats if v)})
    return {"items": merged}


@restores.get("/restores/meta/descriptions")
async def restore_descriptions(
    request: Request,
    _manager: Manager,
    items: str = "",
) -> dict:
    item_list = [p.strip() for p in items.split(",") if p.strip()]
    query = restore_descriptions_query(item_list or None)
    vals = await _database(request).transactions.distinct("description", query)
    return {"items": sorted(v for v in vals if v)}


@restores.post("/restores/bulk-confirm")
async def bulk_confirm_restores(
    body: BulkIdsRequest, request: Request, manager: Manager
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    result = await _database(request).transactions.update_many(
        {"_id": {"$in": ids}, **RESTORE_BASE_MATCH},
        {"$set": dict(_CLEAR_TRASH)},
    )
    n = int(result.modified_count)
    if n:
        await _audit(
            request,
            action="txn.restore_confirm_bulk",
            actor=manager,
            entity_ids=ids,
            details={"count": n},
        )
    return {"updated": n}


@restores.post("/restores/bulk-reject")
async def bulk_reject_restores(
    body: BulkIdsRequest, request: Request, manager: Manager
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    result = await _database(request).transactions.update_many(
        {"_id": {"$in": ids}, **RESTORE_BASE_MATCH},
        {"$set": dict(_CLEAR_RESTORE)},
    )
    n = int(result.modified_count)
    if n:
        await _audit(
            request,
            action="txn.restore_reject_bulk",
            actor=manager,
            entity_ids=ids,
            details={"count": n},
        )
    return {"updated": n}


@restores.post("/restores/{tx_id}/confirm")
async def confirm_restore(
    tx_id: str, request: Request, manager: Manager
) -> dict:
    oid = _valid_id(tx_id)
    result = await _database(request).transactions.update_one(
        {"_id": oid, **RESTORE_BASE_MATCH},
        {"$set": dict(_CLEAR_TRASH)},
    )
    ok = result.modified_count == 1
    if ok:
        await _audit(
            request,
            action="txn.restore_confirm",
            actor=manager,
            entity_ids=[oid],
        )
    return {"ok": ok}


@restores.post("/restores/{tx_id}/reject")
async def reject_restore(
    tx_id: str, request: Request, manager: Manager
) -> dict:
    oid = _valid_id(tx_id)
    result = await _database(request).transactions.update_one(
        {"_id": oid, **RESTORE_BASE_MATCH},
        {"$set": dict(_CLEAR_RESTORE)},
    )
    ok = result.modified_count == 1
    if ok:
        await _audit(
            request,
            action="txn.restore_reject",
            actor=manager,
            entity_ids=[oid],
        )
    return {"ok": ok}
