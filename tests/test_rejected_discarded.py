"""Service tests for rejected / discarded cashier flows."""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import List, Optional

from bson import ObjectId

from tahmeed.models.transaction import Transaction
from tahmeed.services import cashier_service, accountant_service


class _FakeCursor:
    def __init__(self, docs: List[dict]):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def skip(self, *_a, **_k):
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class _FakeCollection:
    def __init__(self, docs: Optional[List[dict]] = None):
        self.docs: List[dict] = list(docs or [])

    def find(self, query: dict):
        return _FakeCursor([d for d in self.docs if _match(d, query)])

    async def find_one(self, query: dict):
        for d in self.docs:
            if _match(d, query):
                return d
        return None

    async def update_one(self, query: dict, update: dict):
        modified = 0
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set", {}))
                modified = 1
                break
        return SimpleNamespace(modified_count=modified)

    async def update_many(self, query: dict, update: dict):
        modified = 0
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set", {}))
                modified += 1
        return SimpleNamespace(modified_count=modified)

    async def delete_many(self, query: dict):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def count_documents(self, query: dict):
        return sum(1 for d in self.docs if _match(d, query))

    async def distinct(self, field: str, query: dict):
        vals = []
        for d in self.docs:
            if _match(d, query) and field in d:
                vals.append(d[field])
        return vals


def _match(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if key == "_id" and isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
            continue
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            continue
        if actual != expected:
            return False
    return True


def _tx_doc(
    *,
    cashier_id: ObjectId,
    rejected: bool = True,
    discarded: bool = False,
    reason: str = "bad amount",
    **extra,
) -> dict:
    doc = {
        "_id": ObjectId(),
        "date": datetime(2026, 7, 1),
        "description": "FUEL",
        "truck_number": "T100 EAF",
        "amount": 50000.0,
        "currency": "TZS",
        "cashier_id": cashier_id,
        "rejected": rejected,
        "discarded": discarded,
        "rejection_reason": reason if rejected else None,
        "verified": False,
        "created_at": datetime(2026, 7, 1, 12, 0, 0),
        "item": "DIESEL",
        "memo": "",
        "receipt_status": "pending",
        "notes_flag": False,
        "ref_float": "",
        "ownership": "",
        "approver": "",
        "payee": "",
        "cheque": "",
    }
    doc.update(extra)
    return doc


def _patch_db(monkeypatch, coll: _FakeCollection):
    db = SimpleNamespace(transactions=coll)
    monkeypatch.setattr(cashier_service, "get_db", lambda: db)
    monkeypatch.setattr(accountant_service, "get_db", lambda: db)
    return db


def test_transaction_discarded_roundtrip():
    tx = Transaction(
        date=datetime(2026, 7, 1),
        description="X",
        truck_number="T1",
        amount=1.0,
        discarded=True,
        rejected=True,
    )
    doc = tx.to_doc()
    assert doc["discarded"] is True
    restored = Transaction.from_doc(doc)
    assert restored.discarded is True


def test_rejected_list_excludes_discarded(monkeypatch):
    cashier = ObjectId()
    other = ObjectId()
    coll = _FakeCollection([
        _tx_doc(cashier_id=cashier, discarded=False),
        _tx_doc(cashier_id=cashier, discarded=True),
        _tx_doc(cashier_id=other, discarded=False),
    ])
    _patch_db(monkeypatch, coll)

    async def scenario():
        rows = await cashier_service.get_rejected_transactions_for_cashier(cashier)
        assert len(rows) == 1
        assert rows[0].discarded is False

        discarded = await cashier_service.get_discarded_transactions_for_cashier(cashier)
        assert len(discarded) == 1
        assert discarded[0].discarded is True

    asyncio.run(scenario())


def test_discard_restore_delete_ownership(monkeypatch):
    cashier = ObjectId()
    other = ObjectId()
    own = _tx_doc(cashier_id=cashier, discarded=False)
    foreign = _tx_doc(cashier_id=other, discarded=False)
    coll = _FakeCollection([own, foreign])
    _patch_db(monkeypatch, coll)

    async def scenario():
        n = await cashier_service.discard_transactions(
            [own["_id"], foreign["_id"]], cashier
        )
        assert n == 1
        assert own["discarded"] is True
        assert foreign["discarded"] is False

        n = await cashier_service.restore_discarded_transactions([own["_id"]], cashier)
        assert n == 1
        assert own["discarded"] is False

        await cashier_service.discard_transactions([own["_id"]], cashier)
        n = await cashier_service.delete_discarded_transactions(
            [own["_id"], foreign["_id"]], cashier
        )
        assert n == 1
        assert len(coll.docs) == 1
        assert coll.docs[0]["_id"] == foreign["_id"]

    asyncio.run(scenario())


def test_resubmit_clears_rejection_flags(monkeypatch):
    cashier = ObjectId()
    doc = _tx_doc(cashier_id=cashier, discarded=False, reason="fix")
    coll = _FakeCollection([doc])
    _patch_db(monkeypatch, coll)

    async def scenario():
        n = await cashier_service.resubmit_rejected_transactions(
            cashier,
            {doc["_id"]: {"amount": 42000.0, "description": "FIXED"}},
        )
        assert n == 1
        assert doc["rejected"] is False
        assert doc["rejection_reason"] is None
        assert doc["discarded"] is False
        assert doc["amount"] == 42000.0
        assert doc["description"] == "FIXED"
        assert doc["last_edited_by"] == cashier

    asyncio.run(scenario())


def test_accountant_rejected_excludes_discarded(monkeypatch):
    cashier = ObjectId()
    coll = _FakeCollection([
        _tx_doc(cashier_id=cashier, discarded=False),
        _tx_doc(cashier_id=cashier, discarded=True),
    ])
    _patch_db(monkeypatch, coll)

    async def scenario():
        rows = await accountant_service.get_rejected_transactions()
        assert len(rows) == 1
        count = await accountant_service.get_rejected_count()
        assert count == 1
        trucks = await accountant_service.get_rejected_trucks()
        assert trucks == ["T100 EAF"]

    asyncio.run(scenario())
