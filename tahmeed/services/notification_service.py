"""API-backed notification counts for the accountant desktop."""

from tahmeed.services.api_client import api_client


async def get_verify_notification_count() -> int:
    payload = await api_client.request("GET", "v1/accountant/notification-counts")
    return int(payload["verify"])
