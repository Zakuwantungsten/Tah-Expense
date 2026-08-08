"""Unit tests for connectivity probe helpers (no live network)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tahmeed.services.connectivity_service import (
    ConnectivityStatus,
    probe_connectivity,
)


def test_status_labels():
    online = ConnectivityStatus(api_ok=True, mongo_ok=True)
    assert online.online
    assert online.short_label() == "Connected"
    assert online.banner_message() == ""
    assert online.dot_color() == "#16A34A"

    offline = ConnectivityStatus(api_ok=False, mongo_ok=False)
    assert not offline.online
    assert offline.short_label() == "Offline"
    assert "No connection" in offline.banner_message()
    assert offline.dot_color() == "#DC2626"

    api_down = ConnectivityStatus(api_ok=False, mongo_ok=True)
    assert api_down.degraded
    assert api_down.short_label() == "API unreachable"
    assert "API" in api_down.banner_message()
    assert api_down.dot_color() == "#D97706"

    mongo_down = ConnectivityStatus(api_ok=True, mongo_ok=False)
    assert mongo_down.degraded
    assert mongo_down.short_label() == "Database unreachable"
    assert "database" in mongo_down.banner_message().lower()


@pytest.mark.asyncio
async def test_probe_connectivity_both_ok():
    with (
        patch(
            "tahmeed.services.connectivity_service._probe_api",
            new=AsyncMock(return_value=(True, "")),
        ),
        patch(
            "tahmeed.services.connectivity_service._probe_mongo",
            new=AsyncMock(return_value=(True, "")),
        ),
    ):
        status = await probe_connectivity()
    assert status.online
    assert status.checked_at is not None


@pytest.mark.asyncio
async def test_probe_api_treats_503_db_as_api_up():
    from tahmeed.services.api_client import ApiError
    from tahmeed.services.connectivity_service import _probe_api

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
    assert ok is True
    assert detail == "api_up_db_not_ready"


@pytest.mark.asyncio
async def test_probe_mongo_timeout():
    from tahmeed.services.connectivity_service import _probe_mongo

    client = MagicMock()
    client.admin.command = AsyncMock(side_effect=asyncio.TimeoutError())
    with patch("tahmeed.db.connection.get_client", return_value=client):
        # wait_for will wrap our timeout from the mock if command hangs;
        # simulate command raising TimeoutError via wait_for by making
        # command sleep forever then cancelling — simpler: raise TimeoutError
        # from wait_for by using a slow coroutine.
        async def _slow(*_a, **_k):
            await asyncio.sleep(10)

        client.admin.command = _slow
        ok, detail = await _probe_mongo(0.05)
    assert ok is False
    assert detail == "timeout"
