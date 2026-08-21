"""Master Expenses in-place edit helpers and service writes."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from bson import ObjectId

from tahmeed.services import accountant_service


class _FakeResult:
    def __init__(self, matched: int, modified: int | None = None) -> None:
        self.matched_count = matched
        self.modified_count = matched if modified is None else modified


class _FakeTransactions:
    def __init__(self) -> None:
        self.update_one_calls: list[tuple[dict, dict]] = []
        self.update_many_calls: list[tuple[dict, dict]] = []

    async def update_one(self, query: dict, update: dict) -> _FakeResult:
        self.update_one_calls.append((query, update))
        return _FakeResult(1)

    async def update_many(self, query: dict, update: dict) -> _FakeResult:
        self.update_many_calls.append((query, update))
        ids = query.get("_id", {}).get("$in", [])
        return _FakeResult(len(ids))


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


def test_update_master_transaction_sets_verified_filter(monkeypatch) -> None:
    txs = _FakeTransactions()
    monkeypatch.setattr(
        accountant_service, "get_db", lambda: SimpleNamespace(transactions=txs),
    )
    tx_id = ObjectId()
    actor = ObjectId()

    ok = asyncio.run(
        accountant_service.update_master_transaction(
            tx_id,
            {"description": "Updated", "truck_number": "T 123"},
            actor,
        )
    )

    assert ok is True
    assert len(txs.update_one_calls) == 1
    query, update = txs.update_one_calls[0]
    assert query["_id"] == tx_id
    assert query["verified"] is True
    assert query["deletion_requested"] == {"$ne": True}
    assert update["$set"]["description"] == "Updated"
    assert update["$set"]["truck_number"] == "T 123"
    assert update["$set"]["last_edited_by"] == actor
    assert "last_edited_at" in update["$set"]


def test_bulk_update_master_transactions(monkeypatch) -> None:
    txs = _FakeTransactions()
    monkeypatch.setattr(
        accountant_service, "get_db", lambda: SimpleNamespace(transactions=txs),
    )
    ids = [ObjectId(), ObjectId()]

    n = asyncio.run(
        accountant_service.bulk_update_master_transactions(
            ids,
            {"ownership": "OWNED", "receipt_status": "received"},
            ObjectId(),
        )
    )

    assert n == 2
    query, update = txs.update_many_calls[0]
    assert query["_id"]["$in"] == ids
    assert query["verified"] is True
    assert update["$set"]["ownership"] == "OWNED"
    assert update["$set"]["receipt_status"] == "received"


def test_bulk_update_master_transactions_empty_ids(monkeypatch) -> None:
    txs = _FakeTransactions()
    monkeypatch.setattr(
        accountant_service, "get_db", lambda: SimpleNamespace(transactions=txs),
    )
    n = asyncio.run(
        accountant_service.bulk_update_master_transactions([], {"memo": "x"})
    )
    assert n == 0
    assert txs.update_many_calls == []
