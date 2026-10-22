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


def test_probe_timeout_is_two_seconds():
    assert _PROBE_TIMEOUT_S == 2.0


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
