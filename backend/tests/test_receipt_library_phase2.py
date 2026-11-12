"""Phase 2 — submitted receipts library inquiry."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.receipt_ops import (
    INBOX_APPROVED,
    INBOX_MATCHED,
    list_receipt_library,
    store_receipt_file_bytes,
)


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$gte" in expected or "$lte" in expected:
                if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                    return False
                if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                    return False
                continue
            if "$in" in expected and actual not in expected["$in"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def sort(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return self

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class FakeColl:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc: dict) -> None:
        self.documents.append(dict(doc))

    def find(self, query: dict, projection: dict | None = None):
        return _Cursor([dict(d) for d in self.documents if _matches(d, query)])


def _db(txs: list[dict] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        receipt_files=FakeColl(),
        transactions=FakeColl(txs or []),
    )


@pytest.mark.asyncio
async def test_library_filters_by_payee_and_status(tmp_path: Path) -> None:
    tx_id = ObjectId()
    db = _db(
        [
            {
                "_id": tx_id,
                "date": datetime.utcnow(),
                "amount": 1000,
                "currency": "TZS",
                "item": "Diesel",
                "payee": "SHELL DAR",
                "ownership": "Ali",
                "receipt_status": "received",
            }
        ]
    )
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="fuel.jpg",
        content=b"\xff\xd8\xff\xe0jpeg-bytes",
        source="desktop",
        transaction_id=tx_id,
    )
    doc = await db.receipt_files.find_one({"_id": meta["id"]})
    doc["inbox_status"] = INBOX_MATCHED
    # FakeColl stores by reference from insert — update in place
    for d in db.receipt_files.documents:
        if d["_id"] == meta["id"]:
            d["inbox_status"] = INBOX_MATCHED
            d["transaction_ids"] = [tx_id]

    rows = await list_receipt_library(
        db,
        date_from=datetime.utcnow().date() - timedelta(days=1),
        date_to=datetime.utcnow().date(),
        payee="SHELL",
        status="matched",
    )
    assert len(rows) == 1
    assert rows[0]["id"] == meta["id"]
    assert "Diesel" in rows[0]["item"]
    assert "SHELL" in rows[0]["payee"].upper()

    empty = await list_receipt_library(
        db,
        payee="NOPE",
        status="matched",
    )
    assert empty == []


@pytest.mark.asyncio
async def test_library_approved_only(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="ok.pdf",
        content=b"%PDF-1.4",
        source="mobile",
    )
    for d in db.receipt_files.documents:
        if d["_id"] == meta["id"]:
            d["inbox_status"] = INBOX_APPROVED

    rows = await list_receipt_library(db, status="approved")
    assert [r["id"] for r in rows] == [meta["id"]]
    parked = await list_receipt_library(db, status="parked")
    assert parked == []
