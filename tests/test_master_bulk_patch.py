"""Master Expenses Save — one bulk-patch for dirty rows."""

from __future__ import annotations

import asyncio

from bson import ObjectId

from tahmeed.services import accountant_service as svc


def test_bulk_patch_master_transactions_one_request(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, "json": kwargs.get("json")})
        return {"updated": 2, "matched": 2, "requested": 2}

    monkeypatch.setattr(svc.api_client, "request", fake_request)
    a, b = ObjectId(), ObjectId()
    result = asyncio.run(
        svc.bulk_patch_master_transactions(
            [
                (a, {"description": "A2", "memo": "x"}),
                (b, {"item": "Parking", "category_name": "Parking"}),
            ]
        )
    )
    assert result["matched"] == 2
    assert len(calls) == 1
    assert calls[0]["path"] == "v1/accountant/master/bulk-patch"
    assert calls[0]["method"] == "POST"
    items = calls[0]["json"]["items"]
    assert {item["id"] for item in items} == {str(a), str(b)}
    assert items[0]["updates"]["description"] == "A2" or items[1]["updates"].get(
        "description"
    ) == "A2"


def test_bulk_patch_master_transactions_empty_is_noop(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_request(method, path, **kwargs):
        calls.append({})
        return {"updated": 0}

    monkeypatch.setattr(svc.api_client, "request", fake_request)
    result = asyncio.run(svc.bulk_patch_master_transactions([]))
    assert result == {"updated": 0, "matched": 0, "requested": 0}
    assert calls == []
