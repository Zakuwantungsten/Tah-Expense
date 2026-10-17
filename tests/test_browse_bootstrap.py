"""Browse Phase 3 — period bootstrap client parsing / legacy fallback."""

from __future__ import annotations

import asyncio

from tahmeed.services import cashier_service as svc


def test_get_browse_bootstrap_parses_composite(monkeypatch) -> None:
    async def fake_request(method, path, **_kwargs):
        assert method == "GET"
        assert path == "v1/transactions/meta/bootstrap"
        return {
            "years": [2026, 2025],
            "months": [{"year": 2026, "month": 3}, {"year": 2025, "month": 12}],
            "total_years": 2,
            "total_months": 2,
        }

    monkeypatch.setattr(svc.api_client, "request", fake_request)
    boot = asyncio.run(svc.get_browse_bootstrap())
    assert boot.years == [2026, 2025]
    assert boot.months == [(2026, 3), (2025, 12)]


def test_get_browse_bootstrap_falls_back_to_legacy(monkeypatch) -> None:
    async def boom(*_a, **_k):
        raise RuntimeError("no bootstrap")

    async def years():
        return [2026]

    async def months():
        return [(2026, 5)]

    monkeypatch.setattr(svc.api_client, "request", boom)
    monkeypatch.setattr(svc, "get_available_years", years)
    monkeypatch.setattr(svc, "get_available_months", months)
    boot = asyncio.run(svc.get_browse_bootstrap())
    assert boot.years == [2026]
    assert boot.months == [(2026, 5)]
