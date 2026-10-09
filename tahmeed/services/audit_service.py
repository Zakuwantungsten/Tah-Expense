"""Append-only operation_events audit trail for desktop business actions."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional, Sequence

from bson import ObjectId
from bson.errors import InvalidId

from tahmeed.config import APP_VERSION
from tahmeed.services.api_client import api_client

logger = logging.getLogger("tahmeed.audit")


def _as_id_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return str(value)
    text = str(value).strip()
    return text or None


def _as_object_id(value: Any) -> Optional[ObjectId]:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError, ValueError):
        return None


async def record_event(
    action: str,
    *,
    actor_id: ObjectId | str | None = None,
    actor_username: str | None = None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_ids: Sequence[ObjectId | str] | None = None,
    upload_id: str | None = None,
    outcome: str = "ok",
    error: str | None = None,
    details: dict | None = None,
    request_id: str | None = None,
) -> Optional[ObjectId]:
    """Insert one audit event via the API. Never raises into callers."""
    try:
        ids = []
        for raw in entity_ids or ():
            s = _as_id_str(raw)
            if s:
                ids.append(s)
        body = {
            "action": action,
            "actor_id": _as_id_str(actor_id),
            "actor_username": actor_username,
            "actor_role": actor_role,
            "entity_type": entity_type,
            "entity_ids": ids,
            "upload_id": upload_id,
            "outcome": outcome,
            "error": error,
            "request_id": request_id or uuid.uuid4().hex,
            "client": "desktop",
            "app_version": APP_VERSION,
            "details": details or {},
        }
        # Drop null optionals so the API schema stays clean.
        payload = {key: value for key, value in body.items() if value is not None}
        response = await api_client.request("POST", "v1/audit/events", json=payload)
        logger.info(
            "audit %s outcome=%s actor=%s entities=%s",
            action,
            outcome,
            body["actor_id"],
            ids[:5],
        )
        if isinstance(response, dict):
            return _as_object_id(response.get("id"))
        return None
    except Exception as exc:
        logger.warning("audit record failed for %s: %s", action, exc)
        return None
