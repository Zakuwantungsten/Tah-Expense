"""API-backed notification counts for the accountant desktop."""

from typing import Optional

from tahmeed.services.api_client import api_client

_badge_etag = ""


async def get_verify_notification_count() -> Optional[int]:
    """Stored verify total. ``None`` when ``updated_at`` has not changed."""
    global _badge_etag
    headers = {"If-None-Match": _badge_etag} if _badge_etag else None
    response = await api_client.request_response(
        "GET",
        "v1/accountant/notification-counts",
        headers=headers,
    )
    etag = response.headers.get("etag") or ""
    if etag:
        _badge_etag = etag
    if response.status_code == 304:
        return None
    payload = response.json()
    return int(payload["verify"])
