"""Phase 2 — Receipts day/range entries + single display_status mapper."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.receipt_ops import (
    list_receipt_day_entries,
    list_receipt_entries,
    receipt_display_status_key,
    receipt_display_status_label,
    serialize_receipt_entry_row,
)


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$or":
            return any(_matches(doc, clause) for clause in expected)
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected or "$lte" in expected:
                if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                    return False
                if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                    return False
                continue
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

    def find(self, query: dict, projection: dict | None = None):
        matched = [dict(d) for d in self.documents if _matches(d, query)]
        if projection:
            keep = {k for k, v in projection.items() if v}
            keep.add("_id")
            matched = [{k: row[k] for k in keep if k in row} for row in matched]
        return _Cursor(matched)


def _db(
    *,
    transactions: list[dict] | None = None,
    requests: list[dict] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        transactions=FakeColl(transactions),
        receipt_requests=FakeColl(requests),
    )


def test_receipt_display_status_mapping() -> None:
    assert receipt_display_status_key({"receipt_status": "no_receipt"}) == "no_receipt"
    assert (
        receipt_display_status_key(
            {"receipt_status": "missing"}, {"status": "waived"}
        )
        == "waived"
    )
    assert (
        receipt_display_status_key(
            {
                "receipt_status": "partial",
                "attachments": [
                    {"id": "a1", "file_id": "a1", "filename": "p.jpg", "partial": True}
                ],
            },
            {"status": "needed", "partial_receipt": True, "partial_note": "page 1"},
        )
        == "partial"
    )
    assert (
        receipt_display_status_key(
            {"receipt_status": "received"}, {"status": "received"}
        )
        == "received"
    )
    assert (
        receipt_display_status_key(
            {"receipt_status": "pending"}, {"status": "needed"}
        )
        == "needed"
    )
    assert receipt_display_status_key({"receipt_status": "receipt"}) == "needed"
    assert receipt_display_status_key({"receipt_status": "pending"}) == "pending"
    assert receipt_display_status_label("no_receipt") == "No receipt"
    assert receipt_display_status_label("needed") == "Needed"


def test_serialize_entry_includes_attachment_refs() -> None:
    tx_id = ObjectId()
    file_id = "66aa00000000000000000001"
    row = serialize_receipt_entry_row(
        {
            "_id": tx_id,
            "date": datetime(2026, 5, 10, 9, 0, 0),
            "amount": 1500,
            "currency": "TZS",
            "item": "Diesel",
            "payee": "SHELL",
            "ownership": "Ali",
            "truck_number": "T 123 ABC",
            "description": "fuel top-up",
            "receipt_status": "partial",
            "receipt_partial_note": "half",
            "attachments": [
                {
                    "id": file_id,
                    "file_id": file_id,
                    "filename": "half.jpg",
                    "partial": True,
                    "partial_note": "half",
                    "size": 12,
                }
            ],
        },
        {
            "_id": ObjectId(),
            "status": "needed",
            "partial_receipt": True,
            "partial_note": "half",
        },
    )
    assert row["display_status"] == "Partial"
    assert row["display_status_key"] == "partial"
    assert row["partial"] is True
    assert row["partial_note"] == "half"
    assert row["primary_file_id"] == file_id
    assert row["attachment_count"] == 1
    assert row["attachments"][0]["filename"] == "half.jpg"
    assert row["date"] == "2026-05-10"
    assert row["truck_number"] == "T 123 ABC"
    assert row["description"] == "fuel top-up"


@pytest.mark.asyncio
async def test_list_receipt_day_entries_for_one_day() -> None:
    day = datetime(2026, 5, 10, 8, 0, 0)
    other = datetime(2026, 5, 11, 8, 0, 0)
    tx_a = ObjectId()
    tx_b = ObjectId()
    tx_c = ObjectId()
    db = _db(
        transactions=[
            {
                "_id": tx_a,
                "date": day,
                "amount": 100,
                "receipt_status": "receipt",
                "item": "A",
            },
            {
                "_id": tx_b,
                "date": day,
                "amount": 200,
                "receipt_status": "no_receipt",
                "item": "B",
            },
            {
                "_id": tx_c,
                "date": other,
                "amount": 300,
                "receipt_status": "receipt",
                "item": "C",
            },
        ],
        requests=[
            {"_id": ObjectId(), "transaction_id": tx_a, "status": "needed"},
        ],
    )
    rows = await list_receipt_day_entries(db, day=date(2026, 5, 10))
    assert len(rows) == 2
    by_id = {row["id"]: row for row in rows}
    assert by_id[str(tx_a)]["display_status_key"] == "needed"
    assert by_id[str(tx_b)]["display_status_key"] == "no_receipt"


@pytest.mark.asyncio
async def test_list_receipt_entries_range_and_status_filter() -> None:
    d1 = datetime(2026, 6, 1, 10, 0, 0)
    d2 = datetime(2026, 6, 15, 10, 0, 0)
    tx_needed = ObjectId()
    tx_waived = ObjectId()
    tx_received = ObjectId()
    file_id = "66bb00000000000000000002"
    db = _db(
        transactions=[
            {
                "_id": tx_needed,
                "date": d1,
                "receipt_status": "pending",
                "amount": 1,
            },
            {
                "_id": tx_waived,
                "date": d1,
                "receipt_status": "missing",
                "amount": 2,
            },
            {
                "_id": tx_received,
                "date": d2,
                "receipt_status": "received",
                "amount": 3,
                "attachments": [
                    {"id": file_id, "file_id": file_id, "filename": "ok.pdf"}
                ],
            },
        ],
        requests=[
            {"_id": ObjectId(), "transaction_id": tx_needed, "status": "needed"},
            {
                "_id": ObjectId(),
                "transaction_id": tx_waived,
                "status": "waived",
            },
            {
                "_id": ObjectId(),
                "transaction_id": tx_received,
                "status": "received",
            },
        ],
    )

    all_rows = await list_receipt_entries(
        db,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
    )
    assert len(all_rows) == 3

    needed_only = await list_receipt_entries(
        db,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        status="needed",
    )
    assert len(needed_only) == 1
    assert needed_only[0]["id"] == str(tx_needed)

    received_only = await list_receipt_entries(
        db,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        status="received",
    )
    assert len(received_only) == 1
    assert received_only[0]["primary_file_id"] == file_id
    assert received_only[0]["display_status"] == "Received"

    waived_only = await list_receipt_entries(db, status="waived")
    assert len(waived_only) == 1
    assert waived_only[0]["display_status_key"] == "waived"
