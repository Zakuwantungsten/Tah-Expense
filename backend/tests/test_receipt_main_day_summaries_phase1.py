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
            if "$type" in expected:
                wanted = expected["$type"]
                if wanted == "date" and not isinstance(actual, (date, datetime)):
                    return False
                continue
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
                # Apply multi-key sort stably from last key to first.
                for field, direction in reversed(list(stage["$sort"].items())):
                    docs.sort(
                        key=lambda d, f=field: (d.get(f) is None, d.get(f)),
                        reverse=direction < 0,
                    )
            elif "$limit" in stage:
                docs = docs[: int(stage["$limit"])]
            elif "$project" in stage:
                projected: list[dict] = []
                for doc in docs:
                    row: dict = {}
                    include_mode = any(
                        v in (1, True) for v in stage["$project"].values()
                    )
                    if include_mode:
                        row["_id"] = doc.get("_id")
                        for key, flag in stage["$project"].items():
                            if key == "_id" and flag in (0, False):
                                row.pop("_id", None)
                                continue
                            if flag in (1, True) and key in doc:
                                row[key] = doc[key]
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


@pytest.mark.asyncio
async def test_list_receipt_day_summaries_prefers_recent_within_scan_cap() -> None:
    """Large DBs used to miss newest days when the scan was unordered + capped."""
    older = datetime(2026, 1, 1, 10, 0, 0)
    newer = datetime(2026, 11, 20, 10, 0, 0)
    txs = [
        {"_id": ObjectId(), "date": older, "receipt_status": "received"}
        for _ in range(30)
    ]
    txs.append(
        {"_id": ObjectId(), "date": newer, "receipt_status": "no_receipt"}
    )
    db = _db(transactions=txs, requests=[])
    # row_cap = max(limit, scan_limit) → 10; without newest-first sort the
    # Nov row (appended last) would fall outside an insertion-order cap.
    page = await list_receipt_day_summaries(db, limit=5, scan_limit=10)
    dates = [row["date"] for row in page["items"]]
    assert "2026-11-20" in dates
    assert dates[0] == "2026-11-20"
