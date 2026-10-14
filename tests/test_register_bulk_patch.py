"""Register Save — bulk-patch dirty rows + no per-row client audit by default."""

from __future__ import annotations

import asyncio

from bson import ObjectId

from tahmeed.services import cashier_service as cs


def test_bulk_update_transactions_one_request(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, "json": kwargs.get("json")})
        return {"updated": 2, "matched": 2, "requested": 2}

    monkeypatch.setattr(cs.api_client, "request", fake_request)
    a, b = ObjectId(), ObjectId()
    n = asyncio.run(
        cs.bulk_update_transactions(
            [
                (a, {"payee": "CITY", "item": "Parking"}),
                (b, {"description": "LATRA FEE"}),
            ]
        )
    )
    assert n == 2
    assert len(calls) == 1
    assert calls[0]["path"] == "v1/transactions/bulk-patch"
    assert calls[0]["method"] == "POST"
    items = calls[0]["json"]["items"]
    assert {item["id"] for item in items} == {str(a), str(b)}


def test_update_transaction_skips_client_audit_by_default(monkeypatch) -> None:
    calls: list[str] = []
    audits: list[str] = []

    async def fake_request(method, path, **kwargs):
        calls.append(f"{method} {path}")
        return {"ok": True}

    async def fake_record(*_a, **_k):
        audits.append("hit")

    monkeypatch.setattr(cs.api_client, "request", fake_request)
    monkeypatch.setattr(
        "tahmeed.services.audit_service.record_event",
        fake_record,
    )
    ok = asyncio.run(
        cs.update_transaction(ObjectId(), {"payee": "X", "last_edited_by": ObjectId()})
    )
    assert ok is True
    assert len(calls) == 1
    assert calls[0].startswith("PATCH v1/transactions/")
    assert audits == []


def test_update_transaction_client_audit_opt_in(monkeypatch) -> None:
    audits: list[str] = []

    async def fake_request(method, path, **kwargs):
        return {"ok": True}

    async def fake_record(action, **_k):
        audits.append(action)

    monkeypatch.setattr(cs.api_client, "request", fake_request)
    monkeypatch.setattr(
        "tahmeed.services.audit_service.record_event",
        fake_record,
    )
    asyncio.run(
        cs.update_transaction(
            ObjectId(),
            {"payee": "X"},
            client_audit=True,
        )
    )
    assert audits == ["txn.update"]
