"""Receipt disk ops + chase / inbox / link + evidence pack (Phases 0, 3, 4)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from bson import ObjectId
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .audit_ops import record_operation_event
from .dependencies import Authenticated, Manager, require_roles
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
    create_storage_folder,
    delete_empty_storage_folder,
    rename_storage_folder,
    trash_storage_folder,
    desk_undo_waive_receipts,
    desk_waive_receipts,
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
    empty_receipt_trash,
    list_trashed_receipt_files,
    manual_link_files,
    mark_requests_reminded,
    materialize_approved_file,
    open_receipt_file_bytes,
    park_receipt_uploads,
    person_receipt_dossier,
    purge_trashed_receipt_files,
    receipt_compliance_summary,
    reject_receipt_files,
    resolve_intended_ownership,
    rematerialize_approved_for_transaction,
    restore_all_trashed_receipt_files,
    restore_trashed_receipt_files,
    search_link_transactions,
    still_request_linked_entries,
    trash_receipt_files,
    upload_and_link_receipt_files,
)
from .receipt_records_ops import (
    build_records_pack,
    create_custom_folder,
    delete_custom_folder,
    discard_record_upload_batch,
    ensure_custom_folder_path,
    ensure_records_workspace,
    list_custom_folders,
    list_record_destinations,
    list_record_files,
    move_record_files,
    parse_record_file_date,
    rename_custom_folder,
    resolve_record_destination,
    store_record_file_bytes,
)
from .receipt_storage import (
    SETTING_KEY,
    list_candidate_roots,
    normalize_directory,
    probe_directory,
    resolve_attachments_directory,
    storage_status,
)
from .receipt_storage_health import (
    StorageUnavailableError,
    assert_receipt_storage_ready,
    get_storage_health,
    on_storage_path_changed,
    refresh_storage_health,
    storage_unavailable_error_payload,
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


class ReceiptDeskWaiveBody(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    request_ids: list[str] = Field(default_factory=list)
    transaction_ids: list[str] = Field(default_factory=list)


class ReceiptDeskUndoWaiveBody(BaseModel):
    request_ids: list[str] = Field(default_factory=list)
    transaction_ids: list[str] = Field(default_factory=list)


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


class ReceiptStillRequestBody(BaseModel):
    transaction_ids: list[str] = Field(default_factory=list, min_length=1)
    note: str = Field(min_length=3, max_length=500)
    notify: bool = True


class ReceiptRejectBody(BaseModel):
    file_ids: list[str] = Field(default_factory=list, min_length=1)
    reason: str = Field(min_length=3, max_length=500)


class ReceiptRematerializeBody(BaseModel):
    file_ids: list[str] = Field(default_factory=list)
    transaction_id: str = ""


class RecordFolderCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None


class RecordFolderRename(BaseModel):
    label: str = Field(min_length=1, max_length=120)


class RecordsPackBody(BaseModel):
    kind: str = ""
    ref_key: str = ""
    folder_id: str = ""
    q: str = ""
    date_from: date | None = None
    date_to: date | None = None


class RecordsDiscardBatchBody(BaseModel):
    file_ids: list[str] = Field(default_factory=list)


class RecordsMoveBody(BaseModel):
    file_ids: list[str] = Field(default_factory=list)
    kind: str = Field(min_length=1)
    ref_key: str = ""
    folder_id: str = ""


class ReceiptTrashBody(BaseModel):
    file_ids: list[str] = Field(default_factory=list)


class StorageFolderCreateBody(BaseModel):
    prefix: str = ""
    name: str = Field(min_length=1, max_length=120)


class StorageFolderRenameBody(BaseModel):
    prefix: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)


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


async def _require_receipt_storage(request: Request) -> None:
    try:
        await assert_receipt_storage_ready(_settings(request), _database(request))
    except StorageUnavailableError as exc:
        raise ApiError(
            503,
            "receipt_storage_unavailable",
            exc.message,
            details=storage_unavailable_error_payload(exc),
        ) from None


@receipts.get("/storage")
async def get_receipt_storage(request: Request, _user: Manager) -> dict[str, Any]:
    """Current path, free space, write probe, and candidate drives/mounts."""
    status = await storage_status(_settings(request), _database(request))
    health = await refresh_storage_health(
        _settings(request), _database(request), force=True
    )
    status["health"] = health
    return json_safe(status)


@receipts.get("/storage/health")
async def get_receipt_storage_health(
    request: Request, _user: Authenticated
) -> dict[str, Any]:
    """Light health signal for desktop badges and mobile degraded mode."""
    return json_safe(
        await get_storage_health(_settings(request), _database(request), force=False)
    )


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
    """Folders + files in a prefix (or filename/sha search), mapped to receipt_files when possible."""
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        payload = await list_storage_objects(
            db, base, prefix=prefix, q=q, limit=limit
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_prefix", str(exc)) from None
    return json_safe(payload)


@receipts.post("/storage/folders", status_code=status.HTTP_201_CREATED)
async def post_storage_folder(
    request: Request,
    actor: Manager,
    body: StorageFolderCreateBody,
) -> dict[str, Any]:
    await _require_receipt_storage(request)
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        payload = await create_storage_folder(
            base, prefix=body.prefix, name=body.name
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_folder", str(exc)) from None
    await record_operation_event(
        db,
        action="storage.folder_create",
        actor=actor,
        request=request,
        entity_type="storage_folder",
        entity_ids=[str(payload.get("prefix") or "")],
        details=payload,
    )
    return json_safe(payload)


@receipts.patch("/storage/folders")
async def patch_storage_folder(
    request: Request,
    actor: Manager,
    body: StorageFolderRenameBody,
) -> dict[str, Any]:
    await _require_receipt_storage(request)
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        payload = await rename_storage_folder(
            base, prefix=body.prefix, name=body.name
        )
    except ValueError as exc:
        raise ApiError(422, "folder_rename_failed", str(exc)) from None
    await record_operation_event(
        db,
        action="storage.folder_rename",
        actor=actor,
        request=request,
        entity_type="storage_folder",
        entity_ids=[str(payload.get("prefix") or "")],
        details=payload,
    )
    return json_safe(payload)


@receipts.delete("/storage/folders")
async def delete_storage_folder_route(
    request: Request,
    actor: Manager,
    prefix: str = Query(...),
    cascade: bool = Query(False),
) -> dict[str, Any]:
    await _require_receipt_storage(request)
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        if cascade:
            payload = await trash_storage_folder(
                db, base, prefix=prefix, actor_id=_actor_oid(actor)
            )
        else:
            payload = await delete_empty_storage_folder(base, prefix=prefix)
    except ValueError as exc:
        raise ApiError(422, "folder_delete_failed", str(exc)) from None
    file_ids = [
        str(x) for x in (payload.get("file_ids") or []) if str(x or "").strip()
    ]
    await record_operation_event(
        db,
        action="storage.folder_delete",
        actor=actor,
        request=request,
        entity_type="storage_folder",
        entity_ids=[prefix, *file_ids][:40],
        details={**payload, "cascade": cascade},
    )
    # Separate trash trail when cascade moved indexed files (searchable under Trash).
    if cascade and int(payload.get("trashed_files") or 0) > 0:
        await record_operation_event(
            db,
            action="receipts.trash",
            actor=actor,
            request=request,
            entity_type="receipt_file",
            entity_ids=file_ids[:40],
            details={
                "source": "storage.folder_delete",
                "prefix": prefix,
                "trashed": payload.get("trashed_files"),
                "skipped": payload.get("skipped_files"),
            },
        )
    return json_safe(payload)


@receipts.get("/trash")
async def get_receipt_trash(
    request: Request,
    _user: Manager,
    limit: int = Query(200, ge=1, le=500),
    q: str = Query(""),
) -> dict[str, Any]:
    return json_safe(
        await list_trashed_receipt_files(_database(request), limit=limit, q=q)
    )


@receipts.post("/trash/restore-all")
async def post_receipt_trash_restore_all(
    request: Request,
    actor: Manager,
) -> dict[str, Any]:
    result = await restore_all_trashed_receipt_files(_database(request))
    await record_operation_event(
        db=_database(request),
        action="receipts.trash_restore_all",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        details=result,
    )
    return json_safe(result)


@receipts.post("/trash/empty")
async def post_receipt_trash_empty(
    request: Request,
    actor: Manager,
) -> dict[str, Any]:
    await _require_receipt_storage(request)
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    result = await empty_receipt_trash(db, base)
    await record_operation_event(
        db=db,
        action="receipts.trash_empty",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        details=result,
    )
    return json_safe(result)


@receipts.post("/trash")
async def post_receipt_trash(
    request: Request,
    actor: Manager,
    body: ReceiptTrashBody,
) -> dict[str, Any]:
    ids = [str(x).strip() for x in (body.file_ids or []) if str(x or "").strip()]
    if not ids:
        raise ApiError(422, "file_ids_required", "Select at least one file")
    result = await trash_receipt_files(
        _database(request), file_ids=ids, actor_id=_actor_oid(actor)
    )
    await record_operation_event(
        db=_database(request),
        action="receipts.trash",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=ids[:40],
        details=result,
    )
    return json_safe(result)


@receipts.post("/trash/restore")
async def post_receipt_trash_restore(
    request: Request,
    actor: Manager,
    body: ReceiptTrashBody,
) -> dict[str, Any]:
    ids = [str(x).strip() for x in (body.file_ids or []) if str(x or "").strip()]
    if not ids:
        raise ApiError(422, "file_ids_required", "Select at least one file")
    result = await restore_trashed_receipt_files(_database(request), file_ids=ids)
    await record_operation_event(
        db=_database(request),
        action="receipts.trash_restore",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=ids[:40],
        details=result,
    )
    return json_safe(result)


@receipts.post("/trash/purge")
async def post_receipt_trash_purge(
    request: Request,
    actor: Manager,
    body: ReceiptTrashBody,
) -> dict[str, Any]:
    ids = [str(x).strip() for x in (body.file_ids or []) if str(x or "").strip()]
    if not ids:
        raise ApiError(422, "file_ids_required", "Select at least one file")
    await _require_receipt_storage(request)
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    result = await purge_trashed_receipt_files(db, base, file_ids=ids)
    await record_operation_event(
        db=db,
        action="receipts.trash_purge",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=ids[:40],
        details=result,
    )
    return json_safe(result)


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
        env_path = await resolve_attachments_directory(_settings(request), db)
        try:
            await on_storage_path_changed(db, env_path)
        except Exception:
            pass
        await record_operation_event(
            db,
            action="receipt_storage.clear",
            actor=actor,
            request=request,
            entity_type="setting",
            entity_ids=[SETTING_KEY],
            details={},
        )
        status = await storage_status(_settings(request), db)
        status["health"] = await refresh_storage_health(
            _settings(request), db, force=True
        )
        return json_safe(status)

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
    await on_storage_path_changed(db, path)
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
    status["health"] = await refresh_storage_health(
        _settings(request), db, force=True
    )
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
    q: str = Query("", description="Search ownership, truck, description, item, approver…"),
    overdue_hours: float = Query(48.0, ge=1.0, le=720.0),
    reminder_threshold: int = Query(3, ge=1, le=50),
    limit: int = Query(400, ge=1, le=1000),
) -> dict[str, Any]:
    items = await list_receipt_requests(
        _database(request),
        preset=preset,
        ownership=ownership,
        q=q,
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
    ownership: str = Query(""),
) -> dict[str, Any]:
    """Desk KPI counts. Optional ``ownership`` scopes to one person."""
    summary = await receipt_compliance_summary(
        _database(request),
        overdue_hours=overdue_hours,
        reminder_threshold=reminder_threshold,
        ownership=ownership,
    )
    return json_safe(summary)


@receipts.get("/people/{ownership}")
async def get_person_receipt_dossier(
    ownership: str,
    request: Request,
    _user: DeskActor,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    overdue_hours: float = Query(48.0, ge=1.0, le=720.0),
    reminder_threshold: int = Query(3, ge=1, le=50),
    entry_limit: int = Query(500, ge=1, le=2000),
    request_limit: int = Query(400, ge=1, le=1000),
    file_limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    """Person dossier: stats + needed + entries + submitted + parked + rejected.

    ``ownership`` is the chase target name (URL-decoded, normalized UPPER).
    """
    try:
        payload = await person_receipt_dossier(
            _database(request),
            ownership,
            date_from=date_from,
            date_to=date_to,
            overdue_hours=overdue_hours,
            reminder_threshold=reminder_threshold,
            entry_limit=entry_limit,
            request_limit=request_limit,
            file_limit=file_limit,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_ownership", str(exc)) from None
    return json_safe(payload)


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
    ownership: str = Query(""),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    """Main drill: expense rows for one register day with one display status.

    Each item includes ``display_status`` / ``display_status_key`` and
    attachment refs (``primary_file_id``, ``attachments[]``) for the shared
    viewer. Open path is unchanged — client uses existing file open routes.
    Optional ``ownership`` narrows to one person's rows.
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
        ownership=ownership,
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
    ownership: str = Query(""),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    """View all: flat entry list across a date range (same row shape as day-entries).

    ``status`` filters ``display_status_key``: all | needed | received | partial |
    waived | no_receipt | pending.
    ``ownership`` filters transaction ownership (normalized person name).
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
        ownership=ownership,
        limit=limit,
    )
    return json_safe(
        {
            "items": items,
            "total": len(items),
            "status": (status or "all").strip().lower() or "all",
            "ownership": (ownership or "").strip() or None,
        }
    )


@receipts.get("/inbox")
async def get_receipt_inbox(
    request: Request,
    _user: DeskActor,
    status: str = Query("parked"),
    unmatched_only: bool = Query(False),
    ownership: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Inbox files. Optional ``ownership`` scopes to one person's attributed files."""
    items = await list_inbox_files(
        _database(request),
        status=status,
        unmatched_only=unmatched_only,
        ownership=ownership,
        limit=limit,
    )
    return {
        "items": json_safe(items),
        "total": len(items),
        "status": status,
        "ownership": (ownership or "").strip() or None,
    }


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
        details={
            "updated": result.get("updated"),
            "completed_transactions": result.get("completed_transactions"),
        },
    )
    return json_safe(result)


