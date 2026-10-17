"""Client bulk_save_transactions / check_for_duplicates_bulk helpers."""

from __future__ import annotations

from datetime import datetime

import pytest
from bson import ObjectId

from tahmeed.models.transaction import Transaction
from tahmeed.services.api_client import ApiConnectionError


@pytest.mark.asyncio
async def test_bulk_save_transactions_maps_results(monkeypatch):
    from tahmeed.services import cashier_service as svc

    id_a, id_b = ObjectId(), ObjectId()

    async def fake_request(method, path, **kwargs):
        assert path == "v1/transactions/bulk-create"
        items = kwargs["json"]["items"]
        assert len(items) == 2
        return {
            "results": [
                {
                    "index": 0,
                    "status": "created",
                    "transaction": {
                        "_id": str(id_a),
                        "date": "2026-08-08T00:00:00",
                        "description": "A",
                        "truck_number": "",
                        "amount": 1.0,
                        "currency": "TZS",
                        "client_row_id": items[0]["client_row_id"],
                        "register_status": "draft",
                        "created_at": "2026-08-08T00:00:00",
                    },
                    "error": None,
                },
                {
                    "index": 1,
                    "status": "already_exists",
                    "transaction": {
                        "_id": str(id_b),
                        "date": "2026-08-08T00:00:00",
                        "description": "B",
                        "truck_number": "",
                        "amount": 2.0,
                        "currency": "TZS",
                        "client_row_id": items[1]["client_row_id"],
                        "register_status": "draft",
                        "created_at": "2026-08-08T00:00:00",
                    },
                    "error": None,
                },
            ],
            "created": 1,
            "already_exists": 1,
            "errors": 0,
        }

    audit_calls: list = []

    async def capture_audit(*a, **k):
        audit_calls.append((a, k))

    monkeypatch.setattr(svc.api_client, "request", fake_request)
    monkeypatch.setattr(
        "tahmeed.services.audit_service.record_event",
        capture_audit,
    )

    txs = [
        Transaction(
            date=datetime(2026, 8, 8),
            description="A",
            truck_number="",
            amount=1.0,
            cashier_id=ObjectId(),
            client_row_id="rid-a",
        ),
        Transaction(
            date=datetime(2026, 8, 8),
            description="B",
            truck_number="",
            amount=2.0,
            cashier_id=ObjectId(),
            client_row_id="rid-b",
        ),
    ]
    outcomes = await svc.bulk_save_transactions(txs)
    assert outcomes[0][0] == "created"
    assert outcomes[0][1]._id == id_a
    assert outcomes[1][0] == "already_exists"
    assert txs[0]._id == id_a
    assert txs[1]._id == id_b
    # Client no longer fires a second audit RTT (server txn.bulk_create covers it).
    assert audit_calls == []


@pytest.mark.asyncio
async def test_bulk_save_replays_chunk_on_timeout(monkeypatch):
    from tahmeed.services import cashier_service as svc

    calls = {"n": 0}
    saved_id = ObjectId()

    async def fake_request(method, path, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ApiConnectionError("timed out", code="timeout")
        items = kwargs["json"]["items"]
        return {
            "results": [
                {
                    "index": 0,
                    "status": "already_exists",
                    "transaction": {
                        "_id": str(saved_id),
                        "date": "2026-08-08T00:00:00",
                        "description": "A",
                        "truck_number": "",
                        "amount": 1.0,
                        "currency": "TZS",
                        "client_row_id": items[0]["client_row_id"],
                        "register_status": "draft",
                        "created_at": "2026-08-08T00:00:00",
                    },
                    "error": None,
                }
            ],
            "created": 0,
            "already_exists": 1,
            "errors": 0,
        }

    monkeypatch.setattr(svc.api_client, "request", fake_request)

    tx = Transaction(
        date=datetime(2026, 8, 8),
        description="A",
        truck_number="",
        amount=1.0,
        cashier_id=ObjectId(),
        client_row_id="rid-a",
    )
    outcomes = await svc.bulk_save_transactions([tx])
    assert calls["n"] == 2
    assert outcomes[0][0] == "already_exists"
    assert tx._id == saved_id


@pytest.mark.asyncio
async def test_bulk_save_chunks_at_api_max(monkeypatch):
    """Client posts at most BULK_CREATE_CHUNK (= API max 100) items per request."""
    from tahmeed.services import cashier_service as svc

    assert svc.BULK_CREATE_CHUNK == 100
    sizes: list[int] = []

    async def fake_request(method, path, **kwargs):
        items = kwargs["json"]["items"]
        sizes.append(len(items))
        return {
            "results": [
                {
                    "index": i,
                    "status": "created",
                    "transaction": {
                        "_id": str(ObjectId()),
                        "date": "2026-08-08T00:00:00",
                        "description": f"R{i}",
                        "truck_number": "",
                        "amount": 1.0,
                        "currency": "TZS",
                        "client_row_id": it["client_row_id"],
                        "register_status": "draft",
                        "created_at": "2026-08-08T00:00:00",
                    },
                    "error": None,
                }
                for i, it in enumerate(items)
            ],
            "created": len(items),
            "already_exists": 0,
            "errors": 0,
        }

    monkeypatch.setattr(svc.api_client, "request", fake_request)
    cashier = ObjectId()
    txs = [
        Transaction(
            date=datetime(2026, 8, 8),
            description=f"R{i}",
            truck_number="",
            amount=1.0,
            cashier_id=cashier,
            client_row_id=f"rid-{i}",
        )
        for i in range(150)
    ]
    outcomes = await svc.bulk_save_transactions(txs)
    assert len(outcomes) == 150
    assert all(o[0] == "created" for o in outcomes)
    assert sizes == [100, 50]


@pytest.mark.asyncio
async def test_check_for_duplicates_bulk_aligns(monkeypatch):
    from tahmeed.services import cashier_service as svc

    async def fake_request(method, path, **kwargs):
        assert path == "v1/transactions/check-duplicates/bulk"
        return {
            "results": [
                {
                    "index": 0,
                    "items": [
                        {
                            "_id": str(ObjectId()),
                            "date": "2026-08-08T00:00:00",
                            "description": "DIESEL",
                            "truck_number": "T1",
                            "amount": 1000.0,
                            "currency": "TZS",
                            "created_at": "2026-08-08T00:00:00",
                        }
                    ],
                    "total": 1,
                },
                {"index": 1, "items": [], "total": 0},
            ],
            "total": 2,
        }

    monkeypatch.setattr(svc.api_client, "request", fake_request)
    hits = await svc.check_for_duplicates_bulk(
        [
            {
                "truck_number": "T1",
                "amount": 1000.0,
                "item": "FUEL",
                "description": "DIESEL",
            },
            {
                "truck_number": "T2",
                "amount": 5.0,
                "item": "X",
                "description": "Y",
            },
        ],
        days=5,
    )
    assert len(hits) == 2
    assert len(hits[0]) == 1
    assert hits[0][0].description == "DIESEL"
    assert hits[1] == []
