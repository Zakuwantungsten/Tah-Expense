"""Unit tests for connectivity probe helpers (no live network)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from tahmeed.services.connectivity_service import (
    ConnectivityStatus,
    _PROBE_TIMEOUT_S,
    _probe_api,
    _status_while_busy,
    connectivity_monitor,
    probe_connectivity,
)


def test_status_labels():
    online = ConnectivityStatus(api_ok=True)
    assert online.online
    assert not online.degraded
    assert online.short_label() == "Connected"
    assert online.banner_message() == ""
    assert online.dot_color() == "#16A34A"
    assert "API" in online.status_bar_text()

    offline = ConnectivityStatus(api_ok=False, api_detail="connection_error")
    assert not offline.online
    assert offline.short_label() == "Offline"
    assert "API" in offline.banner_message()
    assert offline.dot_color() == "#DC2626"

    db_down = ConnectivityStatus(api_ok=False, api_detail="database_unavailable")
    assert not db_down.online
    assert db_down.short_label() == "Server unavailable"
    assert "database" in db_down.banner_message().lower()

    os_off = ConnectivityStatus(api_ok=False, api_detail="os_offline")
    assert not os_off.online
    assert "internet" in os_off.banner_message().lower()


def test_probe_timeout_covers_a_cold_tls_handshake():
    assert _PROBE_TIMEOUT_S == 6.0


@pytest.mark.asyncio
async def test_single_failed_probe_does_not_publish_offline(monkeypatch):
    from tahmeed.signals import app_signals

    results = iter([
        ConnectivityStatus(api_ok=True),
        ConnectivityStatus(api_ok=False, api_detail="timeout"),
        ConnectivityStatus(api_ok=True),
        ConnectivityStatus(api_ok=False, api_detail="timeout"),
        ConnectivityStatus(api_ok=False, api_detail="timeout"),
    ])
    monkeypatch.setattr(
        "tahmeed.services.connectivity_service.probe_connectivity",
        AsyncMock(side_effect=lambda: next(results)),
    )
    published: list[bool] = []

    def _record(status):
        published.append(status.online)

    app_signals.connectivity_changed.connect(_record)
    mon = connectivity_monitor
    mon.stop()
    mon._started = True
    try:
        for _ in range(5):
            await mon._check_async()
    finally:
        mon.stop()
        app_signals.connectivity_changed.disconnect(_record)
    assert published == [True, True, False]


def test_start_defers_the_first_probe(monkeypatch):
    _app = QApplication.instance() or QApplication([])
    fake_qtimer = MagicMock()
    monkeypatch.setattr(
        "tahmeed.services.connectivity_service.QTimer", fake_qtimer
    )
    mon = connectivity_monitor
    mon.stop()
    mon._busy_depth = 0
    try:
        mon.start()
        fake_qtimer.singleShot.assert_called_once_with(
            mon.FIRST_PROBE_DELAY_MS, mon._on_tick
        )
        assert mon._task is None
    finally:
        mon.stop()


def test_loopback_banners_do_not_blame_the_internet(monkeypatch):
    monkeypatch.setattr(
        "tahmeed.services.connectivity_service._loopback_api",
        lambda: True,
    )
    timeout = ConnectivityStatus(api_ok=False, api_detail="timeout")
    refused = ConnectivityStatus(api_ok=False, api_detail="connection_error")
    busy = ConnectivityStatus(api_ok=False, api_detail="busy")

    assert "internet" not in timeout.banner_message().lower()
    assert "did not answer" in timeout.banner_message().lower()
    assert "not running" in refused.banner_message().lower()
    assert "internet" not in refused.banner_message().lower()
    assert busy.banner_message() == (
        "The app is still working. Connection was not lost."
    )
    assert "internet" not in busy.banner_message().lower()
    assert busy.short_label() == "Working"
    assert busy.degraded
    assert busy.dot_color() == "#B45309"


def test_remote_timeout_mentions_internet(monkeypatch):
    monkeypatch.setattr(
        "tahmeed.services.connectivity_service._loopback_api",
        lambda: False,
    )
    timeout = ConnectivityStatus(api_ok=False, api_detail="timeout")
    assert "internet" in timeout.banner_message().lower()


def test_timeout_during_work_becomes_busy():
    checked = datetime(2026, 10, 22, 12, 0, 0)
    raw = ConnectivityStatus(api_ok=False, checked_at=checked, api_detail="timeout")
    busy = _status_while_busy(raw, True)
    assert busy.api_detail == "busy"
    assert busy.checked_at == checked
    refused = ConnectivityStatus(
        api_ok=False, checked_at=checked, api_detail="connection_error"
    )
    assert _status_while_busy(refused, True).api_detail == "connection_error"
    assert _status_while_busy(raw, False).api_detail == "timeout"


def test_nested_pause_does_not_resume_the_light_early(monkeypatch):
    _app = QApplication.instance() or QApplication([])
    idle = MagicMock()
    idle.done.return_value = True

    def _no_probe(coro):
        coro.close()
        return idle

    monkeypatch.setattr(
        "tahmeed.services.connectivity_service.create_task",
        _no_probe,
    )
    mon = connectivity_monitor
    mon.stop()
    mon._busy_depth = 0
    mon._paused = False
    mon._started = True
    mon._timer.start()
    try:
        mon.pause()
        mon.pause()
        mon.resume()
        assert mon._paused
        assert mon._busy_depth == 1
        assert not mon._timer.isActive()
        mon.resume()
        assert mon._busy_depth == 0
        assert not mon._paused
    finally:
        mon.stop()
        mon._busy_depth = 0
        mon._paused = False
        _app.processEvents()


def test_stop_resets_busy_depth_so_next_start_is_not_stuck():
    _app = QApplication.instance() or QApplication([])
    mon = connectivity_monitor
    mon.stop()
    mon._started = True
    mon.pause()
    assert mon._busy_depth == 1
    mon.stop()
    assert mon._busy_depth == 0
    assert not mon._paused
    assert mon.status is None
    assert _app is not None


def test_transport_failure_publishes_offline_immediately():
    from tahmeed.signals import app_signals

    mon = connectivity_monitor
    mon.stop()
    mon._started = True
    published: list[ConnectivityStatus] = []

    def _record(status):
        published.append(status)

    app_signals.connectivity_changed.connect(_record)
    try:
        mon.report_transport_failure(detail="connection_error")
    finally:
        app_signals.connectivity_changed.disconnect(_record)
        mon.stop()
    assert len(published) == 1
    assert not published[0].online
    assert published[0].api_detail == "connection_error"


def test_transport_timeout_while_busy_is_amber_not_offline():
    from tahmeed.signals import app_signals

    mon = connectivity_monitor
    mon.stop()
    mon._started = True
    mon.pause()
    published: list[ConnectivityStatus] = []

    def _record(status):
        published.append(status)

    app_signals.connectivity_changed.connect(_record)
    try:
        mon.report_transport_failure(detail="timeout")
    finally:
        app_signals.connectivity_changed.disconnect(_record)
        mon.stop()
    assert len(published) == 1
    assert published[0].api_detail == "busy"
    assert published[0].degraded


def test_os_disconnect_ignored_for_loopback(monkeypatch):
    from tahmeed.signals import app_signals

    monkeypatch.setattr(
        "tahmeed.services.connectivity_service._loopback_api",
        lambda: True,
    )
    mon = connectivity_monitor
    mon.stop()
    mon._started = True
    published: list[ConnectivityStatus] = []

    def _record(status):
        published.append(status)

    app_signals.connectivity_changed.connect(_record)
    try:
        mon.report_os_reachability(disconnected=True)
    finally:
        app_signals.connectivity_changed.disconnect(_record)
        mon.stop()
    assert published == []


def test_os_disconnect_marks_offline_for_remote_api(monkeypatch):
    from tahmeed.signals import app_signals

    monkeypatch.setattr(
        "tahmeed.services.connectivity_service._loopback_api",
        lambda: False,
    )
    mon = connectivity_monitor
    mon.stop()
    mon._started = True
    published: list[ConnectivityStatus] = []

    def _record(status):
        published.append(status)

    app_signals.connectivity_changed.connect(_record)
    try:
        mon.report_os_reachability(disconnected=True)
    finally:
        app_signals.connectivity_changed.disconnect(_record)
        mon.stop()
    assert len(published) == 1
    assert published[0].api_detail == "os_offline"


@pytest.mark.asyncio
async def test_probe_connectivity_ok():
    with patch(
        "tahmeed.services.connectivity_service._probe_api",
        new=AsyncMock(return_value=(True, "")),
    ):
        status = await probe_connectivity()
    assert status.online
    assert status.checked_at is not None


@pytest.mark.asyncio
async def test_probe_api_live_200_does_not_need_a_database():
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("tahmeed.services.api_client.api_client", mock_client):
        ok, detail = await _probe_api(2.0)
    assert ok is True
    assert detail == ""
    mock_client.request.assert_awaited_once_with(
        "GET", "health/live", auth=False
    )


@pytest.mark.asyncio
async def test_probe_api_treats_503_db_as_offline():
    from tahmeed.services.api_client import ApiError

    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        side_effect=ApiError(
            "Database is not ready",
            status_code=503,
            code="database_unavailable",
        )
    )
    with patch(
        "tahmeed.services.api_client.api_client",
        mock_client,
    ):
        ok, detail = await _probe_api(2.0)
    assert ok is False
    assert detail == "database_unavailable"
    mock_client.request.assert_awaited_once_with(
        "GET", "health/live", auth=False
    )


@pytest.mark.asyncio
async def test_probe_api_connection_refused_is_not_a_timeout():
    from tahmeed.services.api_client import ApiConnectionError

    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        side_effect=ApiConnectionError(
            "Unable to connect",
            code="connection_error",
        )
    )
    with patch("tahmeed.services.api_client.api_client", mock_client):
        ok, detail = await _probe_api(2.0)
    assert ok is False
    assert detail == "connection_error"
