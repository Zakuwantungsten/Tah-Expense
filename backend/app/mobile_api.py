"""Mobile receipt-capture API under ``/v1/mobile`` (Phase 1)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from bson import ObjectId
from fastapi import (
    APIRouter,
    File,
    Form,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .audit_ops import record_operation_event
from .dependencies import Authenticated
from .errors import ApiError
from .receipt_backup import mirror_attachment
from .receipt_ops import (
    get_receipt_file_open_meta,
    link_file_to_transaction,
    list_open_requests_for_user,
    open_receipt_file_bytes,
    ownership_targets_for_user,
    register_expo_push_token,
    request_summary,
    store_receipt_file_bytes,
    waive_receipt_request,
)
from .receipt_storage import resolve_attachments_directory
from .receipt_upload_security import UploadSecurityError, check_mobile_upload_rate
from .schemas import LoginRequest, RefreshRequest
from .serialization import json_safe, object_id

mobile = APIRouter(prefix="/mobile", tags=["mobile"])
_log = logging.getLogger("tahmeed.mobile")


class ExpoDeviceRegister(BaseModel):
    expo_push_token: str = Field(min_length=8, max_length=200)


class ReceiptWaiveBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _actor_id(user: dict[str, Any]) -> ObjectId | None:
    raw = user.get("_id")
    if isinstance(raw, ObjectId):
        return raw
    try:
        return object_id(str(raw)) if raw is not None else None
    except ValueError:
        return None


def _enforce_mobile_upload_limits(request: Request, user: dict[str, Any], files: list) -> None:
    settings = request.app.state.settings
    max_files = int(getattr(settings, "receipt_mobile_max_files_per_request", 10) or 10)
    if len(files) > max_files:
        raise ApiError(
            422,
            "too_many_files",
            f"At most {max_files} files per upload request",
        )
    limit = int(getattr(settings, "receipt_mobile_uploads_per_hour", 60) or 0)
    try:
        check_mobile_upload_rate(
            str(user.get("_id") or user.get("username") or "anon"),
            limit=limit,
            file_count=len(files),
        )
    except UploadSecurityError as exc:
        raise ApiError(429, getattr(exc, "code", "rate_limited"), str(exc)) from None


def _valid_oid(value: str) -> ObjectId:
    try:
        return object_id(value)
    except ValueError:
        raise ApiError(422, "invalid_id", "Resource id is invalid") from None


async def _load_owned_request(
    request: Request, user: dict[str, Any], request_id: str
) -> dict[str, Any]:
    oid = _valid_oid(request_id)
    doc = await _database(request).receipt_requests.find_one({"_id": oid})
    if not doc:
        raise ApiError(404, "not_found", "Receipt request not found")
    targets = await ownership_targets_for_user(_database(request), user)
    names = set(targets["names"])
    person_ids = set(targets["person_ids"])
    asked_name = str(doc.get("asked_of_name") or "")
    asked_person = doc.get("asked_of_person_id")
    owned = asked_name in names or (
        isinstance(asked_person, ObjectId) and asked_person in person_ids
    )
    if not owned:
        raise ApiError(403, "forbidden", "This receipt request is not assigned to you")
    return doc


@mobile.post("/auth/login")
async def mobile_login(body: LoginRequest, request: Request) -> dict:
    from datetime import timedelta

    from .api import login as desktop_login
    from .security import create_access_token, utcnow

    tokens = await desktop_login(body, request)
    # Tag session as mobile and extend refresh so ownership can "sign in once".
    try:
        db = _database(request)
        settings = request.app.state.settings
        user = await db.users.find_one({"username": body.username})
        if user:
            session = (
                await db.auth_sessions.find({"user_id": user["_id"]})
                .sort("created_at", -1)
                .limit(1)
                .to_list(length=1)
            )
            if session:
                now = utcnow()
                days = int(getattr(settings, "mobile_refresh_token_days", 180) or 180)
                await db.auth_sessions.update_one(
                    {"_id": session[0]["_id"]},
                    {
                        "$set": {
                            "client": "mobile",
                            "expires_at": now + timedelta(days=days),
                        }
                    },
                )
                minutes = int(
                    getattr(settings, "mobile_access_token_minutes", 60) or 60
                )
                access, access_expires = create_access_token(
                    settings,
                    user_id=str(user["_id"]),
                    role=str(user.get("role") or ""),
                    session_id=str(session[0]["_id"]),
                    minutes=minutes,
                )
                tokens = {
                    **tokens,
                    "access_token": access,
                    "access_expires_at": access_expires.isoformat(),
                }
    except Exception:
        pass
    return tokens


@mobile.post("/auth/refresh")
async def mobile_refresh(body: RefreshRequest, request: Request) -> dict:
    from .api import refresh as desktop_refresh

    return await desktop_refresh(body, request)


@mobile.post("/devices")
async def register_device(
    body: ExpoDeviceRegister, request: Request, user: Authenticated
) -> dict[str, Any]:
    try:
        updated = await register_expo_push_token(
            _database(request), user, body.expo_push_token
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_token", str(exc)) from None
    await record_operation_event(
        _database(request),
        action="mobile.device_register",
        actor=user,
        request=request,
        entity_type="user",
        entity_ids=[str(user.get("_id") or "")],
        details={"updated_people": updated},
    )
    return {"ok": True, "updated_people": updated}


@mobile.get("/receipt-requests")
async def list_my_receipt_requests(
    request: Request, user: Authenticated
) -> dict[str, Any]:
    rows = await list_open_requests_for_user(_database(request), user)
    items = [await request_summary(_database(request), row) for row in rows]
    return {"items": json_safe(items), "total": len(items)}


@mobile.get("/receipt-requests/{request_id}")
async def get_my_receipt_request(
    request_id: str, request: Request, user: Authenticated
) -> dict[str, Any]:
    doc = await _load_owned_request(request, user, request_id)
    return json_safe(await request_summary(_database(request), doc))


@mobile.post("/receipt-files", status_code=status.HTTP_201_CREATED)
async def upload_early_receipt_files(
    request: Request,
    user: Authenticated,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    """Early upload with no open request — parks unmatched for desk linking."""
    if not files:
        raise ApiError(422, "files_required", "At least one file is required")
    _enforce_mobile_upload_limits(request, user, files)
    db = _database(request)
    base = await resolve_attachments_directory(request.app.state.settings, db)
    actor_id = _actor_id(user)
    saved: list[dict[str, Any]] = []
    for upload in files:
        filename = upload.filename or "file"
        claimed = upload.content_type
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
                source="expo_early",
                actor_id=actor_id,
                claimed_content_type=claimed,
            )
        except ValueError as exc:
            raise ApiError(422, "invalid_attachment", str(exc)) from None
        except OSError as exc:
            raise ApiError(500, "storage_error", "Failed to store attachment") from exc
        saved.append(meta)
    await record_operation_event(
        db,
        action="mobile.receipt_early_upload",
        actor=user,
        request=request,
        entity_type="receipt_file",
        entity_ids=[str(m.get("id")) for m in saved][:40],
        details={"files": len(saved)},
    )
    return {"files": json_safe(saved), "total": len(saved)}


@mobile.post(
    "/receipt-requests/{request_id}/files",
    status_code=status.HTTP_201_CREATED,
)
async def upload_receipt_files(
    request_id: str,
    request: Request,
    user: Authenticated,
    files: list[UploadFile] = File(...),
    partial: bool = Form(False),
    partial_note: str = Form(""),
) -> dict[str, Any]:
    if not files:
        raise ApiError(422, "files_required", "At least one file is required")
    note = " ".join((partial_note or "").strip().split())
    if partial and len(note) < 3:
        raise ApiError(
            422,
            "partial_note_required",
            "Partial upload needs a short note (3+ characters)",
        )
    _enforce_mobile_upload_limits(request, user, files)
    doc = await _load_owned_request(request, user, request_id)
    if doc.get("status") == "waived":
        raise ApiError(409, "waived", "This receipt request was waived")
    tx_id = doc.get("transaction_id")
    if not isinstance(tx_id, ObjectId):
        raise ApiError(500, "invalid_request", "Receipt request has no transaction")

    db = _database(request)
    base = await resolve_attachments_directory(request.app.state.settings, db)
    actor_id = _actor_id(user)
    saved: list[dict[str, Any]] = []
    for upload in files:
        filename = upload.filename or "file"
        claimed = upload.content_type
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
                source="expo",
                actor_id=actor_id,
                request_id=doc["_id"],
                transaction_id=tx_id,
                claimed_content_type=claimed,
            )
            attachment = await link_file_to_transaction(
                db,
                tx_id,
                meta,
                actor_id=actor_id,
                partial=bool(partial),
                partial_note=note,
            )
        except ValueError as exc:
            raise ApiError(422, "invalid_attachment", str(exc)) from None
        except LookupError as exc:
            raise ApiError(404, "not_found", str(exc)) from None
        except OSError as exc:
            raise ApiError(500, "storage_error", "Failed to store attachment") from exc

        s3_key = await mirror_attachment(
            request.app.state.settings, db, tx_id, base, attachment
        )
        if s3_key:
            attachment["s3_key"] = s3_key
            await db.receipt_files.update_one(
                {"_id": meta["id"]},
                {"$set": {"s3_key": s3_key}},
            )
            await db.transactions.update_one(
                {"_id": tx_id, "attachments.id": attachment["id"]},
                {"$set": {"attachments.$.s3_key": s3_key}},
            )
        saved.append(attachment)

    await record_operation_event(
        db,
        action="mobile.receipt_upload",
        actor=user,
        request=request,
        entity_type="receipt_request",
        entity_ids=[str(doc["_id"])],
        details={
            "files": len(saved),
            "transaction_id": str(tx_id),
            "partial": bool(partial),
        },
    )
    refreshed = await db.receipt_requests.find_one({"_id": doc["_id"]})
    return {
        "request": json_safe(await request_summary(db, refreshed or doc)),
        "files": json_safe(saved),
    }


async def _assert_mobile_can_open_file(
    db: Any, user: dict[str, Any], file_id: str
) -> dict[str, Any]:
    """Allow open when the file is on an owned request or uploaded by this user."""
    doc = await db.receipt_files.find_one({"_id": file_id})
    if not doc:
        raise ApiError(404, "not_found", "Receipt file not found")
    actor = _actor_id(user)
    if actor is not None and doc.get("uploaded_by") == actor:
        return doc
    req_id = doc.get("request_id")
    if isinstance(req_id, ObjectId):
        req = await db.receipt_requests.find_one({"_id": req_id})
        if req:
            targets = await ownership_targets_for_user(db, user)
            names = set(targets["names"])
            person_ids = set(targets["person_ids"])
            asked_name = str(req.get("asked_of_name") or "")
            asked_person = req.get("asked_of_person_id")
            if asked_name in names or (
                isinstance(asked_person, ObjectId) and asked_person in person_ids
            ):
                return doc
    # Also allow if linked tx ownership matches this user.
    for tx_id in doc.get("transaction_ids") or []:
        if not isinstance(tx_id, ObjectId):
            continue
        tx = await db.transactions.find_one({"_id": tx_id}, {"ownership": 1})
        if not tx:
            continue
        targets = await ownership_targets_for_user(db, user)
        if str(tx.get("ownership") or "") in set(targets["names"]):
            return doc
    raise ApiError(403, "forbidden", "You cannot open this receipt file")


@mobile.get("/receipt-files/{file_id}/meta")
async def mobile_receipt_file_meta(
    file_id: str, request: Request, user: Authenticated
) -> dict[str, Any]:
    db = _database(request)
    await _assert_mobile_can_open_file(db, user, file_id)
    base = await resolve_attachments_directory(request.app.state.settings, db)
    try:
        meta = await get_receipt_file_open_meta(db, base, file_id)
    except ValueError as exc:
        raise ApiError(404, "not_found", str(exc)) from None
    return json_safe(meta)


@mobile.get("/receipt-files/{file_id}/open")
async def mobile_receipt_file_open(
    file_id: str, request: Request, user: Authenticated
) -> FileResponse:
    db = _database(request)
    await _assert_mobile_can_open_file(db, user, file_id)
    base = await resolve_attachments_directory(request.app.state.settings, db)
    try:
        path, filename, content_type = await open_receipt_file_bytes(db, base, file_id)
    except ValueError as exc:
        raise ApiError(404, "not_found", str(exc)) from None
    except FileNotFoundError as exc:
        raise ApiError(404, "missing_file", str(exc)) from None
    return FileResponse(
        path=Path(path),
        filename=filename,
        media_type=content_type or "application/octet-stream",
    )


@mobile.websocket("/ws/receipts")
async def mobile_receipts_ws(websocket: WebSocket) -> None:
    """Push open-request id lists when they change (live Receipts tab).

    Auth: ``?token=<access_token>``. Polls ownership requests every few seconds
    and emits ``{"type":"open_requests","ids":[...],"total":N}`` on change.
    """
    from .security import decode_access_token

    token = (websocket.query_params.get("token") or "").strip()
    if not token:
        await websocket.close(code=4401)
        return
    settings = websocket.app.state.settings
    try:
        claims = decode_access_token(settings, token)
        user_id = claims.get("sub")
        user = await websocket.app.state.database.db.users.find_one(
            {"_id": object_id(str(user_id))}
        )
        if not user:
            await websocket.close(code=4401)
            return
    except Exception:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    db = websocket.app.state.database.db
    last_key: str | None = None
    try:
        while True:
            try:
                rows = await list_open_requests_for_user(db, user)
                ids = [str(r.get("_id")) for r in rows if r.get("_id") is not None]
                key = ",".join(ids)
                if key != last_key:
                    await websocket.send_json(
                        {"type": "open_requests", "ids": ids, "total": len(ids)}
                    )
                    last_key = key
            except Exception as exc:
                _log.warning("mobile receipts ws poll failed: %s", exc)
            await asyncio.sleep(4)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


@mobile.post("/receipt-requests/{request_id}/waive")
async def waive_my_receipt_request(
    request_id: str,
    body: ReceiptWaiveBody,
    request: Request,
    user: Authenticated,
) -> dict[str, Any]:
    doc = await _load_owned_request(request, user, request_id)
    if doc.get("status") == "received":
        raise ApiError(409, "already_received", "Receipt already received")
    try:
        updated = await waive_receipt_request(
            _database(request), doc, reason=body.reason, actor=user
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_waiver", str(exc)) from None
    await record_operation_event(
        _database(request),
        action="mobile.receipt_waive",
        actor=user,
        request=request,
        entity_type="receipt_request",
        entity_ids=[str(doc["_id"])],
        details={"reason": body.reason[:200]},
    )
    return json_safe(await request_summary(_database(request), updated or doc))
