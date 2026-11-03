"""API-backed notification counts for the accountant desktop.

Uses the stored ``system_counters`` document. A long-poll wait endpoint delivers
near-realtime updates when badges change; short GETs remain for one-shot reads.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from tahmeed.services.api_client import api_client

_badge_etag = ""


def reset_verify_notification_cache() -> None:
    """Forget the last ETag so the next poll returns the full count.

    A new dashboard starts with an empty badge; a 304 would leave it blank.
    """
    global _badge_etag
    _badge_etag = ""


def _apply_etag(response: httpx.Response) -> None:
    global _badge_etag
    etag = response.headers.get("etag") or ""
    if etag:
        _badge_etag = etag


def _payload_from_json(payload: Any) -> dict[str, int]:
    data = payload if isinstance(payload, dict) else {}
    return {
        "verify": int(data.get("verify") or 0),
        "inbox": int(data.get("inbox") or 0),
        "new": int(data.get("new") or 0),
        "edited": int(data.get("edited") or 0),
        "drafts": int(data.get("drafts") or 0),
        "deletion_requests": int(data.get("deletion_requests") or 0),
        "restore_requests": int(data.get("restore_requests") or 0),
    }


async def get_notification_counts() -> Optional[dict[str, int]]:
    """Stored badge payload. ``None`` when ``updated_at`` has not changed."""
    global _badge_etag
    headers = {"If-None-Match": _badge_etag} if _badge_etag else None
    response = await api_client.request_response(
        "GET",
        "v1/accountant/notification-counts",
        headers=headers,
    )
    _apply_etag(response)
    if response.status_code == 304:
        return None
    return _payload_from_json(response.json())


async def get_verify_notification_count() -> Optional[int]:
    """Sidebar verify total. ``None`` when ``updated_at`` has not changed."""
    payload = await get_notification_counts()
    if payload is None:
        return None
    return int(payload["verify"])


async def wait_notification_counts(*, timeout: float = 25.0) -> dict[str, int]:
    """Block until badges change (or timeout), then return the full payload."""
    global _badge_etag
    params = {"timeout": str(timeout)}
    if _badge_etag:
        params["etag"] = _badge_etag.strip().strip('"')
    response = await api_client.request_response(
        "GET",
        "v1/accountant/notification-counts/wait",
        params=params,
        timeout=httpx.Timeout(timeout + 10.0, connect=5.0),
    )
    _apply_etag(response)
    return _payload_from_json(response.json())
