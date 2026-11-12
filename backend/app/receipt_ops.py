"""Receipt requests, file records, and status sync (Phase 1)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from bson import ObjectId

from .attachment_ops import (
    MAX_ATTACHMENT_BYTES,
    allowed_extension,
    attachments_root,
    safe_name,
    staging_root,
)
from .config import Settings
from .receipt_upload_security import (
    UploadSecurityError,
    validate_receipt_upload,
)
from .security import utcnow

logger = logging.getLogger("tahmeed.receipts")

UTC = timezone.utc
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def normalize_person_name(value: str | None) -> str:
    return " ".join((value or "").upper().split())


def files_root(base: Path) -> Path:
    return attachments_root(base) / "files"


# Inbox lifecycle on receipt_files.inbox_status
INBOX_UNMATCHED = "unmatched"
INBOX_MATCHED = "matched"
INBOX_APPROVED = "approved"
INBOX_REJECTED = "rejected"
INBOX_STATUSES = frozenset(
    {INBOX_UNMATCHED, INBOX_MATCHED, INBOX_APPROVED, INBOX_REJECTED}
)
PARKED_STATUSES = (INBOX_UNMATCHED, INBOX_MATCHED)


def ensure_receipt_workspace(base: Path) -> dict[str, Path]:
    """Create canonical + parked / quarantine / approved folders under the disk root."""
    root = attachments_root(base)
    paths = {
        "root": root,
        "files": root / "files",
        "staging": staging_root(base),
        "parked": root / "parked",
        "parked_unmapped": root / "parked" / "unmapped",
        "quarantine": root / "quarantine",
        "approved": root / "approved",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def normalize_receipt_status(raw: Any) -> str:
    """Map UI labels to stored keys.

    Primary types: ``receipt`` (requires upload/notify) and ``no_receipt`` (skip).
    ``received`` = paper already attached. ``pending`` / ``missing`` are legacy.
    """
    text = " ".join(str(raw or "").strip().lower().replace("_", " ").split())
    if not text:
        return "pending"
    aliases = {
        "receipt": "receipt",
        "required": "receipt",
        "needs receipt": "receipt",
        "1": "receipt",
        "yes": "receipt",
        "received": "received",
        "rcvd": "received",
        "have receipt": "received",
        "pending": "pending",
        "0": "pending",
        "missing": "missing",
        "no receipt": "no_receipt",
        "no_receipt": "no_receipt",
        "none": "no_receipt",
        "n/a": "no_receipt",
        "na": "no_receipt",
    }
    valid = {"receipt", "pending", "received", "missing", "no_receipt"}
    return aliases.get(text, text if text in valid else "pending")


async def tx_needs_receipt_request(db: Any, tx: dict[str, Any]) -> bool:
    """Whether saving this row should open/keep a chase request.

    - ``no_receipt`` → skip notify (transaction does not require a receipt).
    - ``received`` → already have paper → no chase.
    - ``receipt`` → requires a receipt → chase when ownership/category says so.
    - ``pending`` / ``missing`` → legacy; still chase (desk should clarify to RECEIPT/NO RECEIPT).
    """
    status = normalize_receipt_status(tx.get("receipt_status"))
    if status in ("received", "no_receipt"):
        return False
    if await category_requires_receipt(db, tx):
        return True
    ownership = normalize_person_name(str(tx.get("ownership") or ""))
    return bool(ownership) and status in ("receipt", "pending", "missing")


def guess_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in _CONTENT_TYPES:
        return _CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


async def category_requires_receipt(db: Any, tx: dict[str, Any]) -> bool:
    category_id = tx.get("category_id")
    if isinstance(category_id, ObjectId):
        doc = await db.categories.find_one({"_id": category_id}, {"requires_receipt": 1})
        if doc is not None:
            return bool(doc.get("requires_receipt"))
    if category_id:
        try:
            oid = ObjectId(str(category_id))
        except Exception:
            oid = None
        if oid is not None:
            doc = await db.categories.find_one({"_id": oid}, {"requires_receipt": 1})
            if doc is not None:
                return bool(doc.get("requires_receipt"))
    name = (tx.get("category_name") or tx.get("item") or "").strip()
    if not name:
        return False
    doc = await db.categories.find_one({"name": name}, {"requires_receipt": 1})
    if doc is None and name.upper() != name:
        doc = await db.categories.find_one({"name": name.upper()}, {"requires_receipt": 1})
    return bool((doc or {}).get("requires_receipt"))


async def find_person_for_ownership(db: Any, ownership: str) -> dict[str, Any] | None:
    name = normalize_person_name(ownership)
    if not name:
        return None
    return await db.people.find_one({"name": name})


async def ensure_receipt_request(db: Any, tx: dict[str, Any]) -> dict[str, Any] | None:
    """Open a needed request when the row should be chased and none is linked.

    Idempotent per transaction. Does not reopen an explicit waiver. Does not
    open a request when the row already has attachment files.
    Sets ``immediate_notify_pending`` when ownership is present so save can
    push without blocking on Expo.
    """
    tx_id = tx.get("_id")
    if not isinstance(tx_id, ObjectId):
        return None
    if not await tx_needs_receipt_request(db, tx):
        return None

    existing = await db.receipt_requests.find_one({"transaction_id": tx_id})
    attachments = list(tx.get("attachments") or [])
    has_files = bool(attachments)
    ownership = normalize_person_name(str(tx.get("ownership") or ""))
    person = await find_person_for_ownership(db, ownership) if ownership else None
    person_id = person.get("_id") if person else None
    now = utcnow()

    if existing:
        if existing.get("status") == "waived":
            return existing
        if has_files:
            if existing.get("status") != "received":
                await db.receipt_requests.update_one(
                    {"_id": existing["_id"]},
                    {
                        "$set": {
                            "status": "received",
                            "asked_of_name": ownership or existing.get("asked_of_name") or "",
                            "asked_of_person_id": person_id,
                            "immediate_notify_pending": False,
                            "updated_at": now,
                        }
                    },
                )
                existing = await db.receipt_requests.find_one({"_id": existing["_id"]})
            return existing
        prev_name = normalize_person_name(str(existing.get("asked_of_name") or ""))
        updates: dict[str, Any] = {
            "asked_of_name": ownership,
            "asked_of_person_id": person_id,
            "updated_at": now,
        }
        if existing.get("status") != "needed":
            updates["status"] = "needed"
        if ownership and (
            not existing.get("initial_notified_at")
            or prev_name != ownership
            or existing.get("status") != "needed"
        ):
            updates["immediate_notify_pending"] = True
        await db.receipt_requests.update_one({"_id": existing["_id"]}, {"$set": updates})
        return await db.receipt_requests.find_one({"_id": existing["_id"]})

    if has_files:
        return None

    doc = {
        "transaction_id": tx_id,
        "asked_of_name": ownership,
        "asked_of_person_id": person_id,
        "status": "needed",
        "opened_at": now,
        "updated_at": now,
        "last_reminded_at": None,
        "reminder_count": 0,
        "initial_notified_at": None,
        "immediate_notify_pending": bool(ownership),
        "waiver_reason": None,
        "waived_by": None,
        "waived_at": None,
        "file_ids": [],
    }
    try:
        result = await db.receipt_requests.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc
    except Exception:
        # Unique race: another save opened it.
        return await db.receipt_requests.find_one({"transaction_id": tx_id})


async def mark_request_received(
    db: Any,
    tx_id: ObjectId,
    *,
    file_id: str | None = None,
) -> None:
    update: dict[str, Any] = {
        "$set": {
            "status": "received",
            "immediate_notify_pending": False,
            "updated_at": utcnow(),
        },
    }
    if file_id:
        update["$addToSet"] = {"file_ids": file_id}
    # Only stamp received_notify_pending when first transitioning to received.
    existing = await db.receipt_requests.find_one(
        {"transaction_id": tx_id, "status": {"$ne": "waived"}},
        {"status": 1, "received_notified_at": 1},
    )
    was_needed = bool(existing) and str(existing.get("status") or "") == "needed"
    if was_needed and not existing.get("received_notified_at"):
        update["$set"]["received_notify_pending"] = True
    await db.receipt_requests.update_one(
        {"transaction_id": tx_id, "status": {"$ne": "waived"}},
        update,
    )
    if was_needed and not (existing or {}).get("received_notified_at"):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _bg() -> None:
            try:
                await flush_received_receipt_notifies(db, tx_ids=[tx_id])
            except Exception as exc:
                logger.warning("received notify failed for %s: %s", tx_id, exc)

        loop.create_task(_bg())


async def sync_receipt_status_after_attach(db: Any, tx_id: ObjectId) -> None:
    """Attach/link → received unless the row was explicitly marked missing/waived."""
    doc = await db.transactions.find_one(
        {"_id": tx_id}, {"receipt_status": 1, "attachments": 1, "ownership": 1, "category_id": 1, "category_name": 1, "item": 1}
    )
    if not doc:
        return
    status = str(doc.get("receipt_status") or "pending").strip().lower()
    if status == "missing":
        # Explicit missing / waiver path stays until cleared.
        request = await db.receipt_requests.find_one({"transaction_id": tx_id})
        if request and request.get("status") == "waived":
            return
    await db.transactions.update_one(
        {"_id": tx_id},
        {"$set": {"receipt_status": "received"}},
    )
    await mark_request_received(db, tx_id)


async def sync_receipt_status_after_detach(db: Any, tx_id: ObjectId) -> None:
    """Last unlink → receipt (still required); honor explicit missing / waiver."""
    doc = await db.transactions.find_one(
        {"_id": tx_id},
        {
            "receipt_status": 1,
            "attachments": 1,
            "ownership": 1,
            "category_id": 1,
            "category_name": 1,
            "item": 1,
        },
    )
    if not doc:
        return
    status = str(doc.get("receipt_status") or "pending").strip().lower()
    remaining = list(doc.get("attachments") or [])
    if remaining:
        return
    request = await db.receipt_requests.find_one({"transaction_id": tx_id})
    if status == "missing" or (request and request.get("status") == "waived"):
        return
    await db.transactions.update_one(
        {"_id": tx_id},
        {"$set": {"receipt_status": "receipt"}},
    )
    if request and request.get("status") != "waived":
        if await category_requires_receipt(db, doc):
            await db.receipt_requests.update_one(
                {"_id": request["_id"]},
                {"$set": {"status": "needed", "updated_at": utcnow()}},
            )


async def register_receipt_file_for_attachment(
    db: Any,
    *,
    tx_id: ObjectId,
    meta: dict[str, Any],
    base: Path,
    source: str,
    actor_id: ObjectId | None = None,
    request_id: ObjectId | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Persist a receipt_files row for an attachment already on disk."""
    file_id = str(meta.get("id") or uuid.uuid4().hex)
    stored_name = str(meta.get("stored_name") or "")
    if stored_name.startswith("files/") or stored_name.startswith("files\\"):
        stored_path = stored_name.replace("\\", "/")
    else:
        stored_path = f"{tx_id}/{stored_name}" if stored_name else ""
    filename = str(meta.get("filename") or "file")
    doc = {
        "_id": file_id,
        "original_filename": filename,
        "content_type": content_type or guess_content_type(filename),
        "size": int(meta.get("size") or 0),
        "sha256": str(meta.get("sha256") or ""),
        "stored_path": stored_path,
        "s3_key": str(meta.get("s3_key") or ""),
        "uploaded_at": meta.get("uploaded_at") or utcnow(),
        "uploaded_by": actor_id,
        "source": source,
        "inbox_status": "matched",
        "request_id": request_id,
        "transaction_ids": [tx_id],
    }
    await db.receipt_files.update_one(
        {"_id": file_id},
        {"$set": doc},
        upsert=True,
    )
    return doc