@receipts.post("/files/{file_id}/still-request")
async def post_receipt_file_still_request(
    file_id: str,
    body: ReceiptStillRequestBody,
    request: Request,
    actor: DeskActor,
) -> dict[str, Any]:
    """Carve out linked row(s): reopen chase + optional re-notify ownership."""
    tx_ids: list[ObjectId] = []
    for raw in body.transaction_ids:
        try:
            tx_ids.append(object_id(raw))
        except ValueError as exc:
            raise ApiError(
                422, "invalid_id", "One or more transaction ids are invalid"
            ) from exc
    try:
        result = await still_request_linked_entries(
            _database(request),
            file_id=file_id,
            transaction_ids=tx_ids,
            note=body.note,
            actor=actor,
            notify=bool(body.notify),
        )
    except ValueError as exc:
        raise ApiError(422, "still_request_failed", str(exc)) from None
    await record_operation_event(
        _database(request),
        action="receipt.still_request",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=[file_id],
        details={
            "reopened": result.get("reopened"),
            "skipped": result.get("skipped"),
            "notified": result.get("notified"),
            "transaction_ids": [str(oid) for oid in tx_ids][:40],
            "note": (body.note or "")[:200],
        },
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


async def _read_upload_batch(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    if not files:
        raise ApiError(422, "files_required", "At least one file is required")
    uploads: list[tuple[str, bytes]] = []
    for upload in files:
        filename = upload.filename or "file"
        try:
            content = await upload.read()
        except Exception as exc:
            raise ApiError(400, "read_failed", "Could not read uploaded file") from exc
        finally:
            await upload.close()
        uploads.append((filename, content))
    return uploads


@receipts.post("/files/upload", status_code=status.HTTP_201_CREATED)
async def post_receipt_files_early_upload(
    request: Request,
    actor: DeskActor,
    files: list[UploadFile] = File(...),
    ownership: str = Form(""),
) -> dict[str, Any]:
    """Desk early drop — park unmatched files with no request/tx yet (atomic).

    Optional ``ownership`` stamps ``intended_ownership`` for person dossier / Inbox.
    """
    uploads = await _read_upload_batch(files)
    await _require_receipt_storage(request)
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    ensure_receipt_workspace(base)
    actor_id = _actor_oid(actor)
    intended, person_id = await resolve_intended_ownership(
        db, explicit=ownership, user=None
    )
    try:
        saved = await park_receipt_uploads(
            db,
            base,
            uploads=uploads,
            actor_id=actor_id,
            source="desktop",
            intended_ownership=intended,
            asked_of_person_id=person_id,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_attachment", str(exc)) from None
    except OSError as exc:
        raise ApiError(500, "storage_error", "Failed to store attachment") from exc
    await record_operation_event(
        db,
        action="receipt.park",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=[str(m.get("id")) for m in saved][:40],
        details={
            "files": len(saved),
            "source": "desktop",
            "atomic": True,
            "intended_ownership": intended or None,
        },
    )
    return {
        "files": json_safe(saved),
        "total": len(saved),
        "intended_ownership": intended or None,
    }


@receipts.post("/upload-and-link", status_code=status.HTTP_201_CREATED)
async def post_receipt_upload_and_link(
    request: Request,
    actor: DeskActor,
    files: list[UploadFile] = File(...),
    transaction_ids: str = Form(""),
    partial: bool = Form(False),
    partial_note: str = Form(""),
) -> dict[str, Any]:
    """Atomic park + link for on-behalf uploads (rolls back parked files on link failure)."""
    import json as _json

    uploads = await _read_upload_batch(files)
    raw_ids = (transaction_ids or "").strip()
    parsed: list[str] = []
    if raw_ids.startswith("["):
        try:
            loaded = _json.loads(raw_ids)
        except _json.JSONDecodeError as exc:
            raise ApiError(422, "invalid_id", "transaction_ids must be JSON or comma-separated") from exc
        if isinstance(loaded, list):
            parsed = [str(x).strip() for x in loaded if str(x).strip()]
    else:
        parsed = [part.strip() for part in raw_ids.split(",") if part.strip()]
    try:
        tx_ids = [object_id(raw) for raw in parsed]
    except ValueError as exc:
        raise ApiError(422, "invalid_id", "One or more transaction ids are invalid") from exc
    if not tx_ids:
        raise ApiError(422, "empty_link", "Select at least one transaction")

    await _require_receipt_storage(request)
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    ensure_receipt_workspace(base)
    try:
        result = await upload_and_link_receipt_files(
            db,
            base,
            uploads=uploads,
            transaction_ids=tx_ids,
            actor_id=_actor_oid(actor),
            partial=bool(partial),
            partial_note=partial_note or "",
            source="desktop",
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_upload_link", str(exc)) from None
    except OSError as exc:
        raise ApiError(500, "storage_error", "Failed to store attachment") from exc

    await record_operation_event(
        db,
        action="receipt.upload_and_link",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=list(result.get("file_ids") or [])[:40],
        details={
            "files": result.get("total"),
            "linked": result.get("linked"),
            "partial": result.get("partial"),
            "transaction_ids": [str(oid) for oid in tx_ids][:40],
            "atomic": True,
        },
    )
    return json_safe(result)


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


@receipts.post("/waive")
async def post_desk_waive(
    body: ReceiptDeskWaiveBody, request: Request, actor: DeskActor
) -> dict[str, Any]:
    """Cashier/accountant: waive selected receipt requests / expense rows."""
    request_ids: list[ObjectId] = []
    for raw in body.request_ids:
        try:
            request_ids.append(object_id(raw))
        except ValueError as exc:
            raise ApiError(422, "invalid_id", "One or more request ids are invalid") from exc
    transaction_ids: list[ObjectId] = []
    for raw in body.transaction_ids:
        try:
            transaction_ids.append(object_id(raw))
        except ValueError as exc:
            raise ApiError(
                422, "invalid_id", "One or more transaction ids are invalid"
            ) from exc
    try:
        result = await desk_waive_receipts(
            _database(request),
            reason=body.reason,
            actor=actor,
            request_ids=request_ids or None,
            transaction_ids=transaction_ids or None,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_waiver", str(exc)) from None
    await record_operation_event(
        _database(request),
        action="receipt.desk_waive",
        actor=actor,
        request=request,
        entity_type="receipt_request",
        entity_ids=list(result.get("request_ids") or [])[:40],
        details={
            "waived": result.get("waived"),
            "skipped": result.get("skipped"),
            "reason": body.reason[:200],
            "transaction_ids": [str(oid) for oid in transaction_ids][:40],
        },
    )
    return json_safe(result)


@receipts.post("/undo-waive")
async def post_desk_undo_waive(
    body: ReceiptDeskUndoWaiveBody, request: Request, actor: DeskActor
) -> dict[str, Any]:
    """Cashier/accountant: undo waive for selected receipt requests / expense rows."""
    request_ids: list[ObjectId] = []
    for raw in body.request_ids:
        try:
            request_ids.append(object_id(raw))
        except ValueError as exc:
            raise ApiError(422, "invalid_id", "One or more request ids are invalid") from exc
    transaction_ids: list[ObjectId] = []
    for raw in body.transaction_ids:
        try:
            transaction_ids.append(object_id(raw))
        except ValueError as exc:
            raise ApiError(
                422, "invalid_id", "One or more transaction ids are invalid"
            ) from exc
    try:
        result = await desk_undo_waive_receipts(
            _database(request),
            request_ids=request_ids or None,
            transaction_ids=transaction_ids or None,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_undo_waiver", str(exc)) from None
    await record_operation_event(
        _database(request),
        action="receipt.desk_undo_waive",
        actor=actor,
        request=request,
        entity_type="receipt_request",
        entity_ids=list(result.get("request_ids") or [])[:40],
        details={
            "restored": result.get("restored"),
            "skipped": result.get("skipped"),
            "transaction_ids": [str(oid) for oid in transaction_ids][:40],
        },
    )
    return json_safe(result)


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


# ── Paperwork → Records (Manager only) ───────────────────────────────────────


@receipts.get("/records/destinations")
async def get_record_destinations(request: Request, _user: Manager) -> dict[str, Any]:
    """Items, suppliers, separate-expense keys, and reusable custom folders."""
    return json_safe(await list_record_destinations(_database(request)))


@receipts.get("/records/folders")
async def get_record_folders(request: Request, _user: Manager) -> dict[str, Any]:
    items = await list_custom_folders(_database(request))
    return {"items": json_safe(items), "total": len(items)}


@receipts.post("/records/folders", status_code=status.HTTP_201_CREATED)
async def post_record_folder(
    body: RecordFolderCreate, request: Request, actor: Manager
) -> dict[str, Any]:
    db = _database(request)
    label = (body.label or "").strip()
    try:
        # Smart nested path: "Parent/Child" creates (or reuses) the chain.
        if "/" in label.replace("\\", "/") and not body.parent_id:
            folder = await ensure_custom_folder_path(
                db, path=label, actor_id=_actor_oid(actor)
            )
        else:
            folder = await create_custom_folder(
                db,
                label=label,
                actor_id=_actor_oid(actor),
                parent_id=body.parent_id,
            )
    except ValueError as exc:
        raise ApiError(422, "invalid_folder", str(exc)) from None
    await record_operation_event(
        db,
        action="records.folder.create",
        actor=actor,
        request=request,
        entity_type="receipt_record_folder",
        entity_ids=[folder["id"]],
        details={
            "label": folder.get("label"),
            "slug": folder.get("slug"),
            "parent_id": folder.get("parent_id"),
            "display_label": folder.get("display_label"),
        },
    )
    return json_safe(folder)


@receipts.patch("/records/folders/{folder_id}")
async def patch_record_folder(
    folder_id: str,
    body: RecordFolderRename,
    request: Request,
    actor: Manager,
) -> dict[str, Any]:
    try:
        oid = object_id(folder_id)
    except ValueError as exc:
        raise ApiError(422, "invalid_id", "Invalid folder id") from exc
    db = _database(request)
    try:
        folder = await rename_custom_folder(db, oid, label=body.label)
    except ValueError as exc:
        raise ApiError(422, "invalid_folder", str(exc)) from None
    await record_operation_event(
        db,
        action="records.folder.rename",
        actor=actor,
        request=request,
        entity_type="receipt_record_folder",
        entity_ids=[folder["id"]],
        details={"label": folder.get("label"), "slug": folder.get("slug")},
    )
    return json_safe(folder)


@receipts.delete("/records/folders/{folder_id}")
async def delete_record_folder_route(
    folder_id: str,
    request: Request,
    actor: Manager,
    cascade: bool = Query(False),
) -> dict[str, Any]:
    try:
        oid = object_id(folder_id)
    except ValueError as exc:
        raise ApiError(422, "invalid_id", "Invalid folder id") from exc
    db = _database(request)
    try:
        result = await delete_custom_folder(
            db, oid, cascade=cascade, actor_id=_actor_oid(actor)
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_folder", str(exc)) from None
    file_ids = [
        str(x) for x in (result.get("file_ids") or []) if str(x or "").strip()
    ]
    await record_operation_event(
        db,
        action="records.folder.delete",
        actor=actor,
        request=request,
        entity_type="receipt_record_folder",
        entity_ids=[folder_id, *file_ids][:40],
        details=result,
    )
    if cascade and int(result.get("trashed_files") or 0) > 0:
        await record_operation_event(
            db,
            action="receipts.trash",
            actor=actor,
            request=request,
            entity_type="receipt_file",
            entity_ids=file_ids[:40],
            details={
                "source": "records.folder.delete",
                "folder_id": folder_id,
                "trashed": result.get("trashed_files"),
                "deleted_folders": result.get("deleted_folders"),
            },
        )
    return json_safe(result)


@receipts.get("/records/files")
async def get_record_files(
    request: Request,
    _user: Manager,
    kind: str = Query(""),
    ref_key: str = Query(""),
    folder_id: str = Query(""),
    q: str = Query(""),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0, le=10_000_000),
) -> dict[str, Any]:
    try:
        items = await list_record_files(
            _database(request),
            kind=kind,
            ref_key=ref_key,
            folder_id=folder_id,
            q=q,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_filter", str(exc)) from None
    return {"items": json_safe(items), "total": len(items), "offset": offset}


@receipts.post("/records/upload", status_code=status.HTTP_201_CREATED)
async def post_records_upload(
    request: Request,
    actor: Manager,
    files: list[UploadFile] = File(...),
    kind: str = Form(...),
    ref_key: str = Form(""),
    folder_id: str = Form(""),
    note: str = Form(""),
    file_date: str = Form(""),
) -> dict[str, Any]:
    """Bulk file into Records under Item / Supplier / Separate / Custom."""
    if not files:
        raise ApiError(422, "files_required", "At least one file is required")
    await _require_receipt_storage(request)
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    ensure_records_workspace(base)
    try:
        destination = await resolve_record_destination(
            db, kind=kind, ref_key=ref_key, folder_id=folder_id
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_destination", str(exc)) from None

    try:
        filed_on = parse_record_file_date(file_date or None)
    except ValueError as exc:
        raise ApiError(422, "invalid_file_date", str(exc)) from None

    actor_id = _actor_oid(actor)
    note_clean = " ".join((note or "").split())[:2000]
    saved: list[dict[str, Any]] = []
    note_file: dict[str, Any] | None = None
    try:
        for upload in files:
            filename = upload.filename or "file"
            try:
                content = await upload.read()
            except Exception as exc:
                raise ApiError(
                    400, "read_failed", "Could not read uploaded file"
                ) from exc
            finally:
                await upload.close()
            try:
                meta = await store_record_file_bytes(
                    db,
                    base,
                    filename=filename,
                    content=content,
                    destination=destination,
                    actor_id=actor_id,
                    note="",
                    claimed_content_type=upload.content_type,
                    file_date=filed_on,
                )
            except ValueError as exc:
                raise ApiError(422, "invalid_attachment", str(exc)) from None
            except OSError as exc:
                raise ApiError(
                    500, "storage_error", "Failed to store attachment"
                ) from exc
            saved.append(meta)

        if note_clean:
            from .security import utcnow

            stamp = utcnow().strftime("%Y%m%d_%H%M%S")
            note_name = f"upload-note_{stamp}.txt"
            note_body = (
                "Tahmeed Records — upload note\n"
                f"Destination: {destination.get('label') or destination.get('ref_key') or ''}\n"
                f"Type: {destination.get('kind') or ''}\n"
                f"File as date: {filed_on.isoformat()}\n"
                f"Files in this upload: {len(saved)}\n"
                "-----\n"
                f"{note_clean}\n"
            ).encode("utf-8")
            try:
                note_file = await store_record_file_bytes(
                    db,
                    base,
                    filename=note_name,
                    content=note_body,
                    destination=destination,
                    actor_id=actor_id,
                    note=note_clean[:500],
                    claimed_content_type="text/plain",
                    file_date=filed_on,
                )
                saved.append(note_file)
            except (ValueError, OSError):
                # Files already stored — do not fail the whole upload for a note miss.
                note_file = None
    except Exception as exc:
        if saved:
            rollback_ids = [str(m.get("id") or "") for m in saved if m.get("id")]
            discarded = await discard_record_upload_batch(db, base, rollback_ids)
            await record_operation_event(
                db,
                action="records.discard_batch",
                actor=actor,
                request=request,
                entity_type="receipt_file",
                entity_ids=rollback_ids[:40],
                outcome="fail",
                error=str(exc)[:400],
                details={
                    "rollback": True,
                    "discarded": discarded,
                    "kind": destination.get("kind"),
                    "ref_key": destination.get("ref_key"),
                    "folder_id": destination.get("folder_id"),
                    "label": destination.get("label"),
                },
            )
        raise

    await record_operation_event(
        db,
        action="records.upload",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=[str(m.get("id")) for m in saved][:40],
        details={
            "files": len(saved),
            "kind": destination.get("kind"),
            "ref_key": destination.get("ref_key"),
            "folder_id": destination.get("folder_id"),
            "label": destination.get("label"),
            "file_date": filed_on.isoformat(),
            "note_file": bool(note_file),
            "has_note": bool(note_clean),
            "atomic": True,
        },
    )
    return {
        "files": json_safe(saved),
        "total": len(saved),
        "destination": json_safe(destination),
        "file_date": filed_on.isoformat(),
        "note_file": json_safe(note_file) if note_file else None,
        "atomic": True,
    }


@receipts.post("/records/move")
async def post_records_move(
    request: Request,
    actor: Manager,
    body: RecordsMoveBody,
) -> dict[str, Any]:
    """Cut → Paste: move Records files into another destination folder."""
    file_ids = [str(x).strip() for x in (body.file_ids or []) if str(x or "").strip()]
    if not file_ids:
        raise ApiError(422, "file_ids_required", "Select at least one file")
    await _require_receipt_storage(request)
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        destination = await resolve_record_destination(
            db,
            kind=body.kind,
            ref_key=body.ref_key,
            folder_id=body.folder_id,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_destination", str(exc)) from None
    try:
        result = await move_record_files(
            db, base, file_ids=file_ids, destination=destination
        )
    except ValueError as exc:
        raise ApiError(422, "move_failed", str(exc)) from None
    await record_operation_event(
        db,
        action="records.move",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=file_ids[:40],
        details={
            "moved": result.get("moved"),
            "skipped": result.get("skipped"),
            "kind": destination.get("kind"),
            "ref_key": destination.get("ref_key"),
            "folder_id": destination.get("folder_id"),
            "label": destination.get("label"),
        },
    )
    return json_safe(result)


@receipts.post("/records/discard-batch")
async def post_records_discard_batch(
    request: Request,
    actor: Manager,
    body: RecordsDiscardBatchBody,
) -> dict[str, Any]:
    """Roll back file ids from a cancelled / failed sequential Records upload."""
    file_ids = [str(x).strip() for x in (body.file_ids or []) if str(x or "").strip()]
    if not file_ids:
        return {"discarded": 0, "requested": 0}
    await _require_receipt_storage(request)
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    discarded = await discard_record_upload_batch(db, base, file_ids)
    await record_operation_event(
        db,
        action="records.discard_batch",
        actor=actor,
        request=request,
        entity_type="receipt_file",
        entity_ids=file_ids[:40],
        details={"requested": len(file_ids), "discarded": discarded},
    )
    return {"discarded": discarded, "requested": len(file_ids)}


@receipts.post("/records/pack")
async def post_records_pack(
    body: RecordsPackBody, request: Request, actor: Manager
) -> Response:
    """Accountant/admin: ZIP Records filings for destination / date filters."""
    db = _database(request)
    base = await resolve_attachments_directory(_settings(request), db)
    try:
        payload, summary = await build_records_pack(
            db,
            base,
            kind=body.kind,
            ref_key=body.ref_key,
            folder_id=body.folder_id,
            q=body.q,
            date_from=body.date_from,
            date_to=body.date_to,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_pack", str(exc)) from None

    await record_operation_event(
        db,
        action="records.pack",
        actor=actor,
        request=request,
        entity_type="receipt_record_pack",
        entity_ids=[],
        details={
            "sha256": summary.get("sha256"),
            "file_count": summary.get("file_count"),
            "missing": summary.get("missing"),
            "kind": summary.get("kind"),
            "ref_key": summary.get("ref_key"),
            "folder_id": summary.get("folder_id"),
            "filename": summary.get("filename"),
        },
    )
    filename = str(summary.get("filename") or "tahmeed-records.zip")
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Pack-Sha256": str(summary.get("sha256") or ""),
            "X-Pack-Files": str(summary.get("file_count") or 0),
            "X-Pack-Missing": str(summary.get("missing") or 0),
        },
    )
