"""Accountant approve / reject / re-approve / category routes (Phase 3.2)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Request

from .accountant_ops import RE_APPROVE_CASCADE_FIELDS
from .badge_counters import apply_remembered, load_badge_docs, note_transition
from .dependencies import require_roles
from .errors import ApiError
from .schemas import (
    BulkCategoryAssignRequest,
    BulkIdsRequest,
    BulkRejectRequest,
    CategoryAssignRequest,
    RejectRequest,
)
from .security import utcnow
from .serialization import object_id

actions = APIRouter(prefix="/transactions", tags=["accountant"])

Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _valid_id(value: str) -> ObjectId:
    try:
        return object_id(value)
    except ValueError:
        raise ApiError(422, "invalid_id", "Resource id is invalid") from None


def _parse_ids(raw_ids: list[str]) -> list[ObjectId]:
    out: list[ObjectId] = []
    for raw in raw_ids:
        text = str(raw).strip()
        if not text:
            continue
        out.append(_valid_id(text))
    return out


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


def _tx_incomplete_item_or_payee(doc: dict[str, Any]) -> bool:
    item = (doc.get("item") or doc.get("category_name") or "").strip()
    payee = (doc.get("payee") or "").strip()
    return not item or not payee


async def _reject_if_missing_item_or_payee(
    request: Request, ids: list[ObjectId]
) -> None:
    """Belt-and-suspenders: approve requires both Item and Payee."""
    if not ids:
        return
    docs = await _database(request).transactions.find(
        {"_id": {"$in": ids}},
        {"item": 1, "category_name": 1, "payee": 1},
    ).to_list(length=None)
    bad = sum(1 for doc in docs if _tx_incomplete_item_or_payee(doc))
    if bad:
        raise ApiError(
            400,
            "missing_item_or_payee",
            f"{bad} transaction(s) still need Item and/or Payee before approve.",
        )


@actions.post("/bulk-approve")
async def bulk_approve(
    body: BulkIdsRequest, request: Request, manager: Manager
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    await _reject_if_missing_item_or_payee(request, ids)
    actor_id = _actor_oid(manager)
    now = utcnow().replace(tzinfo=None)
    db = _database(request)
    approve_filter = {"_id": {"$in": ids}, "verified": False}
    before = await load_badge_docs(db, approve_filter)
    result = await db.transactions.update_many(
        approve_filter,
        {
            "$set": {
                "verified": True,
                "verified_by": actor_id,
                "verified_at": now,
                "rejection_reason": None,
                "possible_duplicate": False,
                "date_discrepancy": False,
            },
            "$unset": {"duplicate_of_id": ""},
        },
    )
    n = int(result.modified_count)
    await apply_remembered(db, before, {"verified": True}, n)
    if n:
        await _audit(
            request,
            action="txn.approve_bulk",
            actor=manager,
            entity_ids=ids,
            details={"count": n},
        )
    return {"updated": n}


@actions.post("/bulk-reject")
async def bulk_reject(
    body: BulkRejectRequest, request: Request, manager: Manager
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    reason = body.reason or ""
    db = _database(request)
    reject_filter = {"_id": {"$in": ids}}
    before = await load_badge_docs(db, reject_filter)
    result = await db.transactions.update_many(
        reject_filter,
        {
            "$set": {
                "verified": False,
                "rejection_reason": reason,
                "rejected": True,
                "discarded": False,
            }
        },
    )
    n = int(result.modified_count)
    await apply_remembered(
        db,
        before,
        {"verified": False, "rejected": True, "discarded": False},
        n,
    )
    if n:
        await _audit(
            request,
            action="txn.reject_bulk",
            actor=manager,
            entity_ids=ids,
            details={"count": n, "reason": reason[:240]},
        )
    return {"updated": n}


@actions.post("/bulk-re-approve")
async def bulk_re_approve(
    body: BulkIdsRequest, request: Request, manager: Manager
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    await _reject_if_missing_item_or_payee(request, ids)
    db = _database(request)
    docs = await db.transactions.find(
        {"_id": {"$in": ids}, "verified": False},
        {"_id": 1, "original_transaction_id": 1},
    ).to_list(length=None)
    cascade_ids = [d["_id"] for d in docs if d.get("original_transaction_id")]
    simple_ids = [d["_id"] for d in docs if not d.get("original_transaction_id")]

    count = 0
    actor_id = _actor_oid(manager)
    for tx_id in cascade_ids:
        if await _re_approve_one(request, tx_id, actor_id, manager):
            count += 1

    if simple_ids:
        now = utcnow().replace(tzinfo=None)
        simple_filter = {"_id": {"$in": simple_ids}, "verified": False}
        before = await load_badge_docs(db, simple_filter)
        result = await db.transactions.update_many(
            simple_filter,
            {
                "$set": {
                    "verified": True,
                    "verified_by": actor_id,
                    "verified_at": now,
                    "rejection_reason": None,
                    "rejected": False,
                    "edited_after_verification": False,
                }
            },
        )
        changed = int(result.modified_count)
        await apply_remembered(
            db, before, {"verified": True, "rejected": False}, changed
        )
        count += changed
    return {"updated": int(count)}


@actions.post("/bulk-category")
async def bulk_category(
    body: BulkCategoryAssignRequest, request: Request, _manager: Manager
) -> dict:
    ids = _parse_ids(body.ids)
    if not ids:
        return {"updated": 0}
    update: dict[str, Any] = {
        "category_name": body.category_name,
        "item": body.category_name,
    }
    if body.category_id:
        update["category_id"] = _valid_id(body.category_id)
    if body.payee is not None:
        update["payee"] = " ".join((body.payee or "").strip().upper().split())
    result = await _database(request).transactions.update_many(
        {"_id": {"$in": ids}},
        {"$set": update},
    )
    return {"updated": int(result.modified_count)}


@actions.post("/{tx_id}/approve")
async def approve_transaction(
    tx_id: str, request: Request, manager: Manager
) -> dict:
    oid = _valid_id(tx_id)
    await _reject_if_missing_item_or_payee(request, [oid])
    actor_id = _actor_oid(manager)
    now = utcnow().replace(tzinfo=None)
    db = _database(request)
    approve_filter = {"_id": oid, "verified": False}
    before = await load_badge_docs(db, approve_filter)
    result = await db.transactions.update_one(
        approve_filter,
        {
            "$set": {
                "verified": True,
                "verified_by": actor_id,
                "verified_at": now,
                "rejection_reason": None,
                "possible_duplicate": False,
                "date_discrepancy": False,
            },
            "$unset": {"duplicate_of_id": ""},
        },
    )
    ok = result.modified_count == 1
    await apply_remembered(db, before, {"verified": True}, int(ok))
    if ok:
        await _audit(
            request,
            action="txn.approve",
            actor=manager,
            entity_ids=[oid],
        )
    return {"ok": ok}


@actions.post("/{tx_id}/reject")
async def reject_transaction(
    tx_id: str, body: RejectRequest, request: Request, manager: Manager
) -> dict:
    oid = _valid_id(tx_id)
    reason = body.reason or ""
    db = _database(request)
    before = await load_badge_docs(db, {"_id": oid})
    result = await db.transactions.update_one(
        {"_id": oid},
        {
            "$set": {
                "verified": False,
                "rejection_reason": reason,
                "rejected": True,
                "discarded": False,
            }
        },
    )
    ok = result.modified_count == 1
    await apply_remembered(
        db,
        before,
        {"verified": False, "rejected": True, "discarded": False},
        int(ok),
    )
    if ok:
        await _audit(
            request,
            action="txn.reject",
            actor=manager,
            entity_ids=[oid],
            details={"reason": reason[:240]},
        )
    return {"ok": ok}


async def _re_approve_one(
    request: Request,
    tx_id: ObjectId,
    accountant_id: ObjectId,
    manager: dict[str, Any],
) -> bool:
    db = _database(request)
    pending_doc = await db.transactions.find_one({"_id": tx_id, "verified": False})
    if not pending_doc:
        return False

    original_id = pending_doc.get("original_transaction_id")
    now = utcnow().replace(tzinfo=None)

    original = None
    if original_id:
        original = await db.transactions.find_one({"_id": original_id})
        updates = {
            k: pending_doc[k] for k in RE_APPROVE_CASCADE_FIELDS if k in pending_doc
        }
        updates.update(
            {
                "verified": True,
                "verified_by": accountant_id,
                "verified_at": now,
                "rejection_reason": None,
                "rejected": False,
                "edited_after_verification": False,
                "last_edited_at": pending_doc.get("last_edited_at"),
                "last_edited_by": pending_doc.get("last_edited_by"),
            }
        )
        result = await db.transactions.update_one(
            {"_id": original_id}, {"$set": updates}
        )
        if result.modified_count != 1:
            return False
        if original is not None:
            after = dict(original)
            after.update(updates)
            await note_transition(db, dict(original), after)
        await db.transactions.delete_one({"_id": tx_id})
        await note_transition(db, dict(pending_doc), None)
        await _audit(
            request,
            action="txn.re_approve",
            actor=manager,
            entity_ids=[original_id, tx_id],
            details={"cascade": True},
        )
        return True

    result = await db.transactions.update_one(
        {"_id": tx_id, "verified": False},
        {
            "$set": {
                "verified": True,
                "verified_by": accountant_id,
                "verified_at": now,
                "rejection_reason": None,
                "rejected": False,
                "edited_after_verification": False,
            }
        },
    )
    ok = result.modified_count == 1
    if ok:
        await note_transition(
            db,
            dict(pending_doc),
            {**dict(pending_doc), "verified": True, "rejected": False},
        )
        await _audit(
            request,
            action="txn.re_approve",
            actor=manager,
            entity_ids=[tx_id],
        )
    return ok


@actions.post("/{tx_id}/re-approve")
async def re_approve_transaction(
    tx_id: str, request: Request, manager: Manager
) -> dict:
    oid = _valid_id(tx_id)
    await _reject_if_missing_item_or_payee(request, [oid])
    ok = await _re_approve_one(request, oid, _actor_oid(manager), manager)
    return {"ok": ok}


@actions.post("/{tx_id}/return-to-inbox")
async def return_to_inbox(tx_id: str, request: Request, _manager: Manager) -> dict:
    oid = _valid_id(tx_id)
    db = _database(request)
    inbox_filter = {"_id": oid, "rejected": True}
    before = await load_badge_docs(db, inbox_filter)
    result = await db.transactions.update_one(
        inbox_filter,
        {"$set": {"rejected": False, "rejection_reason": None, "discarded": False}},
    )
    await apply_remembered(
        db,
        before,
        {"rejected": False, "discarded": False},
        int(result.modified_count),
    )
    return {"ok": result.modified_count == 1}


@actions.patch("/{tx_id}/category")
async def update_category(
    tx_id: str,
    body: CategoryAssignRequest,
    request: Request,
    _manager: Manager,
) -> dict:
    oid = _valid_id(tx_id)
    update: dict[str, Any] = {
        "category_name": body.category_name,
        "item": body.category_name,
    }
    if body.category_id:
        update["category_id"] = _valid_id(body.category_id)
    if body.payee is not None:
        update["payee"] = " ".join((body.payee or "").strip().upper().split())
    result = await _database(request).transactions.update_one(
        {"_id": oid}, {"$set": update}
    )
    return {"ok": result.modified_count == 1}