async def quarantine_rejected_upload(
    db: Any,
    base: Path,
    *,
    filename: str,
    content: bytes,
    reason: str,
    source: str,
    actor_id: ObjectId | None = None,
    code: str = "upload_rejected",
) -> dict[str, Any]:
    """Keep bad bytes out of ``files/``; land them in quarantine/ for desk review."""
    workspace = ensure_receipt_workspace(base)
    file_id = uuid.uuid4().hex
    safe = safe_name(Path(filename).name or "rejected.bin")
    stored_name = f"{file_id}_{safe}"
    dest = workspace["quarantine"] / stored_name
    try:
        dest.write_bytes(content[: MAX_ATTACHMENT_BYTES + 1])
    except OSError:
        dest = workspace["quarantine"] / f"{file_id}.bin"
        try:
            dest.write_bytes(content[:1024])
        except OSError:
            pass
    now = utcnow()
    relative = f"quarantine/{dest.name}"
    doc = {
        "_id": file_id,
        "original_filename": Path(filename).name or "rejected",
        "content_type": "application/octet-stream",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest() if content else "",
        "stored_path": "",
        "quarantine_path": relative,
        "s3_key": "",
        "uploaded_at": now,
        "uploaded_by": actor_id,
        "source": source,
        "inbox_status": INBOX_REJECTED,
        "request_id": None,
        "transaction_ids": [],
        "rejection_reason": f"security:{code}: {reason}"[:500],
        "rejected_at": now,
        "rejected_by": None,
        "security_rejected": True,
        "approved_at": None,
        "approved_by": None,
    }
    await db.receipt_files.insert_one(doc)
    return {"id": file_id, "quarantine_path": relative, "reason": reason, "code": code}


async def store_receipt_file_bytes(
    db: Any,
    base: Path,
    *,
    filename: str,
    content: bytes,
    source: str,
    actor_id: ObjectId | None = None,
    request_id: ObjectId | None = None,
    transaction_id: ObjectId | None = None,
    claimed_content_type: str | None = None,
) -> dict[str, Any]:
    """Write bytes under ``files/<id>_<name>`` and insert ``receipt_files``."""
    try:
        checked = validate_receipt_upload(
            filename, content, claimed_content_type=claimed_content_type
        )
    except UploadSecurityError as exc:
        await quarantine_rejected_upload(
            db,
            base,
            filename=filename,
            content=content or b"",
            reason=str(exc),
            source=source,
            actor_id=actor_id,
            code=getattr(exc, "code", "upload_rejected"),
        )
        raise ValueError(str(exc)) from exc

    original_name = checked["filename"]
    content = checked["content"]
    content_type = checked["content_type"]

    file_id = uuid.uuid4().hex
    stored_name = f"{file_id}_{safe_name(original_name)}"
    relative = f"files/{stored_name}"
    workspace = ensure_receipt_workspace(base)
    stage_dir = workspace["staging"]
    staging = stage_dir / f"receipt_{file_id}.part"
    dest_dir = workspace["files"]
    dest = dest_dir / stored_name
    checksum = hashlib.sha256(content).hexdigest()

    try:
        staging.write_bytes(content)
        os.replace(staging, dest)
    except OSError:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    meta = {
        "id": file_id,
        "filename": original_name,
        "stored_name": relative,
        "stored_path": relative,
        "size": dest.stat().st_size,
        "sha256": checksum,
        "s3_key": "",
        "uploaded_at": utcnow(),
        "content_type": content_type,
    }
    if actor_id is not None:
        meta["uploaded_by"] = actor_id

    doc = {
        "_id": file_id,
        "original_filename": original_name,
        "content_type": content_type,
        "size": meta["size"],
        "sha256": checksum,
        "stored_path": relative,
        "s3_key": "",
        "uploaded_at": meta["uploaded_at"],
        "uploaded_by": actor_id,
        "source": source,
        "inbox_status": INBOX_MATCHED if transaction_id else INBOX_UNMATCHED,
        "request_id": request_id,
        "transaction_ids": [transaction_id] if transaction_id else [],
        "approved_at": None,
        "approved_by": None,
        "rejected_at": None,
        "rejected_by": None,
        "rejection_reason": None,
        "security_rejected": False,
    }
    await db.receipt_files.insert_one(doc)
    return meta


async def link_file_to_transaction(
    db: Any,
    tx_id: ObjectId,
    meta: dict[str, Any],
    *,
    actor_id: ObjectId | None = None,
    partial: bool = False,
    partial_note: str = "",
) -> dict[str, Any]:
    """Push attachment metadata onto the row (desktop-compatible).

    Full link marks the open request received. Partial keeps chase open
    (``needed``) but records the note and ``receipt_status=partial``.
    """
    attachment = {
        "id": meta["id"],
        "filename": meta.get("filename") or meta.get("original_filename") or "file",
        "stored_name": meta.get("stored_name") or meta.get("stored_path") or "",
        "size": int(meta.get("size") or 0),
        "sha256": str(meta.get("sha256") or ""),
        "s3_key": str(meta.get("s3_key") or ""),
        "uploaded_at": meta.get("uploaded_at") or utcnow(),
        "file_id": meta.get("id"),
    }
    if actor_id is not None:
        attachment["uploaded_by"] = actor_id
    if partial:
        attachment["partial"] = True
        note = " ".join((partial_note or "").strip().split())[:500]
        if note:
            attachment["partial_note"] = note
    result = await db.transactions.update_one(
        {"_id": tx_id},
        {"$push": {"attachments": attachment}},
    )
    if result.matched_count == 0:
        raise LookupError("Transaction not found — attachment not saved.")
    file_id = str(meta["id"])
    if partial:
        note = " ".join((partial_note or "").strip().split())[:500]
        now = utcnow()
        await db.transactions.update_one(
            {"_id": tx_id},
            {
                "$set": {
                    "receipt_status": "partial",
                    "receipt_partial_note": note or None,
                }
            },
        )
        req_set: dict[str, Any] = {
            "partial_receipt": True,
            "updated_at": now,
        }
        if note:
            req_set["partial_note"] = note
        await db.receipt_requests.update_one(
            {"transaction_id": tx_id, "status": {"$ne": "waived"}},
            {"$set": req_set, "$addToSet": {"file_ids": file_id}},
        )
    else:
        await sync_receipt_status_after_attach(db, tx_id)
        await mark_request_received(db, tx_id, file_id=file_id)
    return attachment


async def waive_receipt_request(
    db: Any,
    request_doc: dict[str, Any],
    *,
    reason: str,
    actor: dict[str, Any] | None,
) -> dict[str, Any]:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A waiver reason is required")
    now = utcnow()
    actor_id = actor.get("_id") if actor else None
    await db.receipt_requests.update_one(
        {"_id": request_doc["_id"]},
        {
            "$set": {
                "status": "waived",
                "waiver_reason": reason,
                "waived_by": actor_id,
                "waived_at": now,
                "updated_at": now,
            }
        },
    )
    tx_id = request_doc.get("transaction_id")
    if isinstance(tx_id, ObjectId):
        await db.transactions.update_one(
            {"_id": tx_id},
            {"$set": {"receipt_status": "missing"}},
        )
    return await db.receipt_requests.find_one({"_id": request_doc["_id"]})


