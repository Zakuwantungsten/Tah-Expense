"""Admin session listing and force-logout (revoke) endpoints."""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Query, Request, Response, status

from .audit_ops import record_operation_event
from .dependencies import Admin
from .errors import ApiError
from .security import utcnow
from .serialization import json_safe, object_id

sessions = APIRouter(prefix="/sessions", tags=["sessions"])


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _public_session(
    doc: dict[str, Any],
    user: dict[str, Any] | None,
    *,
    current_id: ObjectId | None,
) -> dict[str, Any]:
    return {
        "id": doc.get("_id"),
        "user_id": doc.get("user_id"),
        "username": (user or {}).get("username") or "",
        "full_name": (user or {}).get("full_name") or "",
        "role": (user or {}).get("role") or "",
        "ip": doc.get("ip"),
        "device_mac": doc.get("device_mac"),
        "user_agent": doc.get("user_agent") or "",
        "created_at": doc.get("created_at"),
        "last_used_at": doc.get("last_used_at"),
        "expires_at": doc.get("expires_at"),
        "revoked_at": doc.get("revoked_at"),
        "is_current": bool(current_id and doc.get("_id") == current_id),
    }


@sessions.get("")
async def list_sessions(
    request: Request,
    _admin: Admin,
    user_id: str | None = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(200, ge=1, le=500),
) -> list[dict]:
    db = _database(request)
    now = utcnow()
    query: dict[str, Any] = {}
    if user_id:
        try:
            query["user_id"] = object_id(user_id)
        except ValueError:
            raise ApiError(422, "invalid_id", "user_id is invalid") from None
    if active_only:
        query["revoked_at"] = None
        query["expires_at"] = {"$gt": now}

    docs = await db.auth_sessions.find(query).sort("last_used_at", -1).limit(limit).to_list()
    user_ids = {doc["user_id"] for doc in docs if doc.get("user_id")}
    users: dict[Any, dict] = {}
    if user_ids:
        user_docs = await db.users.find({"_id": {"$in": list(user_ids)}}).to_list(
            length=None
        )
        users = {user["_id"]: user for user in user_docs}

    current_id = request.state.session.get("_id") if hasattr(request.state, "session") else None
    return json_safe(
        [
            _public_session(doc, users.get(doc.get("user_id")), current_id=current_id)
            for doc in docs
        ]
    )


@sessions.post("/{session_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: str, request: Request, admin: Admin
) -> Response:
    db = _database(request)
    try:
        oid = object_id(session_id)
    except ValueError:
        raise ApiError(422, "invalid_id", "session_id is invalid") from None
    now = utcnow()
    current_id = request.state.session.get("_id")
    if current_id == oid:
        raise ApiError(400, "cannot_revoke_current", "Use Log Out to end your own session")

    result = await db.auth_sessions.update_one(
        {"_id": oid, "revoked_at": None},
        {"$set": {"revoked_at": now}},
    )
    if result.matched_count == 0:
        existing = await db.auth_sessions.find_one({"_id": oid})
        if not existing:
            raise ApiError(404, "not_found", "Session not found")
        # Already revoked — idempotent success
        return Response(status_code=204)

    session = await db.auth_sessions.find_one({"_id": oid})
    await record_operation_event(
        db,
        action="session.revoke",
        outcome="ok",
        actor=admin,
        request=request,
        entity_type="session",
        entity_ids=[str(oid)],
        details={"target_user_id": str((session or {}).get("user_id") or "")},
    )
    return Response(status_code=204)


@sessions.post("/revoke-user/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user_sessions(
    user_id: str, request: Request, admin: Admin
) -> Response:
    db = _database(request)
    try:
        oid = object_id(user_id)
    except ValueError:
        raise ApiError(422, "invalid_id", "user_id is invalid") from None
    now = utcnow()
    current_id = request.state.session.get("_id")
    query: dict[str, Any] = {"user_id": oid, "revoked_at": None}
    # Keep caller's own session if they revoke themselves.
    if current_id and request.state.session.get("user_id") == oid:
        query["_id"] = {"$ne": current_id}

    result = await db.auth_sessions.update_many(query, {"$set": {"revoked_at": now}})
    await record_operation_event(
        db,
        action="session.revoke_user",
        outcome="ok",
        actor=admin,
        request=request,
        entity_type="user",
        entity_ids=[str(oid)],
        details={"revoked_count": int(result.modified_count)},
    )
    return Response(status_code=204)


@sessions.post("/revoke-all", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_all_sessions(request: Request, admin: Admin) -> Response:
    """Revoke every active session except the caller's."""
    db = _database(request)
    now = utcnow()
    current_id = request.state.session.get("_id")
    query: dict[str, Any] = {"revoked_at": None}
    if current_id:
        query["_id"] = {"$ne": current_id}
    result = await db.auth_sessions.update_many(query, {"$set": {"revoked_at": now}})
    await record_operation_event(
        db,
        action="session.revoke_all",
        outcome="ok",
        actor=admin,
        request=request,
        entity_type="session",
        details={"revoked_count": int(result.modified_count)},
    )
    return Response(status_code=204)
