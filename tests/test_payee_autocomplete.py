"""Payee catalog suggestions for the register Payee column."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tahmeed.services import payee_service as ps
from tahmeed.ui.cashier.excel_grid import DailyRegister


def test_search_payees_sync_contains_and_prefix() -> None:
    ps.invalidate_payee_cache()
    ps._payee_list = ["PARKING DAR", "SPARE PARTS", "ROADMASTERS SPARES LTD", "MILEAGE"]
    ps._payee_set = set(ps._payee_list)
    assert ps.search_payees_sync("park") == ["PARKING DAR"]
    assert ps.search_payees_sync("spare")[0] == "SPARE PARTS"
    assert "ROADMASTERS SPARES LTD" in (ps.search_payees_sync("spares") or [])
    ps.invalidate_payee_cache()
    assert ps.search_payees_sync("park") is None


def test_update_payees_normalizes_names() -> None:
    stub = SimpleNamespace(_payee_names=[])
    DailyRegister.update_payees(stub, [" Spare Parts ", "parking dar", ""])
    assert stub._payee_names == ["SPARE PARTS", "PARKING DAR"]


@pytest.mark.asyncio
async def test_get_payee_names_uses_cache(monkeypatch) -> None:
    from tahmeed.services import catalog_service

    ps.invalidate_payee_cache()
    catalog_service._body = None
    catalog_service._stale = False
    calls = {"n": 0}

    async def fake_snapshot(*, refresh: bool = False):
        calls["n"] += 1
        return {"payees": [{"name": "FINE", "active": True}]}

    monkeypatch.setattr(catalog_service, "fetch_catalog_snapshot", fake_snapshot)
    names = await ps.get_payee_names()
    assert names == ["FINE"]
    assert await ps.get_payee_names() == ["FINE"]
    assert calls["n"] == 1
    ps.invalidate_payee_cache()
    catalog_service._body = None
