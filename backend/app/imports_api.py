"""Daily + master Excel import HTTP routes (Phase 7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request, status

from .dependencies import require_roles
from .errors import ApiError
from .coa_import_ops import commit_chart_of_accounts
from .import_ops import (
    build_issue_query,
    commit_daily_to_master,
    commit_master_import,
    daily_uploads_pipeline,
    null_daily_import_id_filter,
)
from .schemas import (
    ChartOfAccountsImportRequest,
    DailyImportCommitRequest,
    MasterImportCommitRequest,
)
from .security import utcnow
from .serialization import json_safe, object_id
from .mongo_async import aggregate_list

imports = APIRouter(prefix="/imports", tags=["imports"])
daily = APIRouter(prefix="/daily", tags=["imports-daily"])
master = APIRouter(prefix="/master", tags=["imports-master"])
Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _actor_oid(user: dict[str, Any]) -> ObjectId | None:
    raw = user.get("_id")
    if isinstance(raw, ObjectId):
        return raw
    try:
        return object_id(str(raw)) if raw is not None else None
    except ValueError:
        return None


def _valid_id(value: str) -> ObjectId:
    try:
        return object_id(value)
    except ValueError:
        raise ApiError(422, "invalid_id", "Resource id is invalid") from None


async def _audit(
    request: Request,
    *,
    action: str,
    actor: dict[str, Any],
    upload_id: str | None = None,
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
                "entity_type": "import_batch",
                "entity_ids": [upload_id] if upload_id else [],
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


# --- Daily commit (7.2) -------------------------------------------------------


@daily.post("/commit", status_code=status.HTTP_201_CREATED)
async def daily_commit(
    body: DailyImportCommitRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    db = _database(request)
    try:
        result = await commit_daily_to_master(
            db,
            body.rows,
            verified_by=_actor_oid(manager),
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_row", str(exc)) from None
    await _audit(
        request,
        action="import.daily_master",
        actor=manager,
        upload_id=result.get("upload_id") or None,
        details={
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in result.items()
        },
    )
    return json_safe(result)


# --- Daily uploads (7.3) ------------------------------------------------------


@daily.post("/cleanup-null-ids")
async def cleanup_null_daily_import_ids(
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager  # auth gate only
    db = _database(request)
    result = await db.transactions.update_many(
        null_daily_import_id_filter(),
        {
            "$unset": {
                "daily_import_id": "",
                "daily_import_source": "",
                "import_primary_date": "",
            }
        },
    )
    return {"modified": int(result.modified_count or 0)}


@daily.get("/uploads")
async def list_daily_uploads(
    request: Request,
    manager: Manager,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    del manager
    db = _database(request)
    # Heal legacy null-tagged rows so they never group as a phantom upload.
    await db.transactions.update_many(
        null_daily_import_id_filter(),
        {
            "$unset": {
                "daily_import_id": "",
                "daily_import_source": "",
                "import_primary_date": "",
            }
        },
    )
    rows = await aggregate_list(db.transactions, 
        daily_uploads_pipeline(limit=limit)
    , length=limit)
    return json_safe(rows)


@daily.get("/uploads/{upload_id}/records")
async def get_daily_upload_records(
    upload_id: str,
    request: Request,
    manager: Manager,
    skip: int = Query(0, ge=0),
    limit: int = Query(10000, ge=1, le=20000),
) -> list[dict[str, Any]]:
    del manager
    uid = (upload_id or "").strip()
    if not uid:
        return []
    db = _database(request)
    cursor = (
        db.transactions.find({"daily_import_id": uid})
        .sort([("date", 1), ("day_order", 1), ("created_at", 1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return json_safe(docs)


@daily.delete("/uploads/{upload_id}")
async def delete_daily_upload(
    upload_id: str,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    uid = (upload_id or "").strip()
    if not uid:
        return {"deleted": 0}
    db = _database(request)
    result = await db.transactions.delete_many({"daily_import_id": uid})
    deleted = int(result.deleted_count or 0)
    await _audit(
        request,
        action="import.daily_delete",
        actor=manager,
        upload_id=uid,
        details={"deleted": deleted},
    )
    return {"deleted": deleted}


# --- Daily issues (7.4) -------------------------------------------------------


@daily.get("/issues")
async def list_issue_transactions(
    request: Request,
    manager: Manager,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    search: str = Query(""),
    truck: str = Query(""),
    cashier_id: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    item: str = Query(""),
    description: str = Query(""),
) -> list[dict[str, Any]]:
    del manager
    cashier = None
    if cashier_id:
        cashier = _valid_id(cashier_id)
    query = build_issue_query(
        search=search,
        truck=truck,
        cashier_id=cashier,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
    )
    cursor = (
        _database(request)
        .transactions.find(query)
        .sort([("date", -1), ("created_at", -1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return json_safe(docs)


@daily.get("/issues/count")
async def count_issue_transactions(
    request: Request,
    manager: Manager,
    search: str = Query(""),
    truck: str = Query(""),
    cashier_id: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    item: str = Query(""),
    description: str = Query(""),
) -> dict[str, Any]:
    del manager
    cashier = None
    if cashier_id:
        cashier = _valid_id(cashier_id)
    query = build_issue_query(
        search=search,
        truck=truck,
        cashier_id=cashier,
        date_from=date_from,
        date_to=date_to,
        item=item,
        description=description,
    )
    count = await _database(request).transactions.count_documents(query)
    return {"count": int(count)}


@daily.post("/issues/{tx_id}/clear")
async def clear_issue_flags(
    tx_id: str,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager
    oid = _valid_id(tx_id)
    result = await _database(request).transactions.update_one(
        {"_id": oid},
        {"$set": {"possible_duplicate": False, "date_discrepancy": False}},
    )
    return {"cleared": result.modified_count == 1}


# --- Master commit (7.5) ------------------------------------------------------


@master.post("/commit", status_code=status.HTTP_201_CREATED)
async def master_commit(
    body: MasterImportCommitRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    if not body.rows:
        raise ApiError(422, "empty_rows", "No rows to import")
    db = _database(request)
    try:
        result = await commit_master_import(
            db,
            body.rows,
            source_filename=body.source_filename,
            verified_by=_actor_oid(manager),
            skip_duplicates=body.skip_duplicates,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_row", str(exc)) from None
    await _audit(
        request,
        action="import.master",
        actor=manager,
        upload_id=result.get("upload_id") or None,
        details=dict(result),
    )
    return json_safe(result)


# --- Chart of Accounts (Phase 10) ---------------------------------------------


@imports.post("/chart-of-accounts", status_code=status.HTTP_201_CREATED)
async def chart_of_accounts_import(
    body: ChartOfAccountsImportRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    if not body.rows:
        raise ApiError(422, "empty_rows", "No accounts found in the Chart of Accounts payload.")
    db = _database(request)
    try:
        result = await commit_chart_of_accounts(
            db,
            body.rows,
            replace_existing=body.replace_existing,
        )
    except ValueError as exc:
        raise ApiError(422, "empty_rows", str(exc)) from None
    details = dict(result)
    if body.source_filename:
        details["source_filename"] = body.source_filename
    await _audit(
        request,
        action="coa.replace",
        actor=manager,
        details=details,
    )
    return json_safe(details)


imports.include_router(daily)
imports.include_router(master)
