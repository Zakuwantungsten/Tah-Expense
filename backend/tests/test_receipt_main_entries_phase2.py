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
    if "$and" in query:
        return all(_matches(doc, clause) for clause in query["$and"])
    if "$or" in query:
        if not any(_matches(doc, clause) for clause in query["$or"]):
            return False
        rest = {k: v for k, v in query.items() if k != "$or"}
        return _matches(doc, rest) if rest else True
    for key, expected in query.items():
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


def _resolve_expr(doc: dict, expr: object) -> object:
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr[1:])
    if isinstance(expr, dict) and "$ifNull" in expr:
        left, right = expr["$ifNull"]
        value = _resolve_expr(doc, left)
        return value if value is not None else _resolve_expr(doc, right)
    return expr


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

    def aggregate(self, pipeline: list[dict]):
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [doc for doc in docs if _matches(doc, stage["$match"])]
            elif "$addFields" in stage:
                for doc in docs:
                    for key, expr in stage["$addFields"].items():
                        doc[key] = _resolve_expr(doc, expr)
            elif "$sort" in stage:
                keys = list(stage["$sort"].items())
                for field, direction in reversed(keys):
                    docs.sort(
                        key=lambda d, f=field: (d.get(f) is None, d.get(f)),
                        reverse=direction < 0,
                    )
            elif "$limit" in stage:
                docs = docs[: int(stage["$limit"])]
            elif "$project" in stage:
                projected: list[dict] = []
                for doc in docs:
                    include_mode = any(
                        v in (1, True) for v in stage["$project"].values()
                    )
                    if include_mode:
                        row: dict = {"_id": doc.get("_id")}
                        for key, flag in stage["$project"].items():
                            if key == "_id" and flag in (0, False):
                                row.pop("_id", None)
                                continue
                            if flag in (1, True) and key in doc:
                                row[key] = doc[key]
                        projected.append(row)
                    else:
                        row = dict(doc)
                        for key, flag in stage["$project"].items():
                            if flag in (0, False):
                                row.pop(key, None)
                        projected.append(row)
                docs = projected
        return _Cursor(docs)


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


@pytest.mark.asyncio
async def test_list_receipt_day_entries_finds_day_beyond_unrelated_prefix() -> None:
    """Day drill must query by register day — not filter the first N txs in Python."""
    day = datetime(2026, 5, 10, 8, 0, 0)
    other = datetime(2026, 1, 1, 8, 0, 0)
    target = ObjectId()
    txs = [
        {
            "_id": ObjectId(),
            "date": other,
            "receipt_status": "received",
            "amount": 1,
        }
        for _ in range(80)
    ]
    txs.append(
        {
            "_id": target,
            "date": day,
            "receipt_status": "no_receipt",
            "amount": 2,
        }
    )
    db = _db(transactions=txs, requests=[])
    rows = await list_receipt_day_entries(db, day=date(2026, 5, 10), limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == str(target)
    assert rows[0]["display_status_key"] == "no_receipt"
