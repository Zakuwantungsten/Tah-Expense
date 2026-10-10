"""Admin audit log reader (operation_events via API)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tahmeed.services.api_client import api_client


@dataclass
class AuditEvent:
    id: str
    ts: datetime | None
    action: str
    actor_username: str
    actor_role: str
    outcome: str
    entity_type: str
    entity_ids: list[str]
    error: str
    client: str
    details: dict[str, Any]

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "AuditEvent":
        ts_raw = raw.get("ts")
        ts = None
        if isinstance(ts_raw, str) and ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                ts = None
        return cls(
            id=str(raw.get("id") or raw.get("_id") or ""),
            ts=ts,
            action=str(raw.get("action") or ""),
            actor_username=str(raw.get("actor_username") or ""),
            actor_role=str(raw.get("actor_role") or ""),
            outcome=str(raw.get("outcome") or ""),
            entity_type=str(raw.get("entity_type") or ""),
            entity_ids=[str(x) for x in (raw.get("entity_ids") or [])],
            error=str(raw.get("error") or ""),
            client=str(raw.get("client") or ""),
            details=dict(raw.get("details") or {}),
        )


async def list_audit_events(
    *,
    limit: int = 100,
    action: str | None = None,
    actor: str | None = None,
    outcome: str | None = None,
) -> list[AuditEvent]:
    params: dict[str, Any] = {"limit": limit}
    if action:
        params["action"] = action
    if actor:
        params["actor"] = actor
    if outcome:
        params["outcome"] = outcome
    payload = await api_client.request("GET", "v1/audit/events", params=params)
    if not isinstance(payload, list):
        return []
    return [AuditEvent.from_api(item) for item in payload if isinstance(item, dict)]
