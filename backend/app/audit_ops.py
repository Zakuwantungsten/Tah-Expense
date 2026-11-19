"""Shared append-only operation_events helpers."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import Request

from .security import utcnow
from .security_policy import client_ip

_SIMPLE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+$")


async def record_operation_event(
    db: Any,
    *,
    action: str,
    outcome: str = "ok",
    actor: dict[str, Any] | None = None,
    actor_id: str | None = None,
    actor_username: str | None = None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_ids: list[str] | None = None,
    error: str | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
    client: str = "api",
    app_version: str | None = None,
) -> None:
    """Best-effort audit insert; never raises into callers."""
    try:
        request_id = None
        ip = None
        if request is not None:
            request_id = getattr(request.state, "request_id", None)
            ip = getattr(request.state, "client_ip", None) or client_ip(request)
        if actor:
            actor_id = actor_id or str(actor.get("_id") or "") or None
            actor_username = actor_username or actor.get("username")
            actor_role = actor_role or actor.get("role")
        payload_details = dict(details or {})
        if ip and "ip" not in payload_details:
            payload_details["ip"] = ip
        await db.operation_events.insert_one(
            {
                "ts": utcnow(),
                "action": action,
                "actor_id": actor_id,
                "actor_username": actor_username,
                "actor_role": actor_role,
                "entity_type": entity_type,
                "entity_ids": list(entity_ids or []),
                "outcome": outcome,
                "error": error,
                "request_id": request_id or uuid.uuid4().hex,
                "client": client,
                "app_version": app_version,
                "details": payload_details,
            }
        )
    except Exception:
        pass


def parse_audit_filters(
    *,
    action: str | None = None,
    actor: str | None = None,
    outcome: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if action:
        token = action.strip()
        escaped = re.escape(token)
        # Chip filters are simple prefixes (auth, user, txn…). Anchored,
        # case-sensitive matches can use action_ts; bare substring + /i cannot.
        if _SIMPLE_TOKEN.fullmatch(token):
            query["action"] = {"$regex": f"^{escaped}"}
        else:
            query["action"] = {"$regex": escaped, "$options": "i"}
    if actor:
        token = actor.strip()
        escaped = re.escape(token)
        if _SIMPLE_TOKEN.fullmatch(token):
            query["actor_username"] = {"$regex": f"^{escaped}$", "$options": "i"}
        else:
            query["actor_username"] = {"$regex": escaped, "$options": "i"}
    if outcome:
        query["outcome"] = outcome.strip()
    if since or until:
        ts: dict[str, Any] = {}
        if since:
            ts["$gte"] = since
        if until:
            ts["$lte"] = until
        query["ts"] = ts
    return query
