"""Transaction attachment HTTP routes (Phase 5)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response

from .attachment_ops import (
    add_attachment_bytes,
    cleanup_attachment_orphans,
    find_attachment_meta,
    remove_attachment,
    resolve_attachment_path,
)
from .dependencies import Authenticated, Manager, require_roles
from .errors import ApiError
from .security import utcnow
from .serialization import json_safe, object_id

attachments = APIRouter(tags=["attachments"])

_WRITE_ROLES = ("admin", "cashier", "accountant")
AttachmentWriter = Annotated[dict[str, Any], Depends(require_roles(*_WRITE_ROLES))]


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _attachments_dir(request: Request) -> Path:
    return Path(request.app.state.settings.attachments_directory)


def _valid_id(value: str) -> ObjectId:
    try:
        return object_id(value)
    except ValueError:
        raise ApiError(422, "invalid_id", "Resource id is invalid") from None


def _actor_id(user: dict[str, Any]) -> ObjectId | None:
    raw = user.get("_id")
    if isinstance(raw, ObjectId):
        return raw
    try:
        return object_id(str(raw)) if raw is not None else None
    except ValueError:
        return None


async def _require_transaction(request: Request, tx_id: ObjectId) -> dict[str, Any]:
    doc = await _database(request).transactions.find_one(
        {"_id": tx_id}, {"attachments": 1}
    )
    if not doc:
        raise ApiError(404, "not_found", "Transaction not found")
    return doc


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
                "entity_type": "attachment",
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


@attachments.post("/attachments/cleanup-orphans")
async def cleanup_orphans(
    request: Request,
    manager: Manager,
    max_age_hours: float = Query(24.0, ge=0.0, le=720.0),
    dry_run: bool = Query(False),
) -> dict[str, Any]:
    result = await cleanup_attachment_orphans(
        _database(request),
        _attachments_dir(request),
        max_age_hours=max_age_hours,
        dry_run=dry_run,
    )
    await _audit(
        request,
        action="attachment.cleanup_orphans",
        actor=manager,
        entity_ids=[],
        details={**result, "dry_run": dry_run, "max_age_hours": max_age_hours},
    )
    return result


@attachments.get("/{tx_id}/attachments")
async def list_attachments(
    tx_id: str, request: Request, _user: Authenticated
) -> dict[str, Any]:
    oid = _valid_id(tx_id)
    doc = await _require_transaction(request, oid)
    items = list(doc.get("attachments") or [])
    return {"items": json_safe(items), "total": len(items)}


@attachments.get("/{tx_id}/attachments/{attachment_id}/file")
async def download_attachment(
    tx_id: str,
    attachment_id: str,
    request: Request,
    _user: Authenticated,
) -> FileResponse:
    oid = _valid_id(tx_id)
    if not attachment_id or not attachment_id.strip():
        raise ApiError(422, "invalid_attachment_id", "Attachment id is required")
    doc = await _require_transaction(request, oid)
    meta = find_attachment_meta(doc, attachment_id.strip())
    if meta is None:
        raise ApiError(404, "attachment_not_found", "Attachment not found")
    path = resolve_attachment_path(_attachments_dir(request), oid, meta)
    if path is None:
        raise ApiError(404, "file_missing", "Attachment file is missing on the server")
    filename = str(meta.get("filename") or path.name)
    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
    )


@attachments.post("/{tx_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    tx_id: str,
    request: Request,
    actor: AttachmentWriter,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    oid = _valid_id(tx_id)
    await _require_transaction(request, oid)
    filename = file.filename or "file"
    try:
        content = await file.read()
    except Exception as exc:
        raise ApiError(400, "read_failed", "Could not read uploaded file") from exc
    finally:
        await file.close()

    try:
        meta = await add_attachment_bytes(
            _database(request),
            _attachments_dir(request),
            oid,
            filename=filename,
            content=content,
            actor_id=_actor_id(actor),
        )
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc)) from None
    except ValueError as exc:
        raise ApiError(422, "invalid_attachment", str(exc)) from None
    except OSError as exc:
        raise ApiError(500, "storage_error", "Failed to store attachment") from exc

    await _audit(
        request,
        action="attachment.add",
        actor=actor,
        entity_ids=[oid],
        details={"attachment_id": meta.get("id"), "filename": meta.get("filename")},
    )
    return json_safe(meta)


@attachments.delete(
    "/{tx_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_attachment(
    tx_id: str,
    attachment_id: str,
    request: Request,
    actor: AttachmentWriter,
) -> Response:
    oid = _valid_id(tx_id)
    if not attachment_id or not attachment_id.strip():
        raise ApiError(422, "invalid_attachment_id", "Attachment id is required")
    attachment_id = attachment_id.strip()
    doc = await _require_transaction(request, oid)
    if find_attachment_meta(doc, attachment_id) is None:
        raise ApiError(404, "attachment_not_found", "Attachment not found")

    removed = await remove_attachment(
        _database(request),
        _attachments_dir(request),
        oid,
        attachment_id,
    )
    if removed is None:
        raise ApiError(404, "attachment_not_found", "Attachment not found")

    await _audit(
        request,
        action="attachment.remove",
        actor=actor,
        entity_ids=[oid],
        details={
            "attachment_id": attachment_id,
            "filename": removed.get("filename"),
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
