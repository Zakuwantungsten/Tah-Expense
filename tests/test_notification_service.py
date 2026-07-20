import asyncio

from tahmeed.services import notification_service
from tahmeed.ui.accountant.dashboard import AccountantDashboard


def test_verify_notification_count_uses_authenticated_accountant_endpoint(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def request(method: str, path: str) -> dict:
        calls.append((method, path))
        return {"verify": 12}

    monkeypatch.setattr(notification_service.api_client, "request", request)

    count = asyncio.run(notification_service.get_verify_notification_count())

    assert count == 12
    assert calls == [("GET", "v1/accountant/notification-counts")]


class FakeSidebar:
    def __init__(self) -> None:
        self.counts: list[int] = []

    def set_verify_badge(self, count: int) -> None:
        self.counts.append(count)


def test_dashboard_poll_is_overlap_safe_and_updates_badge(monkeypatch) -> None:
    sidebar = FakeSidebar()
    dashboard = type("DashboardState", (), {})()
    dashboard._sidebar = sidebar
    dashboard._notification_poll_in_flight = True
    poll = AccountantDashboard._poll_notification_counts.__wrapped__

    asyncio.run(poll(dashboard))
    assert sidebar.counts == []

    async def count() -> int:
        return 7

    monkeypatch.setattr(notification_service, "get_verify_notification_count", count)
    dashboard._notification_poll_in_flight = False
    asyncio.run(poll(dashboard))
    assert sidebar.counts == [7]
    assert dashboard._notification_poll_in_flight is False


def test_dashboard_poll_preserves_last_badge_on_network_failure(monkeypatch) -> None:
    sidebar = FakeSidebar()
    dashboard = type("DashboardState", (), {})()
    dashboard._sidebar = sidebar
    dashboard._notification_poll_in_flight = False
    poll = AccountantDashboard._poll_notification_counts.__wrapped__

    async def fail() -> int:
        raise ConnectionError("temporary")

    monkeypatch.setattr(notification_service, "get_verify_notification_count", fail)
    asyncio.run(poll(dashboard))
    assert sidebar.counts == []
    assert dashboard._notification_poll_in_flight is False
