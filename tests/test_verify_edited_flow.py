"""Option B verify/edited flow + inbox filter helpers."""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import List, Optional

from bson import ObjectId

from tahmeed.services import accountant_service, cashier_service
from tahmeed.services.daily_import_service import parse_amount
from tahmeed.ui.accountant.verify_inbox import _fmt_amount, _fmt_num
from tahmeed.models.transaction import Transaction


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

    def find(self, query: dict):
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

    async def update_one(self, query: dict, update: dict):
        modified = 0
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set", {}))
                modified = 1
                break
        return SimpleNamespace(modified_count=modified)

    async def delete_one(self, query: dict):
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
        "edited_after_verification": False,
        "item": "Toll",
        "category_name": "Toll",
        "created_at": datetime(2026, 1, 15, 10, 0, 0),
        "receipt_status": "pending",
    }
    doc.update(extra)
    return doc


def test_parse_amount_normalizes_tzs_strings():
    assert parse_amount("TZS 1,250") == 1250.0
    assert parse_amount("(2,000)") == -2000.0
    assert _fmt_num("TZS 1,250", "TZS ", 0) == "TZS 1,250"
    tx = Transaction(
        date=datetime(2026, 1, 1),
        description="x",
        truck_number="T1",
        amount=1250.0,
        currency="TZS",
    )
    assert _fmt_amount(tx) == "TZS 1,250"


def test_insert_pending_edit_and_reapprove(monkeypatch):
    cashier = ObjectId()
    accountant = ObjectId()
    original = _base_doc(verified=True, cashier_id=cashier, amount=1000.0)
    coll = _FakeCollection([original])
    _patch_db(monkeypatch, coll)

    async def scenario():
        pending_id = await cashier_service.insert_pending_edit(
            original["_id"],
            {"amount": 1500.0, "description": "TOLL FEE UPDATED"},
            cashier,
        )
        assert pending_id != original["_id"]
        assert len(coll.docs) == 2
        pending = next(d for d in coll.docs if d["_id"] == pending_id)
        assert pending["edited_after_verification"] is True
        assert pending["verified"] is False
        assert pending["original_transaction_id"] == original["_id"]

        # Second edit refreshes the same pending doc
        pending_id2 = await cashier_service.insert_pending_edit(
            original["_id"],
            {"amount": 1750.0},
            cashier,
        )
        assert pending_id2 == pending_id
        assert len(coll.docs) == 2

        edited = await accountant_service.get_edited_transactions()
        assert len(edited) == 1
        assert edited[0].amount == 1750.0

        ok = await accountant_service.re_approve_transaction(pending_id, accountant)
        assert ok is True
        assert len(coll.docs) == 1
        master = coll.docs[0]
        assert master["_id"] == original["_id"]
        assert master["verified"] is True
        assert master["amount"] == 1750.0
        assert master["edited_after_verification"] is False

    asyncio.run(scenario())


def test_unverified_edit_lands_in_edited_query(monkeypatch):
    """Option B: in-place flag moves New → Edited without cloning."""
    cashier = ObjectId()
    doc = _base_doc(verified=False, cashier_id=cashier, amount=500.0)
    coll = _FakeCollection([doc])
    _patch_db(monkeypatch, coll)

    async def scenario():
        await cashier_service.update_transaction(doc["_id"], {
            "amount": 750.0,
            "edited_after_verification": True,
            "last_edited_at": datetime.utcnow(),
            "last_edited_by": cashier,
        })
        new_rows = await accountant_service.get_unverified_filtered(edited=False)
        edited_rows = await accountant_service.get_edited_transactions()
        assert new_rows == []
        assert len(edited_rows) == 1
        assert edited_rows[0].amount == 750.0

        ok = await accountant_service.re_approve_transaction(doc["_id"], ObjectId())
        assert ok is True
        assert coll.docs[0]["verified"] is True
        assert coll.docs[0]["edited_after_verification"] is False

    asyncio.run(scenario())


def test_inbox_item_and_description_filters(monkeypatch):
    coll = _FakeCollection([
        _base_doc(description="DIESEL STOP", item="Diesel Cash", category_name="Diesel Cash"),
        _base_doc(description="TOLL GATE", item="Toll", category_name="Toll"),
        _base_doc(
            description="PARKING",
            item="Parking",
            category_name="Parking",
            edited_after_verification=True,
        ),
    ])
    _patch_db(monkeypatch, coll)

    async def scenario():
        diesel = await accountant_service.get_unverified_filtered(
            edited=False, item="Diesel Cash",
        )
        assert len(diesel) == 1
        assert diesel[0].description == "DIESEL STOP"

        toll_desc = await accountant_service.get_unverified_filtered(
            edited=False, description="TOLL",
        )
        assert len(toll_desc) == 1

        search_truckish = await accountant_service.get_edited_transactions(search="PARK")
        assert len(search_truckish) == 1

    asyncio.run(scenario())


def test_get_transactions_by_date_hides_original_when_pending(monkeypatch):
    cashier = ObjectId()
    original = _base_doc(verified=True, cashier_id=cashier, description="ORIG")
    pending = _base_doc(
        verified=False,
        cashier_id=cashier,
        description="PENDING",
        edited_after_verification=True,
        original_transaction_id=original["_id"],
    )
    coll = _FakeCollection([original, pending])
    _patch_db(monkeypatch, coll)

    async def scenario():
        rows = await cashier_service.get_transactions_by_date(
            original["date"].date(), cashier_id=cashier,
        )
        assert len(rows) == 1
        assert rows[0].description == "PENDING"

    asyncio.run(scenario())
