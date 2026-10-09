"""Unit tests for connectivity probe helpers (no live network)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tahmeed.services.connectivity_service import (
    ConnectivityStatus,
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

    offline = ConnectivityStatus(api_ok=False)
    assert not offline.online
    assert offline.short_label() == "Offline"
    assert "API" in offline.banner_message()
    assert offline.dot_color() == "#DC2626"

    db_down = ConnectivityStatus(api_ok=False, api_detail="database_unavailable")
    assert not db_down.online
    assert db_down.short_label() == "Server unavailable"
    assert "database" in db_down.banner_message().lower()


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
async def test_probe_api_treats_503_db_as_offline():
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
    assert ok is False
    assert detail == "database_unavailable"
