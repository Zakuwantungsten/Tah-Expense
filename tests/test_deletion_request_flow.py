"""Cashier delete → accountant confirm/restore for approved expenses."""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import List, Optional

from bson import ObjectId

from tahmeed.services import accountant_service, cashier_service


class _FakeCursor:
    def __init__(self, docs: List[dict]):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n=None):
        self._limit = n
        return self

    def skip(self, n=0):
        self._skip = n
        return self

    async def to_list(self, length=None):
        docs = list(self._docs)
        skip = getattr(self, "_skip", 0)
        docs = docs[skip:]
        if length is not None:
            docs = docs[:length]
        elif getattr(self, "_limit", None) is not None:
            docs = docs[: self._limit]
        return docs


class _FakeCollection:
    def __init__(self, docs: Optional[List[dict]] = None):
        self.docs: List[dict] = list(docs or [])

    def find(self, query: dict, *_a, **_k):
        return _FakeCursor([d for d in self.docs if _match(d, query)])

    async def find_one(self, query: dict):
        for d in self.docs:
            if _match(d, query):
                return d
        return None

    async def insert_one(self, doc: dict):
        doc = dict(doc)
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def update_one(self, query: dict, update: dict, **_kwargs):
        modified = 0
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set", {}))
                modified = 1
                break
        return SimpleNamespace(modified_count=modified)

    async def update_many(self, query: dict, update: dict, **_kwargs):
        modified = 0
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set", {}))
                modified += 1
        return SimpleNamespace(modified_count=modified)

    async def delete_one(self, query: dict, **_kwargs):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def delete_many(self, query: dict, **_kwargs):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def count_documents(self, query: dict):
        return sum(1 for d in self.docs if _match(d, query))


def _match_clause(doc: dict, clause: dict) -> bool:
    if "$or" in clause:
        return any(_match(doc, sub) for sub in clause["$or"])
    if "$and" in clause:
        return all(_match(doc, sub) for sub in clause["$and"])
    return _match(doc, clause)


def _match(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_match_clause(doc, sub) for sub in expected):
                return False
            continue
        if key == "$or":
            if not any(_match_clause(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected and not (actual is not None and actual >= expected["$gte"]):
                return False
            if "$lte" in expected and not (actual is not None and actual <= expected["$lte"]):
                return False
            if "$regex" in expected:
                import re
                flags = re.I if "i" in (expected.get("$options") or "") else 0
                if not re.search(expected["$regex"], str(actual or ""), flags):
                    return False
            continue
        if actual != expected:
            return False
    return True


def _patch_db(monkeypatch, coll: _FakeCollection):
    db = SimpleNamespace(transactions=coll)
    monkeypatch.setattr(cashier_service, "get_db", lambda: db)
    monkeypatch.setattr(accountant_service, "get_db", lambda: db)
    return db


def _base_doc(**extra):
    doc = {
        "_id": ObjectId(),
        "date": datetime(2026, 1, 15),
        "description": "TOLL FEE",
        "truck_number": "T123 ABC",
        "amount": 25000.0,
        "currency": "TZS",
        "cashier_id": ObjectId(),
        "verified": False,
        "rejected": False,
        "discarded": False,
        "deletion_requested": False,
        "edited_after_verification": False,
        "item": "Toll",
        "category_name": "Toll",
        "created_at": datetime(2026, 1, 15, 10, 0, 0),
        "receipt_status": "pending",
        "register_status": "submitted",
    }
    doc.update(extra)
    return doc


def test_unverified_delete_hard_removes(monkeypatch):
    cashier = ObjectId()
    doc = _base_doc(cashier_id=cashier, verified=False, register_status="submitted")
    coll = _FakeCollection([doc])
    _patch_db(monkeypatch, coll)

    async def scenario():
        result = await cashier_service.request_or_delete_transaction(doc["_id"], cashier)
        assert result == "deleted"
        assert coll.docs == []
        inbox = await accountant_service.get_unverified_filtered(edited=False)
        assert inbox == []

    asyncio.run(scenario())


def test_verified_delete_requests_and_hides_from_master(monkeypatch):
    cashier = ObjectId()
    doc = _base_doc(cashier_id=cashier, verified=True, year=2026)
    coll = _FakeCollection([doc])
    _patch_db(monkeypatch, coll)

    async def scenario():
        result = await cashier_service.request_or_delete_transaction(doc["_id"], cashier)
        assert result == "deletion_requested"
        assert len(coll.docs) == 1
        assert coll.docs[0]["deletion_requested"] is True
        assert coll.docs[0]["deletion_requested_by"] == cashier

        master = await accountant_service.get_master_transactions(year=2026, month=1)
        assert master == []

        pending = await accountant_service.get_deletion_requested_filtered()
        assert len(pending) == 1
        assert pending[0]._id == doc["_id"]

        register = await cashier_service.get_transactions_by_date(
            doc["date"].date(), cashier_id=cashier,
        )
        assert register == []

    asyncio.run(scenario())


def test_confirm_deletion_removes_pending_edit_clones(monkeypatch):
    cashier = ObjectId()
    original = _base_doc(
        cashier_id=cashier,
        verified=True,
        deletion_requested=True,
        deletion_requested_at=datetime.utcnow(),
        deletion_requested_by=cashier,
        year=2026,
    )
    clone = _base_doc(
        cashier_id=cashier,
        verified=False,
        edited_after_verification=True,
        original_transaction_id=original["_id"],
        description="PENDING EDIT",
    )
    coll = _FakeCollection([original, clone])
    _patch_db(monkeypatch, coll)

    async def scenario():
        ok = await accountant_service.confirm_deletion(original["_id"])
        assert ok is True
        assert coll.docs == []

    asyncio.run(scenario())


def test_restore_deletion_returns_to_master(monkeypatch):
    cashier = ObjectId()
    doc = _base_doc(
        cashier_id=cashier,
        verified=True,
        deletion_requested=True,
        deletion_requested_at=datetime.utcnow(),
        deletion_requested_by=cashier,
        year=2026,
    )
    coll = _FakeCollection([doc])
    _patch_db(monkeypatch, coll)

    async def scenario():
        ok = await accountant_service.restore_deletion(doc["_id"])
        assert ok is True
        assert coll.docs[0]["deletion_requested"] is False
        assert coll.docs[0]["deletion_requested_at"] is None
        assert coll.docs[0]["deletion_requested_by"] is None

        master = await accountant_service.get_master_transactions(year=2026, month=1)
        assert len(master) == 1
        assert master[0]._id == doc["_id"]

        pending = await accountant_service.get_deletion_requested_filtered()
        assert pending == []

    asyncio.run(scenario())


def test_pending_edit_clone_hard_deletes(monkeypatch):
    cashier = ObjectId()
    original = _base_doc(cashier_id=cashier, verified=True, description="ORIG")
    pending = _base_doc(
        cashier_id=cashier,
        verified=False,
        description="PENDING",
        edited_after_verification=True,
        original_transaction_id=original["_id"],
    )
    coll = _FakeCollection([original, pending])
    _patch_db(monkeypatch, coll)

    async def scenario():
        result = await cashier_service.request_or_delete_transaction(
            pending["_id"], cashier,
        )
        assert result == "deleted"
        assert len(coll.docs) == 1
        assert coll.docs[0]["_id"] == original["_id"]
        assert coll.docs[0]["verified"] is True

    asyncio.run(scenario())
