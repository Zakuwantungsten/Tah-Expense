"""Master Expenses in-place edit helpers and service writes."""

from __future__ import annotations

import asyncio
from datetime import datetime

from bson import ObjectId

from tahmeed.services import accountant_service


def test_prepare_master_updates_syncs_item_date_and_ref_float() -> None:
    dt = datetime(2026, 3, 15)
    prepared = accountant_service.prepare_master_updates({
        "date": dt,
        "item": "Parking",
        "ref_float": "refund to float",
        "receipt_status": "No Receipt",
        "hacked": "nope",
    })
    assert "hacked" not in prepared
    assert prepared["date"] == dt
    assert prepared["month"] == "Mar 26"
    assert prepared["year"] == 2026
    assert prepared["item"] == "Parking"
    assert prepared["category_name"] == "Parking"
    assert prepared["ref_float"] == "refund to float"
    assert prepared["notes_flag"] is True
    assert prepared["receipt_status"] == "no_receipt"


def test_prepare_master_updates_empty_is_noop() -> None:
    assert accountant_service.prepare_master_updates({}) == {}
    assert accountant_service.prepare_master_updates({"unknown": 1}) == {}


def test_update_master_transaction_calls_api(monkeypatch) -> None:
    calls: list[tuple] = []

    async def request(method: str, path: str, *, json: dict | None = None, **_kw):
        calls.append((method, path, json))
        return {"ok": True}

    monkeypatch.setattr(accountant_service.api_client, "request", request)
    tx_id = ObjectId()

    ok = asyncio.run(
        accountant_service.update_master_transaction(
            tx_id,
            {"description": "Updated", "truck_number": "T 123"},
            ObjectId(),
        )
    )

    assert ok is True
    assert len(calls) == 1
    method, path, body = calls[0]
    assert method == "PATCH"
    assert path == f"v1/accountant/master/{tx_id}"
    assert body["updates"]["description"] == "Updated"
    assert body["updates"]["truck_number"] == "T 123"


def test_bulk_update_master_transactions_calls_api(monkeypatch) -> None:
    calls: list[tuple] = []

    async def request(method: str, path: str, *, json: dict | None = None, **_kw):
        calls.append((method, path, json))
        return {"updated": 2}

    monkeypatch.setattr(accountant_service.api_client, "request", request)
    ids = [ObjectId(), ObjectId()]

    n = asyncio.run(
        accountant_service.bulk_update_master_transactions(
            ids,
            {"ownership": "OWNED", "receipt_status": "received"},
            ObjectId(),
        )
    )

    assert n == 2
    method, path, body = calls[0]
    assert method == "POST"
    assert path == "v1/accountant/master/bulk-update"
    assert body["ids"] == [str(i) for i in ids]
    assert body["updates"]["ownership"] == "OWNED"
    assert body["updates"]["receipt_status"] == "received"


def test_bulk_update_master_transactions_empty_ids(monkeypatch) -> None:
    called = False

    async def request(*_a, **_k):
        nonlocal called
        called = True
        return {"updated": 0}

    monkeypatch.setattr(accountant_service.api_client, "request", request)
    n = asyncio.run(
        accountant_service.bulk_update_master_transactions([], {"memo": "x"})
    )
    assert n == 0
    assert called is False
