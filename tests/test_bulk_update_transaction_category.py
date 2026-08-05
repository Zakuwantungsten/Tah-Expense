"""bulk_update_transaction_category — one round-trip for many tx ids."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bson import ObjectId

from tahmeed.services import accountant_service


class _FakeResult:
    def __init__(self, modified: int) -> None:
        self.modified_count = modified


class _FakeTransactions:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict]] = []

    async def update_many(self, query: dict, update: dict) -> _FakeResult:
        self.calls.append((query, update))
        return _FakeResult(len(query.get("_id", {}).get("$in", [])))


def test_bulk_update_transaction_category_sets_item_and_category(monkeypatch) -> None:
    txs = _FakeTransactions()
    monkeypatch.setattr(
        accountant_service, "get_db", lambda: SimpleNamespace(transactions=txs),
    )
    ids = [ObjectId(), ObjectId(), ObjectId()]
    cat_id = ObjectId()

    modified = asyncio.run(
        accountant_service.bulk_update_transaction_category(
            ids, "Parking", cat_id,
        )
    )

    assert modified == 3
    assert len(txs.calls) == 1
    query, update = txs.calls[0]
    assert query == {"_id": {"$in": ids}}
    assert update == {
        "$set": {
            "category_name": "Parking",
            "item": "Parking",
            "category_id": cat_id,
        }
    }


def test_bulk_update_transaction_category_empty_ids_is_noop(monkeypatch) -> None:
    txs = _FakeTransactions()
    monkeypatch.setattr(
        accountant_service, "get_db", lambda: SimpleNamespace(transactions=txs),
    )
    modified = asyncio.run(
        accountant_service.bulk_update_transaction_category([], "Parking", ObjectId())
    )
    assert modified == 0
    assert txs.calls == []
