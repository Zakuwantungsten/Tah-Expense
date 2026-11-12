"""Phase 1 — Receipts Main day-summaries (Needed / No receipt per register day)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.receipt_ops import (
    classify_receipt_day_bucket,
    list_receipt_day_summaries,
    tx_register_calendar_day,
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


def test_tx_register_calendar_day_prefers_import_primary() -> None:
    primary = datetime(2026, 5, 10, 8, 0, 0)
    excel = datetime(2026, 5, 1, 8, 0, 0)
    assert tx_register_calendar_day(
        {"import_primary_date": primary, "date": excel}
    ) == date(2026, 5, 10)
    assert tx_register_calendar_day({"date": excel}) == date(2026, 5, 1)
    assert tx_register_calendar_day({}) is None


def test_classify_needed_no_receipt_and_other() -> None:
    needed_id = ObjectId()
    other_id = ObjectId()
    needed_ids = {needed_id}

    assert (
        classify_receipt_day_bucket(
            {"_id": other_id, "receipt_status": "no_receipt"},
            needed_tx_ids=needed_ids,
        )
        == "no_receipt"
    )
    assert (
        classify_receipt_day_bucket(
            {"_id": needed_id, "receipt_status": "partial"},
            needed_tx_ids=needed_ids,
        )
        == "needed"
    )
    assert (
        classify_receipt_day_bucket(
            {"_id": other_id, "receipt_status": "receipt"},
            needed_tx_ids=set(),
        )
        == "needed"
    )
    assert (
        classify_receipt_day_bucket(
            {"_id": other_id, "receipt_status": "received"},
            needed_tx_ids=set(),
        )
        == "other"
    )
    assert (
        classify_receipt_day_bucket(
            {"_id": other_id, "receipt_status": "pending"},
            needed_tx_ids=set(),
        )
        == "other"
    )
    assert (
        classify_receipt_day_bucket(
            {"_id": other_id, "receipt_status": "missing"},
            needed_tx_ids=set(),
        )
        == "other"
    )


@pytest.mark.asyncio
async def test_list_receipt_day_summaries_groups_and_counts() -> None:
    day_a = datetime(2026, 5, 10, 9, 0, 0)
    day_b = datetime(2026, 5, 11, 9, 0, 0)
    cashier = ObjectId()
    tx_needed_req = ObjectId()
    tx_receipt_label = ObjectId()
    tx_no_receipt = ObjectId()
    tx_received = ObjectId()
    tx_other_day = ObjectId()

    db = _db(
        transactions=[
            {
                "_id": tx_needed_req,
                "date": day_a,
                "receipt_status": "pending",
                "cashier_id": cashier,
            },
            {
                "_id": tx_receipt_label,
                "date": day_a,
                "receipt_status": "receipt",
                "cashier_id": cashier,
            },
            {
                "_id": tx_no_receipt,
                "date": day_a,
                "receipt_status": "no_receipt",
                "cashier_id": cashier,
            },
            {
                "_id": tx_received,
                "date": day_a,
                "receipt_status": "received",
                "cashier_id": cashier,
            },
            {
                "_id": tx_other_day,
                "import_primary_date": day_b,
                "date": day_a,
                "receipt_status": "no_receipt",
                "cashier_id": cashier,
            },
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_needed_req,
                "status": "needed",
            },
            {
                "_id": ObjectId(),
                "transaction_id": tx_received,
                "status": "received",
            },
        ],
    )

    page = await list_receipt_day_summaries(db)
    items = page["items"]
    assert page["total"] == 2
    assert page["has_more"] is False
    assert [row["date"] for row in items] == ["2026-05-11", "2026-05-10"]

    may11 = items[0]
    assert may11["entries_count"] == 1
    assert may11["needed_count"] == 0
    assert may11["no_receipt_count"] == 1

    may10 = items[1]
    assert may10["entries_count"] == 4
    assert may10["needed_count"] == 2  # open request + explicit receipt
    assert may10["no_receipt_count"] == 1
    assert may10["collected_count"] == 1
    assert may10["waived_count"] == 0
    assert may10["partial_count"] == 0
    assert may10["multi_file_count"] == 0
    assert may10["shared_file_count"] == 0
    assert cashier in may10["cashier_ids"]


@pytest.mark.asyncio
async def test_list_receipt_day_summaries_partial_collected_multi_shared() -> None:
    day = datetime(2026, 8, 1, 10, 0, 0)
    tx_partial = ObjectId()
    tx_shared_a = ObjectId()
    tx_shared_b = ObjectId()
    tx_multi = ObjectId()
    tx_waived = ObjectId()
    shared_file = "shared-file-1"
    db = _db(
        transactions=[
            {
                "_id": tx_partial,
                "date": day,
                "receipt_status": "partial",
                "attachments": [
                    {
                        "id": "p1",
                        "file_id": "p1",
                        "filename": "half.jpg",
                        "partial": True,
                    }
                ],
            },
            {
                "_id": tx_shared_a,
                "date": day,
                "receipt_status": "received",
                "attachments": [
                    {"id": shared_file, "file_id": shared_file, "filename": "one.pdf"}
                ],
            },
            {
                "_id": tx_shared_b,
                "date": day,
                "receipt_status": "received",
                "attachments": [
                    {"id": shared_file, "file_id": shared_file, "filename": "one.pdf"}
                ],
            },
            {
                "_id": tx_multi,
                "date": day,
                "receipt_status": "received",
                "attachments": [
                    {"id": "m1", "file_id": "m1", "filename": "a.jpg"},
                    {"id": "m2", "file_id": "m2", "filename": "b.jpg"},
                ],
            },
            {
                "_id": tx_waived,
                "date": day,
                "receipt_status": "missing",
            },
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_partial,
                "status": "needed",
                "partial_receipt": True,
            },
            {
                "_id": ObjectId(),
                "transaction_id": tx_shared_a,
                "status": "received",
            },
            {
                "_id": ObjectId(),
                "transaction_id": tx_shared_b,
                "status": "received",
            },
            {
                "_id": ObjectId(),
                "transaction_id": tx_multi,
                "status": "received",
            },
            {
                "_id": ObjectId(),
                "transaction_id": tx_waived,
                "status": "waived",
            },
        ],
    )
    page = await list_receipt_day_summaries(db)
    items = page["items"]
    assert len(items) == 1
    row = items[0]
    assert row["entries_count"] == 5
    assert row["partial_count"] == 1
    assert row["needed_count"] == 1  # partial also counts as needed
    assert row["collected_count"] == 3  # two shared + one multi
    assert row["waived_count"] == 1
    assert row["multi_file_count"] == 1
    assert row["shared_file_count"] == 2


@pytest.mark.asyncio
async def test_list_receipt_day_summaries_offset_pagination() -> None:
    day_a = datetime(2026, 9, 1, 10, 0, 0)
    day_b = datetime(2026, 9, 2, 10, 0, 0)
    day_c = datetime(2026, 9, 3, 10, 0, 0)
    db = _db(
        transactions=[
            {"_id": ObjectId(), "date": day_c, "receipt_status": "receipt"},
            {"_id": ObjectId(), "date": day_b, "receipt_status": "no_receipt"},
            {"_id": ObjectId(), "date": day_a, "receipt_status": "received"},
        ],
        requests=[],
    )
    first = await list_receipt_day_summaries(db, limit=2, offset=0)
    assert first["total"] == 3
    assert first["has_more"] is True
    assert [r["date"] for r in first["items"]] == ["2026-09-03", "2026-09-02"]
    second = await list_receipt_day_summaries(db, limit=2, offset=2)
    assert second["has_more"] is False
    assert [r["date"] for r in second["items"]] == ["2026-09-01"]


@pytest.mark.asyncio
async def test_list_receipt_day_summaries_date_and_cashier_filters() -> None:
    day = datetime(2026, 6, 1, 12, 0, 0)
    older = day - timedelta(days=5)
    c1 = ObjectId()
    c2 = ObjectId()
    db = _db(
        transactions=[
            {
                "_id": ObjectId(),
                "date": day,
                "receipt_status": "receipt",
                "cashier_id": c1,
            },
            {
                "_id": ObjectId(),
                "date": day,
                "receipt_status": "no_receipt",
                "cashier_id": c2,
            },
            {
                "_id": ObjectId(),
                "date": older,
                "receipt_status": "receipt",
                "cashier_id": c1,
            },
        ],
        requests=[],
    )

    scoped = await list_receipt_day_summaries(
        db,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 1),
        cashier_id=c1,
    )
    assert len(scoped["items"]) == 1
    assert scoped["items"][0]["date"] == "2026-06-01"
    assert scoped["items"][0]["entries_count"] == 1
    assert scoped["items"][0]["needed_count"] == 1
    assert scoped["items"][0]["no_receipt_count"] == 0


@pytest.mark.asyncio
async def test_list_receipt_day_summaries_skips_trashed() -> None:
    day = datetime(2026, 7, 1, 10, 0, 0)
    db = _db(
        transactions=[
            {
                "_id": ObjectId(),
                "date": day,
                "receipt_status": "receipt",
                "trashed": True,
            },
            {
                "_id": ObjectId(),
                "date": day,
                "receipt_status": "no_receipt",
            },
        ],
        requests=[],
    )
    page = await list_receipt_day_summaries(db)
    items = page["items"]
    assert len(items) == 1
    assert items[0]["entries_count"] == 1
    assert items[0]["no_receipt_count"] == 1
    assert items[0]["needed_count"] == 0
