"""bulk_update_transaction_category — one round-trip for many tx ids."""

from __future__ import annotations

import asyncio

from bson import ObjectId

from tahmeed.services import accountant_service


def _fake_api(monkeypatch, payload):
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return payload

    monkeypatch.setattr(accountant_service.api_client, "request", request)
    return calls


def test_bulk_update_transaction_category_posts_ids_once(monkeypatch) -> None:
    calls = _fake_api(monkeypatch, {"updated": 3})
    ids = [ObjectId(), ObjectId(), ObjectId()]
    cat_id = ObjectId()

    modified = asyncio.run(
        accountant_service.bulk_update_transaction_category(
            ids, "Parking", cat_id, payee="  spare   parts ",
        )
    )

    assert modified == 3
    assert calls == [(
        "POST",
        "v1/accountant/transactions/bulk-category",
        {"json": {
            "ids": [str(i) for i in ids],
            "category_name": "Parking",
            "category_id": str(cat_id),
            "payee": "SPARE PARTS",
        }},
    )]


def test_bulk_update_transaction_category_empty_ids_is_noop(monkeypatch) -> None:
    calls = _fake_api(monkeypatch, {"updated": 99})
    modified = asyncio.run(
        accountant_service.bulk_update_transaction_category([], "Parking", ObjectId())
    )
    assert modified == 0
    assert calls == []