async def ownership_targets_for_user(db: Any, user: dict[str, Any]) -> dict[str, Any]:
    """Names / person ids whose open requests this login may see."""
    user_id = user.get("_id")
    names: set[str] = set()
    person_ids: list[ObjectId] = []
    full_name = normalize_person_name(str(user.get("full_name") or ""))
    if full_name:
        names.add(full_name)
    username = normalize_person_name(str(user.get("username") or ""))
    if username:
        names.add(username)

    clauses: list[dict[str, Any]] = []
    if isinstance(user_id, ObjectId):
        clauses.extend([{"user_id": user_id}, {"user_id": str(user_id)}])
    if names:
        clauses.append({"name": {"$in": list(names)}})
    people: list[dict[str, Any]] = []
    if clauses:
        people = await db.people.find({"$or": clauses}).to_list(length=200)

    for person in people or []:
        pname = normalize_person_name(str(person.get("name") or ""))
        if pname:
            names.add(pname)
        pid = person.get("_id")
        if isinstance(pid, ObjectId):
            person_ids.append(pid)
    return {"names": sorted(names), "person_ids": person_ids}


async def list_open_requests_for_user(
    db: Any, user: dict[str, Any], *, limit: int = 100
) -> list[dict[str, Any]]:
    targets = await ownership_targets_for_user(db, user)
    names = targets["names"]
    person_ids = targets["person_ids"]
    if not names and not person_ids:
        return []
    clauses: list[dict[str, Any]] = []
    if names:
        clauses.append({"asked_of_name": {"$in": names}})
    if person_ids:
        clauses.append({"asked_of_person_id": {"$in": person_ids}})
    cursor = (
        db.receipt_requests.find({"status": "needed", "$or": clauses})
        .sort("opened_at", 1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def request_summary(db: Any, request_doc: dict[str, Any]) -> dict[str, Any]:
    tx = None
    tx_id = request_doc.get("transaction_id")
    if isinstance(tx_id, ObjectId):
        tx = await db.transactions.find_one(
            {"_id": tx_id},
            {
                "date": 1,
                "amount": 1,
                "currency": 1,
                "item": 1,
                "category_name": 1,
                "payee": 1,
                "ownership": 1,
                "receipt_status": 1,
                "description": 1,
                "truck_number": 1,
            },
        )
    return {
        "id": str(request_doc.get("_id")),
        "status": request_doc.get("status"),
        "asked_of_name": request_doc.get("asked_of_name") or "",
        "opened_at": request_doc.get("opened_at"),
        "last_reminded_at": request_doc.get("last_reminded_at"),
        "reminder_count": int(request_doc.get("reminder_count") or 0),
        "file_ids": list(request_doc.get("file_ids") or []),
        "waiver_reason": request_doc.get("waiver_reason"),
        "partial_receipt": bool(request_doc.get("partial_receipt")),
        "partial_note": request_doc.get("partial_note") or "",
        "transaction": {
            "id": str(tx_id) if tx_id else None,
            "date": (tx or {}).get("date"),
            "amount": (tx or {}).get("amount"),
            "currency": (tx or {}).get("currency") or "TZS",
            "item": (tx or {}).get("item") or (tx or {}).get("category_name") or "",
            "payee": (tx or {}).get("payee") or "",
            "ownership": (tx or {}).get("ownership") or "",
            "receipt_status": (tx or {}).get("receipt_status") or "pending",
            "description": (tx or {}).get("description") or "",
            "truck_number": (tx or {}).get("truck_number") or "",
        },
    }


async def register_expo_push_token(db: Any, user: dict[str, Any], token: str) -> int:
    token = (token or "").strip()
    if not token:
        raise ValueError("expo_push_token is required")
    targets = await ownership_targets_for_user(db, user)
    names = targets["names"]
    user_id = user.get("_id")
    updated = 0
    if isinstance(user_id, ObjectId):
        result = await db.people.update_many(
            {"$or": [{"user_id": user_id}, {"user_id": str(user_id)}, {"name": {"$in": names}}]},
            {
                "$addToSet": {"expo_push_tokens": token},
                "$set": {"receives_receipt_requests": True},
            },
        )
        updated = int(getattr(result, "modified_count", 0) or 0)
    if updated == 0 and names:
        # Ensure at least one people row can receive tokens for this login.
        primary = names[0]
        await db.people.update_one(
            {"name": primary},
            {
                "$set": {
                    "name": primary,
                    "active": True,
                    "user_id": user_id,
                    "receives_receipt_requests": True,
                },
                "$addToSet": {"expo_push_tokens": token},
                "$setOnInsert": {"phone": ""},
            },
            upsert=True,
        )
        updated = 1
    return updated


def _post_expo_push(messages: list[dict[str, Any]]) -> dict[str, Any]:
    payload = json.dumps(messages).encode("utf-8")
    req = urlrequest.Request(
        EXPO_PUSH_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=20) as response:
        body = response.read().decode("utf-8", errors="replace")
        return json.loads(body) if body else {}


async def _resolve_request_person(
    db: Any, request_doc: dict[str, Any]
) -> dict[str, Any] | None:
    person = None
    person_id = request_doc.get("asked_of_person_id")
    if isinstance(person_id, ObjectId):
        person = await db.people.find_one({"_id": person_id})
    if person is None:
        name = normalize_person_name(str(request_doc.get("asked_of_name") or ""))
        if name:
            person = await db.people.find_one({"name": name})
    if not person or person.get("receives_receipt_requests") is False:
        return None
    return person


async def push_receipt_request_to_owner(
    db: Any,
    request_doc: dict[str, Any],
    *,
    title: str = "Receipt needed",
    body_prefix: str = "",
    mark_initial: bool = True,
) -> str:
    """Send Expo push for one request. Returns ``sent`` | ``skipped`` | ``failed``."""
    person = await _resolve_request_person(db, request_doc)
    if not person:
        return "skipped"
    tokens = [
        str(t).strip()
        for t in (person.get("expo_push_tokens") or [])
        if str(t).strip()
    ]
    if not tokens:
        return "skipped"
    summary = await request_summary(db, request_doc)
    tx = summary.get("transaction") or {}
    day = ""
    raw_date = tx.get("date")
    if raw_date is not None:
        day = str(raw_date)[:10]
    item = tx.get("item") or "Expense"
    amount = f"{tx.get('amount') or ''} {tx.get('currency') or ''}".strip()
    body_parts = [p for p in (body_prefix, day, item, amount) if p]
    body = (
        " · ".join(body_parts)
        if body_parts
        else ("Receipt uploaded" if title.lower().startswith("receipt received") else "Please upload your receipt")
    )
    kind = "received" if "received" in title.lower() else "needed"
    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            "priority": "high",
            "channelId": "receipts",
            "data": {
                "request_id": summary["id"],
                "transaction_id": tx.get("id"),
                "kind": kind,
            },
        }
        for token in tokens
    ]
    try:
        await asyncio.to_thread(_post_expo_push, messages)
        now = utcnow()
        fields: dict[str, Any] = {
            "immediate_notify_pending": False,
            "updated_at": now,
        }
        if mark_initial:
            fields["last_reminded_at"] = now
            fields["initial_notified_at"] = request_doc.get("initial_notified_at") or now
        update: dict[str, Any] = {"$set": fields}
        if mark_initial:
            update["$inc"] = {"reminder_count": 1}
        await db.receipt_requests.update_one({"_id": request_doc["_id"]}, update)
        return "sent"
    except (urlerror.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("receipt push failed for %s: %s", request_doc.get("_id"), exc)
        return "failed"


async def flush_received_receipt_notifies(
    db: Any,
    *,
    limit: int = 100,
    tx_ids: list[ObjectId] | None = None,
) -> dict[str, int]:
    """Push ``Receipt received`` when desk/mobile closes a needed request."""
    query: dict[str, Any] = {
        "status": "received",
        "received_notify_pending": True,
    }
    if tx_ids:
        query["transaction_id"] = {"$in": list(tx_ids)}
    cursor = db.receipt_requests.find(query).limit(limit)
    sent = skipped = failed = 0
    async for request_doc in cursor:
        result = await push_receipt_request_to_owner(
            db,
            request_doc,
            title="Receipt received",
            body_prefix="Uploaded for you",
            mark_initial=False,
        )
        now = utcnow()
        if result == "sent":
            await db.receipt_requests.update_one(
                {"_id": request_doc["_id"]},
                {
                    "$set": {
                        "received_notify_pending": False,
                        "received_notified_at": now,
                        "updated_at": now,
                    }
                },
            )
            sent += 1
        elif result == "failed":
            failed += 1
        else:
            await db.receipt_requests.update_one(
                {"_id": request_doc["_id"]},
                {
                    "$set": {
                        "received_notify_pending": False,
                        "updated_at": now,
                    }
                },
            )
            skipped += 1
    return {"sent": sent, "skipped": skipped, "failed": failed}


async def flush_immediate_receipt_notifies(
    db: Any,
    *,
    limit: int = 100,
    request_ids: list[Any] | None = None,
) -> dict[str, int]:
    """Drain ``immediate_notify_pending`` flags (save-path + scheduler)."""
    query: dict[str, Any] = {
        "status": "needed",
        "immediate_notify_pending": True,
    }
    if request_ids:
        oids: list[ObjectId] = []
        for raw in request_ids:
            if isinstance(raw, ObjectId):
                oids.append(raw)
            else:
                try:
                    oids.append(ObjectId(str(raw)))
                except Exception:
                    continue
        if not oids:
            return {"sent": 0, "skipped": 0, "failed": 0}
        query["_id"] = {"$in": oids}
    cursor = db.receipt_requests.find(query).limit(limit)
    sent = skipped = failed = 0
    async for request_doc in cursor:
        result = await push_receipt_request_to_owner(
            db, request_doc, title="Receipt needed"
        )
        if result == "sent":
            sent += 1
        elif result == "failed":
            failed += 1
        else:
            # No tokens / opted out — clear pending so we do not spin forever.
            await db.receipt_requests.update_one(
                {"_id": request_doc["_id"]},
                {
                    "$set": {
                        "immediate_notify_pending": False,
                        "updated_at": utcnow(),
                    }
                },
            )
            skipped += 1
    return {"sent": sent, "skipped": skipped, "failed": failed}


async def send_receipt_reminders(
    settings: Settings,
    db: Any,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Push Expo reminders for needed requests past the threshold."""
    # Always drain any save-time / received notifies that never got a worker.
    immediate = await flush_immediate_receipt_notifies(db, limit=limit)
    received = await flush_received_receipt_notifies(db, limit=limit)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    threshold = timedelta(hours=max(1, int(settings.receipt_reminder_hours)))
    cutoff = now - threshold
    # Compare both aware and naive opened_at / last_reminded_at stored values.
    cutoff_naive = cutoff.replace(tzinfo=None)
    cursor = db.receipt_requests.find(
        {
            "status": "needed",
            "immediate_notify_pending": {"$ne": True},
            "$or": [
                {"last_reminded_at": None, "opened_at": {"$lte": cutoff_naive}},
                {"last_reminded_at": None, "opened_at": {"$lte": cutoff}},
                {"last_reminded_at": {"$lte": cutoff_naive}},
                {"last_reminded_at": {"$lte": cutoff}},
            ],
        }
    ).limit(limit)
    reminded = 0
    skipped = 0
    failed = 0
    async for request_doc in cursor:
        result = await push_receipt_request_to_owner(db, request_doc)
        if result == "sent":
            reminded += 1
        elif result == "failed":
            failed += 1
        else:
            skipped += 1
    return {
        "reminded": reminded,
        "skipped": skipped,
        "failed": failed,
        "immediate_sent": int(immediate.get("sent") or 0),
        "immediate_failed": int(immediate.get("failed") or 0),
        "received_sent": int(received.get("sent") or 0),
        "received_failed": int(received.get("failed") or 0),
    }


def _age_hours(opened_at: Any, now: datetime) -> float | None:
    if opened_at is None:
        return None
    if isinstance(opened_at, datetime):
        value = opened_at
    else:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    ref = now if now.tzinfo else now.replace(tzinfo=UTC)
    return max(0.0, (ref - value).total_seconds() / 3600.0)


def _today_start_naive(now: datetime) -> datetime:
    aware = now if now.tzinfo else now.replace(tzinfo=UTC)
    start = datetime(aware.year, aware.month, aware.day, tzinfo=UTC)
    return start.replace(tzinfo=None)


async def _tx_lookup(db: Any, tx_ids: list[ObjectId]) -> dict[ObjectId, dict[str, Any]]:
    if not tx_ids:
        return {}
    docs = await db.transactions.find(
        {"_id": {"$in": tx_ids}},
        {
            "date": 1,
            "amount": 1,
            "currency": 1,
            "item": 1,
            "category_name": 1,
            "payee": 1,
            "ownership": 1,
            "receipt_status": 1,
        },
    ).to_list(length=None)
    return {doc["_id"]: doc for doc in docs if isinstance(doc.get("_id"), ObjectId)}


def _serialize_request_row(
    row: dict[str, Any],
    *,
    now: datetime,
    tx_map: dict[ObjectId, dict[str, Any]],
) -> dict[str, Any]:
    tid = row.get("transaction_id")
    tx = tx_map.get(tid) if isinstance(tid, ObjectId) else None
    age = _age_hours(row.get("opened_at"), now)
    return {
        "id": str(row.get("_id")),
        "status": str(row.get("status") or ""),
        "ownership": normalize_person_name(str(row.get("asked_of_name") or ""))
        or "(blank)",
        "opened_at": row.get("opened_at"),
        "age_hours": age,
        "last_reminded_at": row.get("last_reminded_at"),
        "reminder_count": int(row.get("reminder_count") or 0),
        "transaction_id": str(tid) if tid is not None else None,
        "amount": (tx or {}).get("amount"),
        "currency": (tx or {}).get("currency") or "TZS",
        "item": (tx or {}).get("item") or (tx or {}).get("category_name") or "",
        "payee": (tx or {}).get("payee") or "",
        "date": (tx or {}).get("date"),
        "receipt_status": (tx or {}).get("receipt_status") or "",
        "waiver_reason": row.get("waiver_reason") or "",
    }


async def list_receipt_requests(
    db: Any,
    *,
    preset: str = "open",
    ownership: str = "",
    overdue_hours: float = 48.0,
    reminder_threshold: int = 3,
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Desk request list with presets: today / open / overdue / received / waived / all."""
    now = datetime.now(UTC)
    key = (preset or "open").strip().lower()
    query: dict[str, Any] = {}
    if key == "today":
        query["opened_at"] = {"$gte": _today_start_naive(now)}
    elif key in {"open", "needed"}:
        query["status"] = "needed"
    elif key == "received":
        query["status"] = "received"
    elif key == "waived":
        query["status"] = "waived"
    elif key == "overdue":
        query["status"] = "needed"
    elif key == "heavy_reminders":
        query["status"] = "needed"
        query["reminder_count"] = {"$gte": max(1, int(reminder_threshold))}
    # "all" → no status filter

    name = normalize_person_name(ownership)
    if name:
        query["asked_of_name"] = name

    cursor = db.receipt_requests.find(query).sort("opened_at", -1).limit(max(limit * 3, limit))
    rows = await cursor.to_list(length=max(limit * 3, limit))
    tx_ids = [
        row["transaction_id"]
        for row in rows
        if isinstance(row.get("transaction_id"), ObjectId)
    ]
    tx_map = await _tx_lookup(db, tx_ids)
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _serialize_request_row(row, now=now, tx_map=tx_map)
        if key == "overdue":
            if (item.get("age_hours") or 0) < float(overdue_hours):
                continue
        items.append(item)
        if len(items) >= limit:
            break
    return items


async def receipt_compliance_summary(
    db: Any,
    *,
    overdue_hours: float = 48.0,
    reminder_threshold: int = 3,
) -> dict[str, Any]:
    """One-shot KPI counts for the Receipts desk header."""
    now = datetime.now(UTC)
    today_start = _today_start_naive(now)
    threshold = max(1, int(reminder_threshold))
    overdue_h = float(overdue_hours)

    open_rows = await db.receipt_requests.find({"status": "needed"}).to_list(length=8000)
    today_requested = await db.receipt_requests.count_documents(
        {"opened_at": {"$gte": today_start}}
    )
    received = await db.receipt_requests.count_documents({"status": "received"})
    waived = await db.receipt_requests.count_documents({"status": "waived"})
    unmatched = await db.receipt_files.count_documents(
        {"inbox_status": INBOX_UNMATCHED}
    )
    matched_pending = await db.receipt_files.count_documents(
        {"inbox_status": INBOX_MATCHED}
    )
    approved_files = await db.receipt_files.count_documents(
        {"inbox_status": INBOX_APPROVED}
    )
    rejected_files = await db.receipt_files.count_documents(
        {"inbox_status": INBOX_REJECTED}
    )
    parked = int(unmatched) + int(matched_pending)

    still_needed = 0
    overdue = 0
    heavy = 0
    for row in open_rows:
        still_needed += 1
        age = _age_hours(row.get("opened_at"), now) or 0.0
        if age >= overdue_h:
            overdue += 1
        if int(row.get("reminder_count") or 0) >= threshold:
            heavy += 1

    return {
        "today_requested": int(today_requested),
        "still_needed": still_needed,
        "received": int(received),
        "waived": int(waived),
        "overdue": overdue,
        "heavy_reminders": heavy,
        "unmatched_uploads": int(unmatched),
        "matched_pending": int(matched_pending),
        "parked_uploads": parked,
        "approved_files": int(approved_files),
        "rejected_files": int(rejected_files),
        "overdue_hours": overdue_h,
        "reminder_threshold": threshold,
        "as_of": now,
    }


async def chase_groups(db: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    """Open needed requests grouped by ownership for the desktop chase list."""
    now = datetime.now(UTC)
    cursor = db.receipt_requests.find({"status": "needed"}).sort("opened_at", 1)
    rows = await cursor.to_list(length=5000)
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = normalize_person_name(str(row.get("asked_of_name") or "")) or "(blank)"
        bucket = groups.get(name)
        opened = row.get("opened_at")
        last = row.get("last_reminded_at")
        age = _age_hours(opened, now)
        if bucket is None:
            groups[name] = {
                "ownership": name,
                "open_count": 1,
                "oldest_opened_at": opened,
                "oldest_age_hours": age,
                "last_reminded_at": last,
                "reminder_count": int(row.get("reminder_count") or 0),
                "request_ids": [str(row.get("_id"))],
                "transaction_ids": [str(row.get("transaction_id"))]
                if row.get("transaction_id")
                else [],
            }
            continue
        bucket["open_count"] += 1
        bucket["request_ids"].append(str(row.get("_id")))
        if row.get("transaction_id"):
            bucket["transaction_ids"].append(str(row["transaction_id"]))
        bucket["reminder_count"] = max(
            int(bucket.get("reminder_count") or 0), int(row.get("reminder_count") or 0)
        )
        if opened is not None and (
            bucket.get("oldest_opened_at") is None or opened < bucket["oldest_opened_at"]
        ):
            bucket["oldest_opened_at"] = opened
            bucket["oldest_age_hours"] = age
        if last is not None and (
            bucket.get("last_reminded_at") is None or last > bucket["last_reminded_at"]
        ):
            bucket["last_reminded_at"] = last
    ordered = sorted(
        groups.values(),
        key=lambda g: (
            -(g.get("oldest_age_hours") or 0),
            -int(g.get("open_count") or 0),
            str(g.get("ownership") or ""),
        ),
    )
    return ordered[:limit]


async def list_inbox_files(
    db: Any,
    *,
    status: str = "parked",
    unmatched_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List receipt files. ``parked`` = unmatched + matched (not yet permanent)."""
    key = (status or "parked").strip().lower()
    if unmatched_only:
        key = INBOX_UNMATCHED
    query: dict[str, Any]
    if key == "parked":
        query = {"inbox_status": {"$in": list(PARKED_STATUSES)}}
    elif key == "quarantine":
        # Security fail-closed uploads (never entered files/).
        query = {"security_rejected": True}
    elif key == "all":
        query = {}
    elif key in INBOX_STATUSES:
        query = {"inbox_status": key}
    else:
        query = {"inbox_status": {"$in": list(PARKED_STATUSES)}}
    cursor = db.receipt_files.find(query).sort("uploaded_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    items: list[dict[str, Any]] = []
    for doc in docs:
        items.append(
            {
                "id": str(doc.get("_id")),
                "original_filename": doc.get("original_filename") or "",
                "content_type": doc.get("content_type") or "",
                "size": int(doc.get("size") or 0),
                "sha256": doc.get("sha256") or "",
                "stored_path": doc.get("stored_path") or "",
                "s3_key": doc.get("s3_key") or "",
                "uploaded_at": doc.get("uploaded_at"),
                "source": doc.get("source") or "",
                "inbox_status": doc.get("inbox_status") or INBOX_UNMATCHED,
                "request_id": str(doc["request_id"])
                if doc.get("request_id") is not None
                else None,
                "transaction_ids": [str(oid) for oid in (doc.get("transaction_ids") or [])],
                "rejection_reason": doc.get("rejection_reason") or "",
                "approved_at": doc.get("approved_at"),
                "rejected_at": doc.get("rejected_at"),
                "partial": bool(doc.get("partial")),
                "partial_note": doc.get("partial_note") or "",
                "security_rejected": bool(doc.get("security_rejected")),
                "quarantine_path": doc.get("quarantine_path") or "",
            }
        )
    return items


async def list_quarantine_files(
    db: Any, base: Path, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Desk view of security-rejected / quarantined uploads."""
    items = await list_inbox_files(db, status="quarantine", limit=limit)
    workspace = ensure_receipt_workspace(base)
    qdir = workspace["quarantine"]
    known = {str(row.get("quarantine_path") or "").replace("\\", "/") for row in items}
    if qdir.is_dir():
        for path in sorted(qdir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            rel = f"quarantine/{path.name}"
            if rel in known:
                continue
            items.append(
                {
                    "id": "",
                    "original_filename": path.name,
                    "content_type": "application/octet-stream",
                    "size": path.stat().st_size,
                    "sha256": "",
                    "stored_path": "",
                    "s3_key": "",
                    "uploaded_at": datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                    "source": "disk",
                    "inbox_status": INBOX_REJECTED,
                    "request_id": None,
                    "transaction_ids": [],
                    "rejection_reason": "On-disk quarantine file",
                    "approved_at": None,
                    "rejected_at": None,
                    "partial": False,
                    "partial_note": "",
                    "security_rejected": True,
                    "quarantine_path": rel,
                }
            )
            if len(items) >= limit:
                break
    return items[:limit]


def _resolve_file_path(base: Path, doc: dict[str, Any]) -> Path | None:
    stored = str(doc.get("stored_path") or "").replace("\\", "/")
    if not stored:
        return None
    root = attachments_root(base).resolve()
    path = (attachments_root(base) / stored).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def resolve_receipt_file_path(base: Path, doc: dict[str, Any]) -> Path | None:
    """Public path jail: receipt_files.stored_path under the attachments root."""
    return _resolve_file_path(base, doc)


def _as_day_start(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        raw = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime(raw.year, raw.month, raw.day, tzinfo=UTC)
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _as_day_end(value: date | datetime) -> datetime:
    start = _as_day_start(value)
    return start.replace(hour=23, minute=59, second=59, microsecond=999999)


def _tx_oids_from_file(doc: dict[str, Any]) -> list[ObjectId]:
    out: list[ObjectId] = []
    for raw in doc.get("transaction_ids") or []:
        if isinstance(raw, ObjectId):
            out.append(raw)
        else:
            try:
                out.append(ObjectId(str(raw)))
            except Exception:
                continue
    return out


async def list_receipt_library(
    db: Any,
    *,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
    item: str = "",
    payee: str = "",
    ownership: str = "",
    status: str = "all",
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Submitted-receipts inquiry over ``receipt_files`` + linked transaction fields.

    Date filters ``uploaded_at``. Item / payee / ownership require at least one
    linked transaction that matches (unmatched files drop out when those are set).
    """
    if date_from is None and date_to is None:
        end = utcnow()
        start = end - timedelta(days=30)
    else:
        start = (
            _as_day_start(date_from)
            if date_from is not None
            else datetime(1970, 1, 1, tzinfo=UTC)
        )
        end = _as_day_end(date_to) if date_to is not None else utcnow()
    if end < start:
        raise ValueError("date_to must be on or after date_from")

    query: dict[str, Any] = {
        "uploaded_at": {"$gte": start, "$lte": end},
        "security_rejected": {"$ne": True},
    }
    key = (status or "all").strip().lower()
    if key == "parked":
        query["inbox_status"] = {"$in": list(PARKED_STATUSES)}
    elif key == "quarantine":
        query.pop("security_rejected", None)
        query["security_rejected"] = True
    elif key in INBOX_STATUSES:
        query["inbox_status"] = key
    elif key not in {"", "all"}:
        query["inbox_status"] = {"$in": list(PARKED_STATUSES)}

    cap = max(1, min(int(limit), 500))
    # Over-fetch slightly when tx filters may drop rows
    fetch_n = min(500, cap * 3 if (item or payee or ownership) else cap)
    docs = (
        await db.receipt_files.find(query)
        .sort("uploaded_at", -1)
        .limit(fetch_n)
        .to_list(length=fetch_n)
    )

    all_tx_ids: list[ObjectId] = []
    for doc in docs:
        all_tx_ids.extend(_tx_oids_from_file(doc))
    # unique preserve order
    seen_tx: set[ObjectId] = set()
    uniq_tx: list[ObjectId] = []
    for oid in all_tx_ids:
        if oid not in seen_tx:
            seen_tx.add(oid)
            uniq_tx.append(oid)
    tx_map = await _tx_lookup(db, uniq_tx)

    item_q = (item or "").strip().lower()
    payee_q = " ".join((payee or "").strip().upper().split())
    ownership_q = normalize_person_name(ownership)
    need_tx_filter = bool(item_q or payee_q or ownership_q)

    items: list[dict[str, Any]] = []
    for doc in docs:
        tx_oids = _tx_oids_from_file(doc)
        linked = [tx_map[oid] for oid in tx_oids if oid in tx_map]
        if need_tx_filter:
            matched_links: list[dict[str, Any]] = []
            for tx in linked:
                if item_q:
                    label = str(tx.get("item") or tx.get("category_name") or "").lower()
                    if item_q not in label:
                        continue
                if payee_q:
                    if " ".join(str(tx.get("payee") or "").upper().split()) != payee_q:
                        # allow substring match for inquiry
                        if payee_q not in " ".join(
                            str(tx.get("payee") or "").upper().split()
                        ):
                            continue
                if ownership_q:
                    if normalize_person_name(str(tx.get("ownership") or "")) != ownership_q:
                        continue
                matched_links.append(tx)
            if not matched_links:
                continue
            linked = matched_links

        items_set: list[str] = []
        payees_set: list[str] = []
        owns_set: list[str] = []
        for tx in linked:
            it = str(tx.get("item") or tx.get("category_name") or "").strip()
            if it and it not in items_set:
                items_set.append(it)
            py = str(tx.get("payee") or "").strip()
            if py and py not in payees_set:
                payees_set.append(py)
            ow = str(tx.get("ownership") or "").strip()
            if ow and ow not in owns_set:
                owns_set.append(ow)

        items.append(
            {
                "id": str(doc.get("_id")),
                "original_filename": doc.get("original_filename") or "",
                "content_type": doc.get("content_type") or "",
                "size": int(doc.get("size") or 0),
                "sha256": doc.get("sha256") or "",
                "stored_path": doc.get("stored_path") or "",
                "s3_key": doc.get("s3_key") or "",
                "uploaded_at": doc.get("uploaded_at"),
                "source": doc.get("source") or "",
                "inbox_status": doc.get("inbox_status") or INBOX_UNMATCHED,
                "request_id": str(doc["request_id"])
                if doc.get("request_id") is not None
                else None,
                "transaction_ids": [str(oid) for oid in tx_oids],
                "item": ", ".join(items_set),
                "payee": ", ".join(payees_set),
                "ownership": ", ".join(owns_set),
                "linked_count": len(tx_oids),
                "rejection_reason": doc.get("rejection_reason") or "",
                "partial": bool(doc.get("partial")),
                "partial_note": doc.get("partial_note") or "",
            }
        )
        if len(items) >= cap:
            break
    return items


_STORAGE_TOP_LEVEL = frozenset({"files", "approved", "quarantine", "parked"})
_HEX32 = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def normalize_storage_prefix(prefix: str) -> str:
    """Jail browse prefixes to files/ | approved/ | quarantine/ | parked/."""
    raw = (prefix or "").replace("\\", "/").strip()
    parts = [p for p in raw.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError("Invalid storage prefix")
    if parts and parts[0] not in _STORAGE_TOP_LEVEL:
        raise ValueError(
            "Prefix must be under files/, approved/, quarantine/, or parked/"
        )
    return "/".join(parts)


def _file_id_from_stored_name(name: str) -> str:
    stem = Path(name).name
    if "_" not in stem:
        return ""
    left = stem.split("_", 1)[0]
    return left if _HEX32.match(left) else ""


def _storage_dir(base: Path, prefix: str) -> Path:
    root = attachments_root(base).resolve()
    if not prefix:
        return root
    path = (root / Path(*prefix.split("/"))).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path escapes receipt root") from exc
    return path


async def _docs_by_ids(db: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    clean = [i for i in ids if i]
    if not clean:
        return {}
    docs = await db.receipt_files.find({"_id": {"$in": clean}}).to_list(
        length=len(clean) + 10
    )
    return {str(d.get("_id")): d for d in docs}


async def list_storage_tree(db: Any, base: Path, *, prefix: str = "") -> dict[str, Any]:
    """Immediate child folders under an indexed storage prefix (not a full dump)."""
    ensure_receipt_workspace(base)
    norm = normalize_storage_prefix(prefix)
    if not norm:
        return {
            "prefix": "",
            "folders": [
                {"name": name, "prefix": name}
                for name in ("files", "approved", "quarantine", "parked")
            ],
        }
    target = _storage_dir(base, norm)
    folders: list[dict[str, str]] = []
    if target.is_dir():
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            folders.append({"name": child.name, "prefix": f"{norm}/{child.name}"})
    return {"prefix": norm, "folders": folders}


async def list_storage_objects(
    db: Any,
    base: Path,
    *,
    prefix: str = "",
    q: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """List files in a prefix, enriched from ``receipt_files`` when possible.

    When ``q`` is set, search the index by filename / sha256 prefix (still
    scoped to allowed roots) instead of only the current folder.
    """
    ensure_receipt_workspace(base)
    norm = normalize_storage_prefix(prefix)
    cap = max(1, min(int(limit), 500))
    needle = (q or "").strip()

    items: list[dict[str, Any]] = []

    if needle:
        rx = re.escape(needle)
        query: dict[str, Any] = {
            "$or": [
                {"original_filename": {"$regex": rx, "$options": "i"}},
                {"sha256": {"$regex": f"^{re.escape(needle.lower())}"}},
                {"stored_path": {"$regex": rx, "$options": "i"}},
            ]
        }
        if norm:
            query = {
                "$and": [
                    query,
                    {
                        "$or": [
                            {"stored_path": {"$regex": f"^{re.escape(norm)}(/|$)"}},
                            {
                                "materialized_paths": {
                                    "$elemMatch": {
                                        "$regex": f"^{re.escape(norm)}(/|$)"
                                    }
                                }
                            },
                            {
                                "quarantine_path": {
                                    "$regex": f"^{re.escape(norm)}(/|$)"
                                }
                            },
                        ]
                    },
                ]
            }
        docs = (
            await db.receipt_files.find(query)
            .sort("uploaded_at", -1)
            .limit(cap)
            .to_list(length=cap)
        )
        for doc in docs:
            stored = str(doc.get("stored_path") or "").replace("\\", "/")
            fid = str(doc.get("_id") or "")
            items.append(
                {
                    "name": str(doc.get("original_filename") or Path(stored).name or fid),
                    "key": stored or str(doc.get("quarantine_path") or ""),
                    "kind": "file",
                    "size": int(doc.get("size") or 0),
                    "file_id": fid,
                    "content_type": doc.get("content_type") or "",
                    "inbox_status": doc.get("inbox_status") or "",
                    "sha256": doc.get("sha256") or "",
                    "uploaded_at": doc.get("uploaded_at"),
                    "indexed": True,
                }
            )
        return {"prefix": norm, "q": needle, "items": items, "total": len(items)}

    if not norm:
        return {"prefix": "", "q": "", "items": [], "total": 0}

    target = _storage_dir(base, norm)
    if not target.is_dir():
        return {"prefix": norm, "q": "", "items": [], "total": 0}

    disk_files: list[Path] = []
    for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if child.is_file() and not child.name.startswith("."):
            disk_files.append(child)
        if len(disk_files) >= cap:
            break

    guessed_ids = [_file_id_from_stored_name(p.name) for p in disk_files]
    by_id = await _docs_by_ids(db, guessed_ids)

    # Also map by exact stored_path / quarantine_path / materialized path
    keys = [f"{norm}/{p.name}".replace("\\", "/") for p in disk_files]
    path_docs = await db.receipt_files.find(
        {
            "$or": [
                {"stored_path": {"$in": keys}},
                {"quarantine_path": {"$in": keys}},
                {"materialized_paths": {"$in": keys}},
            ]
        }
    ).to_list(length=len(keys) + 20)
    by_key: dict[str, dict[str, Any]] = {}
    for doc in path_docs:
        for field in ("stored_path", "quarantine_path"):
            k = str(doc.get(field) or "").replace("\\", "/")
            if k:
                by_key[k] = doc
        for mp in doc.get("materialized_paths") or []:
            k = str(mp or "").replace("\\", "/")
            if k:
                by_key[k] = doc

    for path in disk_files:
        key = f"{norm}/{path.name}".replace("\\", "/")
        fid = _file_id_from_stored_name(path.name)
        doc = by_key.get(key) or by_id.get(fid)
        try:
            size = path.stat().st_size
        except OSError:
            size = int((doc or {}).get("size") or 0)
        items.append(
            {
                "name": str(
                    (doc or {}).get("original_filename") or path.name
                ),
                "key": key,
                "kind": "file",
                "size": size,
                "file_id": str((doc or {}).get("_id") or fid or ""),
                "content_type": (doc or {}).get("content_type")
                or guess_content_type(path.name),
                "inbox_status": (doc or {}).get("inbox_status") or "",
                "sha256": (doc or {}).get("sha256") or "",
                "uploaded_at": (doc or {}).get("uploaded_at"),
                "indexed": doc is not None,
            }
        )

    return {"prefix": norm, "q": "", "items": items, "total": len(items)}


async def get_receipt_file_open_meta(
    db: Any, base: Path, file_id: str
) -> dict[str, Any]:
    """Return viewer chrome for a receipt file. Raises ValueError if missing."""
    fid = (file_id or "").strip()
    if not fid:
        raise ValueError("File id is required")
    doc = await db.receipt_files.find_one({"_id": fid})
    if not doc:
        raise ValueError("Receipt file not found")
    path = _resolve_file_path(base, doc)
    filename = str(doc.get("original_filename") or (path.name if path else "file"))
    content_type = str(doc.get("content_type") or "") or guess_content_type(filename)
    size = int(doc.get("size") or 0)
    if path is not None and path.is_file():
        try:
            size = path.stat().st_size
        except OSError:
            pass
    return {
        "id": fid,
        "original_filename": filename,
        "content_type": content_type,
        "size": size,
        "sha256": doc.get("sha256") or "",
        "stored_path": doc.get("stored_path") or "",
        "s3_key": doc.get("s3_key") or "",
        "inbox_status": doc.get("inbox_status") or INBOX_UNMATCHED,
        "source": doc.get("source") or "",
        "uploaded_at": doc.get("uploaded_at"),
        "request_id": str(doc["request_id"]) if doc.get("request_id") is not None else None,
        "transaction_ids": [str(oid) for oid in (doc.get("transaction_ids") or [])],
        "rejection_reason": doc.get("rejection_reason") or "",
        "partial": bool(doc.get("partial")),
        "partial_note": doc.get("partial_note") or "",
        "security_rejected": bool(doc.get("security_rejected")),
        "file_present": path is not None and path.is_file(),
    }


async def open_receipt_file_bytes(
    db: Any, base: Path, file_id: str
) -> tuple[Path, str, str]:
    """Resolve bytes on disk for streaming. Returns (path, filename, content_type)."""
    fid = (file_id or "").strip()
    if not fid:
        raise ValueError("File id is required")
    doc = await db.receipt_files.find_one({"_id": fid})
    if not doc:
        raise ValueError("Receipt file not found")
    path = _resolve_file_path(base, doc)
    if path is None:
        raise FileNotFoundError("Receipt file is missing on the server")
    filename = str(doc.get("original_filename") or path.name)
    content_type = str(doc.get("content_type") or "") or guess_content_type(filename)
    return path, filename, content_type


def _approved_folder_segment(value: str, *, fallback: str = "unclassified") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (value or "").strip())
    text = text.strip(" .") or fallback
    return text[:80]


def _tx_browse_item(tx: dict[str, Any]) -> str:
    return str(tx.get("item") or tx.get("category_name") or "").strip()


def _approved_relpath(tx: dict[str, Any], stored_name: str) -> str | None:
    """``YYYY/MM/<item>/<file>`` under approved/, or None if item missing."""
    item = _tx_browse_item(tx)
    if not item:
        return None
    day = tx.get("date")
    if isinstance(day, datetime):
        year, month = f"{day.year:04d}", f"{day.month:02d}"
    elif isinstance(day, date):
        year, month = f"{day.year:04d}", f"{day.month:02d}"
    else:
        text = str(day or "")[:10]
        year = text[:4] if len(text) >= 4 else "0000"
        month = text[5:7] if len(text) >= 7 else "00"
    return (
        f"{year}/{month}/{_approved_folder_segment(item)}/"
        f"{safe_name(Path(stored_name).name)}"
    )


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        try:
            if dest.stat().st_size == src.stat().st_size:
                return
        except OSError:
            pass
        try:
            dest.unlink()
        except OSError:
            pass
    try:
        os.link(src, dest)
    except OSError:
        import shutil

        shutil.copy2(src, dest)


def _browse_rel_to_path(base: Path, rel: str) -> Path | None:
    text = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not text or ".." in text.split("/"):
        return None
    if not (
        text.startswith("approved/")
        or text.startswith("parked/")
    ):
        return None
    return attachments_root(base) / Path(*text.split("/"))


def _unlink_browse_path(base: Path, rel: str) -> None:
    path = _browse_rel_to_path(base, rel)
    if path is None:
        return
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


async def materialize_approved_file(
    db: Any,
    base: Path,
    file_doc: dict[str, Any],
) -> list[str]:
    """Idempotent browse copies under ``approved/…/<item>/`` or ``parked/unmapped/``.

    Canonical bytes stay in ``files/``. Linked rows without an item land in the
    temp unmapped folder until mapping supplies an item, then relocate atomically
    (new path → DB → remove stale browse path).
    """
    if str(file_doc.get("inbox_status") or "") != INBOX_APPROVED:
        return []
    src = _resolve_file_path(base, file_doc)
    if src is None:
        return []
    workspace = ensure_receipt_workspace(base)
    approved_root = workspace["approved"]
    unmapped_root = workspace["parked_unmapped"]
    raw_ids = list(file_doc.get("transaction_ids") or [])
    tx_oids: list[ObjectId] = []
    for raw in raw_ids:
        if isinstance(raw, ObjectId):
            tx_oids.append(raw)
        else:
            try:
                tx_oids.append(ObjectId(str(raw)))
            except Exception:
                continue
    if not tx_oids:
        return list(file_doc.get("materialized_paths") or [])
    txs = await db.transactions.find(
        {"_id": {"$in": tx_oids}},
        {"date": 1, "item": 1, "category_name": 1, "payee": 1},
    ).to_list(length=len(tx_oids) + 10)
    written: list[str] = []
    for tx in txs:
        rel = _approved_relpath(tx, src.name)
        if not rel:
            continue
        dest = approved_root / rel
        _link_or_copy(src, dest)
        written.append(f"approved/{rel.replace(chr(92), '/')}")
    if not written:
        # Linked but no item yet → temp browse folder (not sorted by item).
        dest = unmapped_root / safe_name(src.name)
        _link_or_copy(src, dest)
        written.append(f"parked/unmapped/{safe_name(src.name)}")
    desired = sorted(set(written))
    previous = [
        str(p).replace("\\", "/")
        for p in (file_doc.get("materialized_paths") or [])
        if str(p).strip()
    ]
    # DB first with desired paths, then drop stale browse copies (atomic enough
    # that a crash leaves an extra copy, never a missing canonical file).
    await db.receipt_files.update_one(
        {"_id": file_doc["_id"]},
        {
            "$set": {
                "materialized_paths": desired,
                "materialized_at": utcnow(),
                "rematerialize_pending": False,
                "browse_placement": (
                    "approved" if any(p.startswith("approved/") for p in desired) else "unmapped"
                ),
            }
        },
    )
    for old in previous:
        if old not in desired:
            _unlink_browse_path(base, old)
    return written


async def rematerialize_approved_for_transaction(
    db: Any, base: Path, tx_id: ObjectId
) -> int:
    """Re-run materialize when a linked row gains item/payee (idempotent)."""
    cursor = db.receipt_files.find(
        {
            "inbox_status": INBOX_APPROVED,
            "transaction_ids": tx_id,
        }
    )
    docs = await cursor.to_list(length=200)
    # Also match string form of tx id stored on some docs
    extra = await db.receipt_files.find(
        {
            "inbox_status": INBOX_APPROVED,
            "transaction_ids": str(tx_id),
        }
    ).to_list(length=200)
    seen: set[str] = set()
    count = 0
    for doc in list(docs) + list(extra):
        fid = str(doc.get("_id"))
        if fid in seen:
            continue
        seen.add(fid)
        paths = await materialize_approved_file(db, base, doc)
        if paths:
            count += 1
    return count


async def mark_rematerialize_pending_for_txs(db: Any, tx_ids: list[ObjectId]) -> int:
    if not tx_ids:
        return 0
    mixed: list[Any] = list(tx_ids) + [str(oid) for oid in tx_ids]
    result = await db.receipt_files.update_many(
        {
            "inbox_status": INBOX_APPROVED,
            "transaction_ids": {"$in": mixed},
        },
        {"$set": {"rematerialize_pending": True, "updated_at": utcnow()}},
    )
    return int(getattr(result, "modified_count", 0) or 0)


async def flush_pending_rematerialize(
    db: Any,
    base: Path,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Drain ``rematerialize_pending`` so unmapped browse copies relocate."""
    ensure_receipt_workspace(base)
    docs = await db.receipt_files.find(
        {"inbox_status": INBOX_APPROVED, "rematerialize_pending": True}
    ).to_list(length=limit)
    ok = 0
    failed = 0
    for doc in docs:
        try:
            await materialize_approved_file(db, base, doc)
            ok += 1
        except Exception as exc:
            failed += 1
            logger.warning(
                "rematerialize failed for %s: %s", doc.get("_id"), exc
            )
    return {"ok": ok, "failed": failed}


async def process_receipt_side_effects_for_txs(
    settings: Settings,
    db: Any,
    base: Path | None,
    tx_ids: list[ObjectId],
) -> dict[str, Any]:
    """Ensure requests, immediate notify, and rematerialize for mapped/saved rows."""
    unique: list[ObjectId] = []
    seen: set[ObjectId] = set()
    for oid in tx_ids:
        if isinstance(oid, ObjectId) and oid not in seen:
            seen.add(oid)
            unique.append(oid)
    if not unique:
        return {"requests": 0, "notify": {}, "rematerialize": {}}

    request_ids: list[ObjectId] = []
    for oid in unique:
        doc = await db.transactions.find_one({"_id": oid})
        if not doc:
            continue
        try:
            req = await ensure_receipt_request(db, doc)
        except Exception as exc:
            logger.warning("ensure_receipt_request failed for %s: %s", oid, exc)
            continue
        if req and isinstance(req.get("_id"), ObjectId) and req.get("immediate_notify_pending"):
            request_ids.append(req["_id"])

    notify: dict[str, int] = {"sent": 0, "skipped": 0, "failed": 0}
    if request_ids:
        notify = await flush_immediate_receipt_notifies(db, request_ids=request_ids)
    rematerialize: dict[str, int] = {}
    if base is not None:
        await mark_rematerialize_pending_for_txs(db, unique)
        for oid in unique:
            try:
                await rematerialize_approved_for_transaction(db, base, oid)
            except Exception as exc:
                logger.warning("rematerialize for tx %s failed: %s", oid, exc)
        rematerialize = await flush_pending_rematerialize(
            db, base, limit=max(50, len(unique) * 4)
        )
    return {
        "requests": len(request_ids),
        "notify": notify,
        "rematerialize": rematerialize,
    }


def schedule_receipt_side_effects(
    settings: Settings,
    db: Any,
    base: Path | None,
    tx_ids: list[ObjectId],
) -> None:
    """Fire-and-forget side effects so save/map APIs stay fast."""
    if not tx_ids:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _runner() -> None:
        try:
            await process_receipt_side_effects_for_txs(settings, db, base, tx_ids)
        except Exception as exc:
            logger.warning("receipt side effects failed: %s", exc)

    loop.create_task(_runner())


async def approve_receipt_files(
    db: Any,
    base: Path,
    *,
    file_ids: list[str],
    actor_id: ObjectId | None,
) -> dict[str, Any]:
    """Mark parked/matched files as permanent evidence (still live under files/)."""
    ensure_receipt_workspace(base)
    now = utcnow()
    updated = 0
    materialized = 0
    for file_id in file_ids:
        fid = (file_id or "").strip()
        if not fid:
            continue
        doc = await db.receipt_files.find_one({"_id": fid})
        if not doc:
            continue
        status = doc.get("inbox_status") or INBOX_UNMATCHED
        if status == INBOX_APPROVED:
            paths = await materialize_approved_file(db, base, doc)
            if paths:
                materialized += 1
            continue
        if status == INBOX_REJECTED:
            raise ValueError(f"File {fid} was rejected; re-link or re-upload before approve")
        result = await db.receipt_files.update_one(
            {"_id": fid},
            {
                "$set": {
                    "inbox_status": INBOX_APPROVED,
                    "approved_at": now,
                    "approved_by": actor_id,
                    "updated_at": now,
                    "rejection_reason": None,
                    "rejected_at": None,
                    "rejected_by": None,
                }
            },
        )
        updated += int(getattr(result, "modified_count", 0) or 0)
        refreshed = await db.receipt_files.find_one({"_id": fid})
        if refreshed:
            paths = await materialize_approved_file(db, base, refreshed)
            if paths:
                materialized += 1
    return {
        "updated": updated,
        "files": len(file_ids),
        "materialized": materialized,
    }


async def reject_receipt_files(
    db: Any,
    base: Path,
    *,
    file_ids: list[str],
    reason: str,
    actor_id: ObjectId | None,
) -> dict[str, Any]:
    """Reject files and copy bytes into quarantine/ (canonical files/ kept)."""
    text = " ".join((reason or "").strip().split())
    if len(text) < 3:
        raise ValueError("Rejection reason is required (at least 3 characters)")
    workspace = ensure_receipt_workspace(base)
    quarantine = workspace["quarantine"]
    now = utcnow()
    updated = 0
    for file_id in file_ids:
        fid = (file_id or "").strip()
        if not fid:
            continue
        doc = await db.receipt_files.find_one({"_id": fid})
        if not doc:
            continue
        if (doc.get("inbox_status") or "") == INBOX_APPROVED:
            raise ValueError(f"File {fid} is already approved; cannot reject")
        src = _resolve_file_path(base, doc)
        if src is not None:
            dest = quarantine / src.name
            if not dest.exists():
                import shutil

                shutil.copy2(src, dest)
        result = await db.receipt_files.update_one(
            {"_id": fid},
            {
                "$set": {
                    "inbox_status": INBOX_REJECTED,
                    "rejection_reason": text[:500],
                    "rejected_at": now,
                    "rejected_by": actor_id,
                    "updated_at": now,
                    "quarantine_path": f"quarantine/{src.name}" if src is not None else "",
                }
            },
        )
        updated += int(getattr(result, "modified_count", 0) or 0)
    return {"updated": updated, "files": len(file_ids)}


async def search_link_transactions(
    db: Any,
    *,
    ownership: str = "",
    amount: float | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    needed_only: bool = False,
    today: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "trashed": {"$ne": True},
        "discarded": {"$ne": True},
    }
    name = normalize_person_name(ownership)
    if name:
        query["ownership"] = name
    if amount is not None:
        query["amount"] = amount
    if today and date_from is None and date_to is None:
        start = _today_start_naive(datetime.now(UTC))
        end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
        query["date"] = {"$gte": start, "$lte": end}
    elif date_from is not None or date_to is not None:
        date_q: dict[str, Any] = {}
        if date_from is not None:
            date_q["$gte"] = date_from.replace(tzinfo=None) if date_from.tzinfo else date_from
        if date_to is not None:
            date_q["$lte"] = date_to.replace(tzinfo=None) if date_to.tzinfo else date_to
        query["date"] = date_q
    cursor = db.transactions.find(
        query,
        {
            "date": 1,
            "amount": 1,
            "currency": 1,
            "item": 1,
            "category_name": 1,
            "payee": 1,
            "ownership": 1,
            "receipt_status": 1,
            "description": 1,
            "attachments": 1,
            "receipt_partial_note": 1,
        },
    ).sort("date", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    tx_ids = [doc["_id"] for doc in docs if doc.get("_id") is not None]
    req_by_tx: dict[Any, dict[str, Any]] = {}
    if tx_ids:
        req_rows = await db.receipt_requests.find(
            {"transaction_id": {"$in": tx_ids}}
        ).to_list(length=len(tx_ids) + 50)
        for row in req_rows:
            tid = row.get("transaction_id")
            if tid is not None and tid not in req_by_tx:
                req_by_tx[tid] = row
    out: list[dict[str, Any]] = []
    for doc in docs:
        attachments = list(doc.get("attachments") or [])
        req = req_by_tx.get(doc.get("_id")) or {}
        req_status = str(req.get("status") or "")
        receipt_status = str(doc.get("receipt_status") or "pending")
        if req_status == "received" or receipt_status == "received":
            coverage = "received"
        elif receipt_status == "partial" or req.get("partial_receipt"):
            coverage = "partial"
        elif attachments:
            coverage = "linked"
        elif req_status == "needed" or receipt_status in ("pending", "needed", ""):
            coverage = "needed"
        else:
            coverage = receipt_status or "none"
        out.append(
            {
                "id": str(doc.get("_id")),
                "date": doc.get("date"),
                "amount": doc.get("amount"),
                "currency": doc.get("currency") or "TZS",
                "item": doc.get("item") or doc.get("category_name") or "",
                "payee": doc.get("payee") or "",
                "ownership": doc.get("ownership") or "",
                "receipt_status": receipt_status or "pending",
                "description": doc.get("description") or "",
                "attachment_count": len(attachments),
                "request_status": req_status or None,
                "request_id": str(req["_id"]) if req.get("_id") is not None else None,
                "coverage": coverage,
                "partial_note": str(
                    req.get("partial_note") or doc.get("receipt_partial_note") or ""
                ),
            }
        )
    if needed_only:
        out = [row for row in out if row["coverage"] in ("needed", "partial", "linked")]
    return out


async def manual_link_files(
    db: Any,
    *,
    file_ids: list[str],
    transaction_ids: list[ObjectId],
    actor_id: ObjectId | None,
    partial: bool = False,
    partial_note: str = "",
    attachments_dir: Path | None = None,
) -> dict[str, Any]:
    """Link N inbox files ↔ M rows (desktop confirm). Supports partial coverage."""
    if not file_ids or not transaction_ids:
        raise ValueError("Select at least one file and one transaction")
    note = " ".join((partial_note or "").strip().split())[:500]
    if partial and len(note) < 3:
        raise ValueError("Partial link needs a short note (e.g. covers fuel only)")
    linked = 0
    skipped = 0
    rematerialized = 0
    for file_id in file_ids:
        file_doc = await db.receipt_files.find_one({"_id": file_id})
        if not file_doc:
            continue
        if str(file_doc.get("inbox_status") or "") == INBOX_REJECTED:
            raise ValueError(f"File {file_id} was rejected; re-upload before linking")
        stored = str(file_doc.get("stored_path") or "")
        meta = {
            "id": file_id,
            "filename": file_doc.get("original_filename") or "file",
            "stored_name": stored,
            "stored_path": stored,
            "size": int(file_doc.get("size") or 0),
            "sha256": str(file_doc.get("sha256") or ""),
            "s3_key": str(file_doc.get("s3_key") or ""),
            "uploaded_at": file_doc.get("uploaded_at") or utcnow(),
        }
        for tx_id in transaction_ids:
            tx = await db.transactions.find_one({"_id": tx_id}, {"attachments": 1})
            if not tx:
                continue
            existing_ids = {
                str(item.get("id") or item.get("file_id") or "")
                for item in (tx.get("attachments") or [])
            }
            if file_id in existing_ids:
                if not partial:
                    await mark_request_received(db, tx_id, file_id=file_id)
                skipped += 1
                continue
            await link_file_to_transaction(
                db,
                tx_id,
                meta,
                actor_id=actor_id,
                partial=partial,
                partial_note=note,
            )
            linked += 1
        current = str(file_doc.get("inbox_status") or INBOX_UNMATCHED)
        next_status = (
            INBOX_APPROVED if current == INBOX_APPROVED else INBOX_MATCHED
        )
        file_set: dict[str, Any] = {
            "inbox_status": next_status,
            "updated_at": utcnow(),
            "partial": bool(partial),
        }
        if partial:
            file_set["partial_note"] = note
        else:
            file_set["partial_note"] = None
        await db.receipt_files.update_one(
            {"_id": file_id},
            {
                "$set": file_set,
                "$addToSet": {"transaction_ids": {"$each": transaction_ids}},
            },
        )
        if attachments_dir is not None:
            refreshed = await db.receipt_files.find_one({"_id": file_id})
            if refreshed and str(refreshed.get("inbox_status") or "") == INBOX_APPROVED:
                paths = await materialize_approved_file(db, attachments_dir, refreshed)
                if paths:
                    rematerialized += 1
    return {
        "linked": linked,
        "skipped": skipped,
        "files": len(file_ids),
        "transactions": len(transaction_ids),
        "partial": bool(partial),
        "partial_note": note if partial else "",
        "rematerialized": rematerialized,
    }


async def mark_requests_reminded(
    db: Any, *, ownership: str = "", request_ids: list[ObjectId] | None = None
) -> int:
    """Desktop 'marked reminded' without sending push."""
    now = utcnow()
    query: dict[str, Any] = {"status": "needed"}
    if request_ids:
        query["_id"] = {"$in": request_ids}
    name = normalize_person_name(ownership)
    if name:
        query["asked_of_name"] = name
    if not request_ids and not name:
        return 0
    result = await db.receipt_requests.update_many(
        query,
        {
            "$set": {"last_reminded_at": now, "updated_at": now},
            "$inc": {"reminder_count": 1},
        },
    )
    return int(getattr(result, "modified_count", 0) or 0)


async def reject_if_needed_receipts(db: Any, ids: list[ObjectId]) -> None:
    """Block verify/approve while a required receipt request is still needed."""
    if not ids:
        return
    needed = await db.receipt_requests.find(
        {"transaction_id": {"$in": ids}, "status": "needed"},
        {"transaction_id": 1},
    ).to_list(length=None)
    if not needed:
        return
    needed_ids = {doc["transaction_id"] for doc in needed if doc.get("transaction_id")}
    txs = await db.transactions.find(
        {"_id": {"$in": list(needed_ids)}},
        {"category_id": 1, "category_name": 1, "item": 1, "attachments": 1},
    ).to_list(length=None)
    blocked = 0
    for tx in txs or []:
        if await category_requires_receipt(db, tx):
            blocked += 1
    if blocked:
        raise ValueError(
            f"{blocked} transaction(s) still need a receipt before verify."
        )
