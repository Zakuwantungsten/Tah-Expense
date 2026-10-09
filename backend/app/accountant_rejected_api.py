"""Accountant rejected-queue HTTP routes (Phase 3.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from .accountant_ops import (
    REJECTED_SORT,
    apply_cascade_filters,
    build_rejected_query,
    inbox_base_query,
)
from .dependencies import require_roles
from .errors import ApiError
from .serialization import json_safe, object_id

rejected = APIRouter(tags=["accountant"])

Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]


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


def _rejected_filters(
    *,
    search: str,
    truck: str,
    cashier_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    item: str,
    description: str,
) -> dict[str, Any]:
    return build_rejected_query(
        search=search,
        truck=truck,
        cashier_id=_parse_optional_oid(cashier_id),
        date_from=_parse_optional_dt(date_from),
        date_to=_parse_optional_dt(date_to),
        item=item,
        description=description,
    )


@rejected.get("/rejected")
async def list_rejected(
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
    query = _rejected_filters(
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
        .sort(list(REJECTED_SORT))
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return {"items": json_safe(docs), "total": len(docs)}


@rejected.get("/rejected/count")
async def count_rejected(
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
    query = _rejected_filters(
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


@rejected.get("/counts/rejected")
async def rejected_badge_count(request: Request, _manager: Manager) -> dict:
    total = await _database(request).transactions.count_documents(
        inbox_base_query(rejected=True)
    )
    return {"count": int(total)}


@rejected.get("/rejected/meta/trucks")
async def rejected_trucks(request: Request, _manager: Manager) -> dict:
    vals = await _database(request).transactions.distinct(
        "truck_number", inbox_base_query(rejected=True)
    )
    return {"items": sorted(v for v in vals if v)}


@rejected.get("/rejected/meta/items")
async def rejected_items(
    request: Request,
    _manager: Manager,
    descriptions: str = "",
) -> dict:
    desc_list = [p.strip() for p in descriptions.split(",") if p.strip()]
    base = apply_cascade_filters(
        inbox_base_query(rejected=True),
        descriptions=desc_list or None,
    )
    db = _database(request)
    items = await db.transactions.distinct("item", base)
    cats = await db.transactions.distinct("category_name", base)
    names = {*(v for v in items if v), *(v for v in cats if v)}
    return {"items": sorted(names, key=str.lower)}


@rejected.get("/rejected/meta/descriptions")
async def rejected_descriptions(
    request: Request,
    _manager: Manager,
    items: str = "",
) -> dict:
    item_list = [p.strip() for p in items.split(",") if p.strip()]
    base = apply_cascade_filters(
        inbox_base_query(rejected=True),
        items=item_list or None,
    )
    vals = await _database(request).transactions.distinct("description", base)
    return {"items": sorted((v for v in vals if v), key=str.lower)}


@rejected.get("/rejected/meta/cashiers")
async def rejected_cashier_ids(request: Request, _manager: Manager) -> dict:
    vals = await _database(request).transactions.distinct(
        "cashier_id", inbox_base_query(rejected=True)
    )
    return {"items": json_safe([v for v in vals if v is not None])}
