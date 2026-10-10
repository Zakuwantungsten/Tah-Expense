"""Admin session listing and force-logout against the API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tahmeed.services.api_client import api_client


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class SessionInfo:
    id: str
    user_id: str
    username: str
    full_name: str
    role: str
    ip: str
    device_mac: str
    user_agent: str
    created_at: datetime | None
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    is_current: bool

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "SessionInfo":
        return cls(
            id=str(raw.get("id") or raw.get("_id") or ""),
            user_id=str(raw.get("user_id") or ""),
            username=str(raw.get("username") or ""),
            full_name=str(raw.get("full_name") or ""),
            role=str(raw.get("role") or ""),
            ip=str(raw.get("ip") or ""),
            device_mac=str(raw.get("device_mac") or ""),
            user_agent=str(raw.get("user_agent") or ""),
            created_at=_parse_dt(raw.get("created_at")),
            last_used_at=_parse_dt(raw.get("last_used_at")),
            expires_at=_parse_dt(raw.get("expires_at")),
            revoked_at=_parse_dt(raw.get("revoked_at")),
            is_current=bool(raw.get("is_current")),
        )


async def list_sessions(
    *,
    user_id: str | None = None,
    active_only: bool = True,
    limit: int = 200,
) -> list[SessionInfo]:
    params: dict[str, Any] = {"active_only": str(active_only).lower(), "limit": limit}
    if user_id:
        params["user_id"] = user_id
    payload = await api_client.request("GET", "v1/sessions", params=params)
    if not isinstance(payload, list):
        return []
    return [SessionInfo.from_api(item) for item in payload if isinstance(item, dict)]


async def revoke_session(session_id: str) -> None:
    await api_client.request("POST", f"v1/sessions/{session_id}/revoke")


async def revoke_user_sessions(user_id: str) -> None:
    await api_client.request("POST", f"v1/sessions/revoke-user/{user_id}")


async def revoke_all_sessions() -> None:
    await api_client.request("POST", "v1/sessions/revoke-all")
