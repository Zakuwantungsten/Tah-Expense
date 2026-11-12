"""Receipt disk ops + chase / inbox / link + evidence pack (Phases 0, 3, 4)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .audit_ops import record_operation_event
from .dependencies import Manager, require_roles
from .errors import ApiError
from .receipt_evidence import (
    build_consolidate_pack,
    build_evidence_pack,
    list_evidence_exports,
    record_evidence_export,
)
from .receipt_ops import (
    approve_receipt_files,
    chase_groups,
    ensure_receipt_workspace,
    get_receipt_file_open_meta,
    list_inbox_files,
    list_quarantine_files,
    list_receipt_day_entries,
    list_receipt_day_summaries,
    list_receipt_entries,
    list_receipt_library,
    list_receipt_requests,
    list_storage_objects,
    list_storage_tree,
    manual_link_files,
    mark_requests_reminded,
    materialize_approved_file,
    open_receipt_file_bytes,
    receipt_compliance_summary,
    reject_receipt_files,
    rematerialize_approved_for_transaction,
    search_link_transactions,
    store_receipt_file_bytes,
)
from .receipt_storage import (
    SETTING_KEY,
    list_candidate_roots,
    normalize_directory,
    probe_directory,
    resolve_attachments_directory,
    storage_status,
)
from .serialization import json_safe, object_id

receipts = APIRouter(prefix="/receipts", tags=["receipts"])

DeskActor = Annotated[
    dict[str, Any], Depends(require_roles("admin", "accountant", "cashier"))
]


class ReceiptStorageWrite(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    create: bool = False
    clear_override: bool = False


class ReceiptStorageProbe(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    create: bool = False


class ReceiptLinkBody(BaseModel):
    file_ids: list[str] = Field(default_factory=list, min_length=1)
    transaction_ids: list[str] = Field(default_factory=list, min_length=1)
    partial: bool = False
    partial_note: str = Field(default="", max_length=500)


class ReceiptRemindBody(BaseModel):
    ownership: str = ""
    request_ids: list[str] = Field(default_factory=list)


class EvidencePackBody(BaseModel):
    date_from: date
    date_to: date
    item: str = ""
    items: list[str] = Field(default_factory=list)
    payee: str = ""
    ownership: str = ""
    approved_only: bool = False


class ConsolidateBody(BaseModel):
    date_from: date
    date_to: date
    item: str = ""
    items: list[str] = Field(default_factory=list)
    payee: str = ""
    ownership: str = ""
    approved_only: bool = True


class ReceiptFilesBody(BaseModel):
    file_ids: list[str] = Field(default_factory=list, min_length=1)


class ReceiptRejectBody(BaseModel):
    file_ids: list[str] = Field(default_factory=list, min_length=1)
    reason: str = Field(min_length=3, max_length=500)


class ReceiptRematerializeBody(BaseModel):
    file_ids: list[str] = Field(default_factory=list)
    transaction_id: str = ""


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _settings(request: Request) -> Any:
    return request.app.state.settings


def _actor_oid(user: dict[str, Any]) -> ObjectId | None:
    raw = user.get("_id")
    if isinstance(raw, ObjectId):
        return raw
    try:
        return object_id(str(raw)) if raw is not None else None
    except ValueError:
        return None


@receipts.get("/storage")
async def get_receipt_storage(request: Request, _user: Manager) -> dict[str, Any]:
    """Current path, free space, write probe, and candidate drives/mounts."""
    return json_safe(await storage_status(_settings(request), _database(request)))


@receipts.get("/storage/tree")
async def get_receipt_storage_tree(
    request: Request,
    _user: Manager,
    prefix: str = Query(""),
) -> dict[str, Any]:
    """Child folders under an indexed receipt-disk prefix (files/approved/…)."""
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        payload = await list_storage_tree(db, base, prefix=prefix)
    except ValueError as exc:
        raise ApiError(422, "invalid_prefix", str(exc)) from None
    return json_safe(payload)


@receipts.get("/storage/objects")
async def get_receipt_storage_objects(
    request: Request,
    _user: Manager,
    prefix: str = Query(""),
    q: str = Query(""),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    """Files in a prefix (or filename/sha search), mapped to receipt_files when possible."""
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        payload = await list_storage_objects(
            db, base, prefix=prefix, q=q, limit=limit
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_prefix", str(exc)) from None
    return json_safe(payload)


@receipts.get("/storage/candidates")
async def get_receipt_storage_candidates(
    request: Request, _user: Manager
) -> dict[str, Any]:
    return {"items": json_safe(list_candidate_roots())}


@receipts.post("/storage/probe")
async def probe_receipt_storage(
    body: ReceiptStorageProbe, request: Request, _user: Manager
) -> dict[str, Any]:
    try:
        path = normalize_directory(body.path)
    except ValueError as exc:
        raise ApiError(422, "invalid_path", str(exc)) from None
    return json_safe(probe_directory(path, create=body.create))


@receipts.put("/storage")
async def put_receipt_storage(
    body: ReceiptStorageWrite, request: Request, actor: Manager
) -> dict[str, Any]:
    """Set or clear the runtime attachments directory override."""
    db = _database(request)
    if body.clear_override:
        await db.system_settings.delete_one({"key": SETTING_KEY})
        await record_operation_event(
            db,
            action="receipt_storage.clear",
            actor=actor,
            request=request,
            entity_type="setting",
            entity_ids=[SETTING_KEY],
            details={},
        )
        return json_safe(await storage_status(_settings(request), db))

    try:
        path = normalize_directory(body.path)
    except ValueError as exc:
        raise ApiError(422, "invalid_path", str(exc)) from None

    probe = probe_directory(path, create=body.create)
    if not probe["write_ok"]:
        raise ApiError(
            422,
            "path_not_writable",
            probe.get("error") or "Directory is not writable",
            details=probe,
        )

    await db.system_settings.update_one(
        {"key": SETTING_KEY},
        {"$set": {"key": SETTING_KEY, "value": str(path)}},
        upsert=True,
    )
    ensure_receipt_workspace(path)
    await record_operation_event(
        db,
        action="receipt_storage.update",
        actor=actor,
        request=request,
        entity_type="setting",
        entity_ids=[SETTING_KEY],
        details={"path": str(path), "created": probe.get("created")},
    )
    status = await storage_status(_settings(request), db)
    status["probe"] = probe
    return json_safe(status)


@receipts.get("/chase")
async def get_receipt_chase(
    request: Request, _user: DeskActor, limit: int = Query(200, ge=1, le=500)
) -> dict[str, Any]:
    items = await chase_groups(_database(request), limit=limit)
    return {"items": json_safe(items), "total": len(items)}


@receipts.get("/requests")
async def get_receipt_requests(
    request: Request,
    _user: DeskActor,
    preset: str = Query("open"),
    ownership: str = "",
    overdue_hours: float = Query(48.0, ge=1.0, le=720.0),
    reminder_threshold: int = Query(3, ge=1, le=50),
    limit: int = Query(400, ge=1, le=1000),
) -> dict[str, Any]:
    items = await list_receipt_requests(
        _database(request),
        preset=preset,
        ownership=ownership,
        overdue_hours=overdue_hours,
        reminder_threshold=reminder_threshold,
        limit=limit,
    )
    return {"items": json_safe(items), "total": len(items), "preset": preset}


@receipts.get("/compliance")
async def get_receipt_compliance(
    request: Request,
    _user: DeskActor,
    overdue_hours: float = Query(48.0, ge=1.0, le=720.0),
    reminder_threshold: int = Query(3, ge=1, le=50),
) -> dict[str, Any]:
    summary = await receipt_compliance_summary(
        _database(request),
        overdue_hours=overdue_hours,
        reminder_threshold=reminder_threshold,
    )
    return json_safe(summary)


@receipts.get("/day-summaries")
async def get_receipt_day_summaries(
    request: Request,
    _user: DeskActor,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    cashier_id: str | None = Query(None),
    limit: int = Query(40, ge=1, le=500),
    offset: int = Query(0, ge=0, le=100000),
) -> dict[str, Any]:
    """Main tab: one row per register day with receipt progress counts.

    Counts: Needed, Partial, Collected, Waived, No receipt, Multi-file, Shared.
    Supports ``offset`` / ``limit`` for infinite-scroll pagination.

    **Needed** — open request ``needed`` or ``receipt_status=receipt``.
    **Partial** also increments Needed (still incomplete) plus ``partial_count``.
    **Collected** — received / linked complete.
    **Multi-file** — one entry with many attached files.
    **Shared** — entry whose file is also linked to another entry.

    Day key is register calendar day (``import_primary_date`` else ``date``).
    Default scope is all cashiers (same as chase/compliance); pass
    ``cashier_id`` to narrow.
    """
    owner: ObjectId | None = None
    if cashier_id and str(cashier_id).strip():
        try:
            owner = object_id(str(cashier_id).strip())
        except Exception as exc:
            raise ApiError(422, "invalid_cashier_id", "Invalid cashier_id") from exc
    payload = await list_receipt_day_summaries(
        _database(request),
        date_from=date_from,
        date_to=date_to,
        cashier_id=owner,
        limit=limit,
        offset=offset,
    )
    return json_safe(payload)


@receipts.get("/day-entries")
async def get_receipt_day_entries(
    request: Request,
    _user: DeskActor,
    day: date = Query(..., description="Register calendar day YYYY-MM-DD"),
    cashier_id: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    """Main drill: expense rows for one register day with one display status.

    Each item includes ``display_status`` / ``display_status_key`` and
    attachment refs (``primary_file_id``, ``attachments[]``) for the shared
    viewer. Open path is unchanged — client uses existing file open routes.
    """
    owner: ObjectId | None = None
    if cashier_id and str(cashier_id).strip():
        try:
            owner = object_id(str(cashier_id).strip())
        except Exception as exc:
            raise ApiError(422, "invalid_cashier_id", "Invalid cashier_id") from exc
    items = await list_receipt_day_entries(
        _database(request),
        day=day,
        cashier_id=owner,
        limit=limit,
    )
    return json_safe({"items": items, "total": len(items), "day": day.isoformat()})


@receipts.get("/entries")
async def get_receipt_entries(
    request: Request,
    _user: DeskActor,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    status: str = Query("all"),
    cashier_id: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    """View all: flat entry list across a date range (same row shape as day-entries).

    ``status`` filters ``display_status_key``: all | needed | received | partial |
    waived | no_receipt | pending.
    """
    owner: ObjectId | None = None
    if cashier_id and str(cashier_id).strip():
        try:
            owner = object_id(str(cashier_id).strip())
        except Exception as exc:
            raise ApiError(422, "invalid_cashier_id", "Invalid cashier_id") from exc
    items = await list_receipt_entries(
        _database(request),
        date_from=date_from,
        date_to=date_to,
        status=status,
        cashier_id=owner,
        limit=limit,
    )
    return json_safe(
        {
            "items": items,
            "total": len(items),
            "status": (status or "all").strip().lower() or "all",
        }
    )


@receipts.get("/inbox")
async def get_receipt_inbox(
    request: Request,
    _user: DeskActor,
    status: str = Query("parked"),
    unmatched_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    items = await list_inbox_files(
        _database(request),
        status=status,
        unmatched_only=unmatched_only,
        limit=limit,
    )
    return {"items": json_safe(items), "total": len(items), "status": status}


@receipts.get("/quarantine")
async def get_receipt_quarantine(
    request: Request,
    _user: DeskActor,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    items = await list_quarantine_files(db, base, limit=limit)
    return {"items": json_safe(items), "total": len(items)}


@receipts.get("/library")
async def get_receipt_library(
    request: Request,
    _user: DeskActor,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    item: str = Query(""),
    payee: str = Query(""),
    ownership: str = Query(""),
    status: str = Query("all"),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    """Submitted receipts inquiry — filter by upload date + linked row fields."""
    try:
        items = await list_receipt_library(
            _database(request),
            date_from=date_from,
            date_to=date_to,
            item=item,
            payee=payee,
            ownership=ownership,
            status=status,
            limit=limit,
        )
    except ValueError as exc:
        raise ApiError(422, "library_failed", str(exc)) from None
    return {
        "items": json_safe(items),
        "total": len(items),
        "status": status or "all",
    }


@receipts.get("/files/{file_id}/meta")
async def get_receipt_file_meta(
    file_id: str, request: Request, _user: DeskActor
) -> dict[str, Any]:
    """Viewer chrome for an inbox / library file (no bytes)."""
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        meta = await get_receipt_file_open_meta(db, base, file_id)
    except ValueError as exc:
        msg = str(exc)
        code = "not_found" if "not found" in msg.lower() else "invalid_file"
        status_code = 404 if code == "not_found" else 422
        raise ApiError(status_code, code, msg) from None
    return json_safe(meta)


@receipts.get("/files/{file_id}/open")
async def get_receipt_file_open(
    file_id: str, request: Request, _user: DeskActor
) -> FileResponse:
    """Stream receipt bytes for inline view (image / PDF / any stored type)."""
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        path, filename, content_type = await open_receipt_file_bytes(db, base, file_id)
    except ValueError as exc:
        msg = str(exc)
        code = "not_found" if "not found" in msg.lower() else "invalid_file"
        status_code = 404 if code == "not_found" else 422
        raise ApiError(status_code, code, msg) from None
    except FileNotFoundError as exc:
        raise ApiError(404, "file_missing", str(exc)) from None
    return FileResponse(
        path,
        filename=filename,
        media_type=content_type or "application/octet-stream",
        content_disposition_type="inline",
    )


@receipts.post("/files/approve")
async def post_receipt_files_approve(
    body: ReceiptFilesBody, request: Request, actor: DeskActor
) -> dict[str, Any]:
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    ensure_receipt_workspace(base)
    file_ids = [fid.strip() for fid in body.file_ids if fid.strip()]
    if not file_ids:
        raise ApiError(422, "empty_files", "Select at least one file")
    try:
        result = await approve_receipt_files(
            db, base, file_ids=file_ids, actor_id=_actor_oid(actor)
        )
    except ValueError as exc:
        raise ApiError(422, "approve_failed", str(exc)) from None
    await record_operation_event(
        db,
        action="receipt.approve",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=file_ids[:40],
        details={"updated": result.get("updated")},
    )
    return json_safe(result)


@receipts.post("/files/rematerialize")
async def post_receipt_files_rematerialize(
    body: ReceiptRematerializeBody, request: Request, actor: DeskActor
) -> dict[str, Any]:
    """Idempotent browse-folder refresh after approve or when item/payee lands."""
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    ensure_receipt_workspace(base)
    count = 0
    tid = (body.transaction_id or "").strip()
    if tid:
        try:
            oid = object_id(tid)
        except ValueError as exc:
            raise ApiError(422, "invalid_id", "Invalid transaction id") from exc
        count += await rematerialize_approved_for_transaction(db, base, oid)
    file_ids = [fid.strip() for fid in body.file_ids if fid.strip()]
    for fid in file_ids:
        doc = await db.receipt_files.find_one({"_id": fid})
        if not doc:
            continue
        paths = await materialize_approved_file(db, base, doc)
        if paths:
            count += 1
    if not tid and not file_ids:
        raise ApiError(422, "empty", "Provide file_ids and/or transaction_id")
    await record_operation_event(
        db,
        action="receipt.materialize",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=file_ids[:40] or ([tid] if tid else []),
        details={"materialized": count, "transaction_id": tid or None},
    )
    return {"materialized": count}


@receipts.post("/files/reject")
async def post_receipt_files_reject(
    body: ReceiptRejectBody, request: Request, actor: DeskActor
) -> dict[str, Any]:
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    ensure_receipt_workspace(base)
    file_ids = [fid.strip() for fid in body.file_ids if fid.strip()]
    if not file_ids:
        raise ApiError(422, "empty_files", "Select at least one file")
    try:
        result = await reject_receipt_files(
            db,
            base,
            file_ids=file_ids,
            reason=body.reason,
            actor_id=_actor_oid(actor),
        )
    except ValueError as exc:
        raise ApiError(422, "reject_failed", str(exc)) from None
    await record_operation_event(
        db,
        action="receipt.reject",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=file_ids[:40],
        details={"updated": result.get("updated"), "reason": body.reason[:240]},
    )
    return json_safe(result)


@receipts.get("/link-candidates")
async def get_link_candidates(
    request: Request,
    _user: DeskActor,
    ownership: str = "",
    amount: float | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    needed_only: bool = Query(False),
    today: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    items = await search_link_transactions(
        _database(request),
        ownership=ownership,
        amount=amount,
        date_from=date_from,
        date_to=date_to,
        needed_only=needed_only,
        today=today,
        limit=limit,
    )
    return {"items": json_safe(items), "total": len(items)}


@receipts.post("/files/upload", status_code=status.HTTP_201_CREATED)
async def post_receipt_files_early_upload(
    request: Request,
    actor: DeskActor,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    """Desk early drop — park unmatched files with no request/tx yet."""
    if not files:
        raise ApiError(422, "files_required", "At least one file is required")
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    ensure_receipt_workspace(base)
    actor_id = _actor_oid(actor)
    saved: list[dict[str, Any]] = []
    for upload in files:
        filename = upload.filename or "file"
        try:
            content = await upload.read()
        except Exception as exc:
            raise ApiError(400, "read_failed", "Could not read uploaded file") from exc
        finally:
            await upload.close()
        try:
            meta = await store_receipt_file_bytes(
                db,
                base,
                filename=filename,
                content=content,
                source="desktop",
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ApiError(422, "invalid_attachment", str(exc)) from None
        except OSError as exc:
            raise ApiError(500, "storage_error", "Failed to store attachment") from exc
        saved.append(meta)
    await record_operation_event(
        db,
        action="receipt.park",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=[str(m.get("id")) for m in saved][:40],
        details={"files": len(saved), "source": "desktop"},
    )
    return {"files": json_safe(saved), "total": len(saved)}


@receipts.post("/link")
async def post_receipt_link(
    body: ReceiptLinkBody, request: Request, actor: DeskActor
) -> dict[str, Any]:
    try:
        tx_ids = [object_id(raw) for raw in body.transaction_ids]
    except ValueError as exc:
        raise ApiError(422, "invalid_id", "One or more transaction ids are invalid") from exc
    file_ids = [fid.strip() for fid in body.file_ids if fid.strip()]
    if not file_ids or not tx_ids:
        raise ApiError(422, "empty_link", "Select at least one file and one transaction")
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        result = await manual_link_files(
            db,
            file_ids=file_ids,
            transaction_ids=tx_ids,
            actor_id=_actor_oid(actor),
            partial=bool(body.partial),
            partial_note=body.partial_note or "",
            attachments_dir=base,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_link", str(exc)) from None
    await record_operation_event(
        db,
        action="receipt.link",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=file_ids[:40],
        details={
            "linked": result.get("linked"),
            "partial": result.get("partial"),
            "partial_note": result.get("partial_note") or "",
            "transaction_ids": [str(oid) for oid in tx_ids][:40],
            "rematerialized": result.get("rematerialized"),
        },
    )
    return json_safe(result)


@receipts.post("/mark-reminded")
async def post_mark_reminded(
    body: ReceiptRemindBody, request: Request, actor: DeskActor
) -> dict[str, Any]:
    request_ids: list[ObjectId] = []
    for raw in body.request_ids:
        try:
            request_ids.append(object_id(raw))
        except ValueError as exc:
            raise ApiError(422, "invalid_id", "One or more request ids are invalid") from exc
    updated = await mark_requests_reminded(
        _database(request),
        ownership=body.ownership,
        request_ids=request_ids or None,
    )
    await record_operation_event(
        _database(request),
        action="receipt.mark_reminded",
        actor=actor,
        request=request,
        entity_type="receipt_request",
        entity_ids=[str(oid) for oid in request_ids][:40] or [body.ownership],
        details={"updated": updated, "ownership": body.ownership},
    )
    return {"updated": updated}


@receipts.post("/files/consolidate")
async def post_receipt_files_consolidate(
    body: ConsolidateBody, request: Request, actor: Manager
) -> Response:
    """Accountant/admin: files-only ZIP for date/item/payee/ownership filters."""
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        payload, summary = await build_consolidate_pack(
            db,
            base,
            date_from=body.date_from,
            date_to=body.date_to,
            item=body.item,
            items=list(body.items or []),
            payee=body.payee,
            ownership=body.ownership,
            approved_only=bool(body.approved_only),
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_consolidate", str(exc)) from None

    export_id = await record_evidence_export(db, summary=summary, actor=actor)
    await record_operation_event(
        db,
        action="receipt.consolidate",
        actor=actor,
        request=request,
        entity_type="receipt_export",
        entity_ids=[str(export_id)],
        details={
            "sha256": summary.get("sha256"),
            "size": summary.get("size"),
            "row_count": summary.get("row_count"),
            "files_in_zip": summary.get("files_in_zip"),
            "missing_on_disk": summary.get("missing_on_disk"),
            "date_from": body.date_from.isoformat(),
            "date_to": body.date_to.isoformat(),
            "item": body.item,
            "items": list(summary.get("items") or []),
            "approved_only": bool(body.approved_only),
            "payee": body.payee,
            "ownership": body.ownership,
            "filename": summary.get("filename"),
            "kind": "consolidate",
        },
    )
    filename = str(summary.get("filename") or "tahmeed-receipts.zip")
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Pack-Sha256": str(summary.get("sha256") or ""),
            "X-Export-Id": str(export_id),
            "X-Pack-Rows": str(summary.get("row_count") or 0),
            "X-Pack-With-File": str(summary.get("with_file") or 0),
            "X-Pack-Without-File": str(summary.get("without_file") or 0),
            "X-Pack-Files": str(summary.get("files_in_zip") or 0),
            "X-Pack-Missing": str(summary.get("missing_on_disk") or 0),
        },
    )


@receipts.post("/evidence-pack")
async def post_evidence_pack(
    body: EvidencePackBody, request: Request, actor: Manager
) -> Response:
    """Accountant/admin: build a date-range ZIP (folders + index.csv + README)."""
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        payload, summary = await build_evidence_pack(
            db,
            base,
            date_from=body.date_from,
            date_to=body.date_to,
            item=body.item,
            items=list(body.items or []),
            payee=body.payee,
            ownership=body.ownership,
            approved_only=bool(body.approved_only),
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_pack", str(exc)) from None

    export_id = await record_evidence_export(db, summary=summary, actor=actor)
    await record_operation_event(
        db,
        action="receipt.evidence_pack",
        actor=actor,
        request=request,
        entity_type="receipt_export",
        entity_ids=[str(export_id)],
        details={
            "sha256": summary.get("sha256"),
            "size": summary.get("size"),
            "row_count": summary.get("row_count"),
            "with_file": summary.get("with_file"),
            "without_file": summary.get("without_file"),
            "waived": summary.get("waived"),
            "date_from": body.date_from.isoformat(),
            "date_to": body.date_to.isoformat(),
            "item": body.item,
            "items": list(summary.get("items") or []),
            "approved_only": bool(body.approved_only),
            "payee": body.payee,
            "ownership": body.ownership,
            "filename": summary.get("filename"),
        },
    )
    filename = str(summary.get("filename") or "tahmeed-evidence.zip")
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Pack-Sha256": str(summary.get("sha256") or ""),
            "X-Export-Id": str(export_id),
            "X-Pack-Rows": str(summary.get("row_count") or 0),
            "X-Pack-With-File": str(summary.get("with_file") or 0),
            "X-Pack-Without-File": str(summary.get("without_file") or 0),
        },
    )


@receipts.get("/evidence-exports")
async def get_evidence_exports(
    request: Request,
    _user: Manager,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    items = await list_evidence_exports(_database(request), limit=limit)
    return {"items": json_safe(items), "total": len(items)}
