"""save_transaction replays once on timeout when client_row_id is set."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId

from tahmeed.models.transaction import Transaction
from tahmeed.services.api_client import ApiConnectionError


@pytest.mark.asyncio
async def test_save_transaction_replays_after_timeout(monkeypatch):
    from tahmeed.services import cashier_service as svc

    calls: list[dict] = []
    saved_id = ObjectId()
    rid = "11111111-2222-3333-4444-555555555555"

    async def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, "json": kwargs.get("json")})
        if len(calls) == 1:
            raise ApiConnectionError("timed out", code="timeout")
        body = kwargs.get("json") or {}
        return {
            "_id": str(saved_id),
            "date": body.get("date") or datetime(2026, 8, 8),
            "description": body.get("description") or "DIESEL",
            "truck_number": body.get("truck_number") or "",
            "amount": body.get("amount") or 1000.0,
            "currency": body.get("currency") or "TZS",
            "cashier_id": body.get("cashier_id"),
            "register_status": "draft",
            "client_row_id": body.get("client_row_id"),
            "created_at": datetime(2026, 8, 8),
        }

    monkeypatch.setattr(svc.api_client, "request", fake_request)
    monkeypatch.setattr(
        "tahmeed.services.audit_service.record_event",
        lambda *a, **k: None,
    )

    tx = Transaction(
        date=datetime(2026, 8, 8),
        description="DIESEL",
        truck_number="T100",
        amount=1000.0,
        cashier_id=ObjectId(),
        client_row_id=rid,
    )
    saved = await svc.save_transaction(tx)
    assert len(calls) == 2
    assert calls[0]["json"]["client_row_id"] == rid
    assert calls[1]["json"]["client_row_id"] == rid
    assert saved._id == saved_id
    assert tx._id == saved_id


@pytest.mark.asyncio
async def test_save_transaction_does_not_replay_without_client_row_id(monkeypatch):
    from tahmeed.services import cashier_service as svc

    async def fake_request(*_a, **_k):
        raise ApiConnectionError("timed out", code="timeout")

    monkeypatch.setattr(svc.api_client, "request", fake_request)

    tx = Transaction(
        date=datetime(2026, 8, 8),
        description="DIESEL",
        truck_number="T100",
        amount=1000.0,
        cashier_id=ObjectId(),
    )
    with pytest.raises(ApiConnectionError):
        await svc.save_transaction(tx)
