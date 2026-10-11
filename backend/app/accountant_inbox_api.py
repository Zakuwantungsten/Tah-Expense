"""Accountant verify-inbox HTTP routes (Phase 3.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from .accountant_ops import (
    EDITED_INBOX_SORT,
    UNVERIFIED_INBOX_SORT,
    apply_cascade_filters,
    build_inbox_query,
    edited_count_query,
    format_available_dates,
    inbox_base_query,
    pending_inbox_count_query,
)
from .dependencies import require_roles
from .errors import ApiError
from .serialization import json_safe, object_id

inbox = APIRouter(tags=["accountant"])

Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]
RegisterActor = Annotated[
    dict[str, Any], Depends(require_roles("admin", "cashier", "accountant"))
]


def _database(request: Request) -> Any:
    return request.app.state.database.db


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


def _inbox_filters(
    *,
    search: str,
    truck: str,
    cashier_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    edited: bool | None,
    item: str,
    description: str,
) -> dict[str, Any]:
    return build_inbox_query(
        search=search,
        truck=truck,
        cashier_id=_parse_optional_oid(cashier_id),
        date_from=_parse_optional_dt(date_from),
        date_to=_parse_optional_dt(date_to),
        edited=edited,
        item=item,
        description=description,
    )


@inbox.get("/inbox")
async def list_inbox(
    request: Request,
    _manager: Manager,
    search: str = "",
    truck: str = "",
    cashier_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    edited: bool | None = None,
    item: str = "",
    description: str = "",
    limit: int = Query(25, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> dict:
    query = _inbox_filters(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        edited=edited,
        item=item,
        description=description,
    )
    sort = EDITED_INBOX_SORT if edited is True else UNVERIFIED_INBOX_SORT
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort(list(sort))
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@inbox.get("/inbox/count")
async def count_inbox(
    request: Request,
    _manager: Manager,
    search: str = "",
    truck: str = "",
    cashier_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    edited: bool | None = None,
    item: str = "",
    description: str = "",
) -> dict:
    query = _inbox_filters(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        edited=edited,
        item=item,
        description=description,
    )
    total = await _database(request).transactions.count_documents(query)
    return {"count": int(total)}


@inbox.get("/inbox/edited")
async def list_edited_inbox(
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
    query = _inbox_filters(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        edited=True,
        item=item,
        description=description,
    )
    docs = (
        await _database(request)
        .transactions.find(query)
        .sort(list(EDITED_INBOX_SORT))
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@inbox.get("/inbox/edited/count")
async def count_edited_inbox(
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
    query = _inbox_filters(
        search=search,
        truck=truck,
        cashier_id=cashier_id,
        date_from=date_from,
        date_to=date_to,
        edited=True,
        item=item,
        description=description,
    )
    total = await _database(request).transactions.count_documents(query)
    return {"count": int(total)}


@inbox.get("/counts/pending")
async def pending_count(request: Request, _manager: Manager) -> dict:
    db = _database(request)
    inbox_n = await db.transactions.count_documents(pending_inbox_count_query())
    deletions = await db.transactions.count_documents(
        {"deletion_requested": True, "trashed": {"$ne": True}}
    )
    restores = await db.transactions.count_documents(
        {"trashed": True, "restore_requested": True}
    )
    return {"count": int(inbox_n) + int(deletions) + int(restores)}


@inbox.get("/counts/edited")
async def edited_badge_count(request: Request, _manager: Manager) -> dict:
    total = await _database(request).transactions.count_documents(edited_count_query())
    return {"count": int(total)}


@inbox.get("/inbox/meta/dates")
async def inbox_dates(
    request: Request,
    _manager: Manager,
    edited: bool | None = None,
) -> dict:
    """Distinct expense days present in the New/Edited verify queues."""
    query = dict(inbox_base_query(rejected=False))
    if edited is True:
        query["edited_after_verification"] = True
    elif edited is False:
        query["edited_after_verification"] = {"$ne": True}
    vals = await _database(request).transactions.distinct("date", query)
    return {"items": format_available_dates(vals)}


@inbox.get("/inbox/meta/trucks")
async def inbox_trucks(request: Request, _manager: Manager) -> dict:
    vals = await _database(request).transactions.distinct(
        "truck_number", inbox_base_query(rejected=False)
    )
    items = sorted(v for v in vals if v)
    return {"items": items}


@inbox.get("/inbox/meta/items")
async def inbox_items(
    request: Request,
    _manager: Manager,
    descriptions: str = "",
) -> dict:
    desc_list = [p.strip() for p in descriptions.split(",") if p.strip()]
    base = apply_cascade_filters(
        inbox_base_query(rejected=False),
        descriptions=desc_list or None,
    )
    db = _database(request)
    items = await db.transactions.distinct("item", base)
    cats = await db.transactions.distinct("category_name", base)
    names = {*(v for v in items if v), *(v for v in cats if v)}
    return {"items": sorted(names, key=str.lower)}


@inbox.get("/inbox/meta/descriptions")
async def inbox_descriptions(
    request: Request,
    _manager: Manager,
    items: str = "",
) -> dict:
    item_list = [p.strip() for p in items.split(",") if p.strip()]
    base = apply_cascade_filters(
        inbox_base_query(rejected=False),
        items=item_list or None,
    )
    vals = await _database(request).transactions.distinct("description", base)
    return {"items": sorted((v for v in vals if v), key=str.lower)}


@inbox.get("/inbox/meta/cashiers")
async def inbox_cashier_ids(request: Request, _manager: Manager) -> dict:
    vals = await _database(request).transactions.distinct(
        "cashier_id", inbox_base_query(rejected=False)
    )
    return {"items": json_safe([v for v in vals if v is not None])}


@inbox.get("/cashier-names")
async def cashier_names(
    request: Request,
    _actor: RegisterActor,
    ids: str = Query("", description="Comma-separated user ObjectIds"),
) -> dict:
    del _actor  # auth gate only — cashiers need names for Browse/Uploads columns
    raw_ids = [part.strip() for part in ids.split(",") if part.strip()]
    if not raw_ids:
        return {"names": {}}
    query_ids: list[ObjectId] = []
    for raw in raw_ids:
        try:
            query_ids.append(object_id(raw))
        except ValueError:
            continue
    if not query_ids:
        return {"names": {}}
    docs = (
        await _database(request)
        .users.find(
            {"_id": {"$in": list(set(query_ids))}},
            {"_id": 1, "full_name": 1, "username": 1},
        )
        .to_list(length=None)
    )
    names_by_oid: dict[str, str] = {}
    for doc in docs:
        name = (doc.get("full_name") or "").strip()
        if not name:
            name = (doc.get("username") or "").strip()
        names_by_oid[str(doc["_id"])] = name or "Unknown cashier"
    return {
        "names": {
            raw: names_by_oid.get(raw, "Unknown cashier")
            for raw in raw_ids
            if ObjectId.is_valid(raw)
        }
    }
