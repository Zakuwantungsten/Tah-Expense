import asyncio

from tahmeed.services import notification_service
from tahmeed.ui.accountant.dashboard import AccountantDashboard


def test_verify_notification_count_uses_authenticated_accountant_endpoint(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class _Response:
        status_code = 200
        headers = {"etag": '"0"'}

        def json(self) -> dict:
            return {
                "verify": 12,
                "inbox": 10,
                "new": 8,
                "edited": 2,
                "drafts": 0,
                "deletion_requests": 2,
                "restore_requests": 0,
            }

    async def request_response(method: str, path: str, **_kwargs: object) -> _Response:
        calls.append((method, path))
        return _Response()

    monkeypatch.setattr(
        notification_service.api_client, "request_response", request_response
    )
    notification_service._badge_etag = ""

    count = asyncio.run(notification_service.get_verify_notification_count())

    assert count == 12
    assert calls == [("GET", "v1/accountant/notification-counts")]


def test_reset_cache_drops_stale_etag_for_new_dashboard(monkeypatch) -> None:
    sent: list[object] = []

    class _Response:
        status_code = 200
        headers = {"etag": '"1"'}

        def json(self) -> dict:
            return {
                "verify": 4,
                "inbox": 4,
                "new": 4,
                "edited": 0,
                "drafts": 0,
                "deletion_requests": 0,
                "restore_requests": 0,
            }

    async def request_response(method: str, path: str, **kwargs: object) -> _Response:
        sent.append(kwargs.get("headers"))
        return _Response()

    monkeypatch.setattr(
        notification_service.api_client, "request_response", request_response
    )
    notification_service._badge_etag = '"stale"'
    notification_service.reset_verify_notification_cache()

    assert asyncio.run(notification_service.get_verify_notification_count()) == 4
    assert sent == [None]


class _ActiveTimer:
    def isActive(self) -> bool:
        return True


class FakeSidebar:
    def __init__(self) -> None:
        self.counts: list[int] = []
        self.drafts: list[int] = []

    def set_verify_badge(self, count: int) -> None:
        self.counts.append(count)

    def verify_badge(self) -> int:
        return self.counts[-1] if self.counts else 0

    def set_drafts_badge(self, count: int) -> None:
        self.drafts.append(count)

    def drafts_badge(self) -> int:
        return self.drafts[-1] if self.drafts else 0

    def set_saved_badge(self, count: int) -> None:
        self.drafts.append(count)


def test_dashboard_poll_is_overlap_safe_and_updates_badge(monkeypatch) -> None:
    sidebar = FakeSidebar()
    dashboard = type("DashboardState", (), {})()
    dashboard._sidebar = sidebar
    dashboard._notification_timer = _ActiveTimer()
    dashboard._notification_poll_in_flight = True
    dashboard._pages = {}
    dashboard._apply_notification_counts = (
        lambda payload: AccountantDashboard._apply_notification_counts(
            dashboard, payload
        )
    )
    poll = AccountantDashboard._poll_notification_counts_async

    asyncio.run(poll(dashboard))
    assert sidebar.counts == []

    async def counts() -> dict:
        return {
            "verify": 7,
            "inbox": 7,
            "new": 5,
            "edited": 2,
            "drafts": 0,
            "deletion_requests": 0,
            "restore_requests": 0,
        }

    monkeypatch.setattr(notification_service, "get_notification_counts", counts)
    dashboard._notification_poll_in_flight = False
    asyncio.run(poll(dashboard))
    assert sidebar.counts == [7]
    assert dashboard._notification_poll_in_flight is False

    async def unchanged() -> None:
        return None

    monkeypatch.setattr(notification_service, "get_notification_counts", unchanged)
    dashboard._notification_poll_in_flight = False
    asyncio.run(poll(dashboard))
    assert sidebar.counts == [7]


def test_dashboard_poll_preserves_last_badge_on_network_failure(monkeypatch) -> None:
    sidebar = FakeSidebar()
    dashboard = type("DashboardState", (), {})()
    dashboard._sidebar = sidebar
    dashboard._notification_timer = _ActiveTimer()
    dashboard._notification_poll_in_flight = False
    poll = AccountantDashboard._poll_notification_counts_async

    async def fail() -> int:
        raise ConnectionError("temporary")

    monkeypatch.setattr(notification_service, "get_notification_counts", fail)
    asyncio.run(poll(dashboard))
    assert sidebar.counts == []
    assert dashboard._notification_poll_in_flight is False


def test_dashboard_stream_defers_when_nested_task_active(monkeypatch) -> None:
    """Stream start must not create_task while another asyncio task is current."""
    from PySide6.QtCore import QTimer

    created: list[object] = []
    scheduled: list[object] = []

    async def fake_loop() -> None:
        return None

    dashboard = type("DashboardState", (), {})()
    dashboard._notification_live = True
    dashboard._notification_poll_task = None
    dashboard._notification_stream_loop = fake_loop
    dashboard._ensure_notification_stream = (
        lambda: AccountantDashboard._ensure_notification_stream(dashboard)
    )
    dashboard._start_notification_stream = (
        lambda: AccountantDashboard._start_notification_stream(dashboard)
    )

    monkeypatch.setattr(
        "tahmeed.ui.async_utils.in_running_task",
        lambda: True,
    )
    monkeypatch.setattr(
        "tahmeed.ui.async_utils.create_task",
        lambda coro: created.append(coro) or coro.close() or None,
    )
    monkeypatch.setattr(
        QTimer, "singleShot", staticmethod(lambda _ms, cb: scheduled.append(cb))
    )

    dashboard._start_notification_stream()
    assert created == []
    assert len(scheduled) == 1

    monkeypatch.setattr("tahmeed.ui.async_utils.in_running_task", lambda: False)
    dashboard._start_notification_stream()
    assert len(created) == 1


def test_note_verify_queue_bump_lights_sidebar_and_protects(monkeypatch) -> None:
    """Deletion request / pending-edit must bump Verify immediately (like Submit)."""
    from PySide6.QtCore import QTimer

    sidebar = FakeSidebar()
    sidebar.set_verify_badge(3)
    kicks: list[object] = []
    scheduled: list[tuple[int, object]] = []

    dashboard = type("DashboardState", (), {})()
    dashboard._sidebar = sidebar
    dashboard._protect_verify_badge_until = 0.0
    dashboard._kick_notification_refresh = lambda: kicks.append("kick")
    dashboard._apply_draft_badges = lambda _count: None
    dashboard._note_verify_queue_bump = (
        lambda n: AccountantDashboard._note_verify_queue_bump(dashboard, n)
    )
    dashboard._apply_notification_counts = (
        lambda payload: AccountantDashboard._apply_notification_counts(
            dashboard, payload
        )
    )
    dashboard._pages = {}

    monkeypatch.setattr(
        QTimer,
        "singleShot",
        staticmethod(lambda ms, cb: scheduled.append((ms, cb))),
    )

    dashboard._note_verify_queue_bump(2)
    assert sidebar.verify_badge() == 5
    assert dashboard._protect_verify_badge_until > 0
    assert len(scheduled) == 1 and scheduled[0][0] == 200

    # Stale lower poll must not wipe the optimistic bump while protected.
    dashboard._apply_notification_counts({
        "verify": 3,
        "inbox": 3,
        "new": 3,
        "edited": 0,
        "drafts": 0,
        "deletion_requests": 0,
        "restore_requests": 0,
    })
    assert sidebar.verify_badge() == 5
