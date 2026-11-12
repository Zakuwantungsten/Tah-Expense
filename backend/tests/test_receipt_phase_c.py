"""Phase C — N↔M link, partial coverage, early-then-link."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.receipt_ops import (
    INBOX_MATCHED,
    INBOX_UNMATCHED,
    chase_groups,
    list_inbox_files,
    manual_link_files,
    search_link_transactions,
    store_receipt_file_bytes,
)


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected or "$lte" in expected:
                if actual is None:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
                    return False
            continue
        if actual != expected:
            return False
    return True


class FakeColl:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                result = dict(doc)
                if projection:
                    keep = {k for k, v in projection.items() if v}
                    keep.add("_id")
                    result = {k: result[k] for k in keep if k in result}
                return result
        return None

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=document.get("_id"))

    async def update_one(
        self, query: dict, update: dict, *, upsert: bool = False
    ) -> SimpleNamespace:
        for doc in self.documents:
            if not _matches(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            if "$push" in update:
                for key, value in update["$push"].items():
                    doc.setdefault(key, [])
                    doc[key] = list(doc[key]) + [value]
            if "$addToSet" in update:
                for key, value in update["$addToSet"].items():
                    existing = list(doc.get(key) or [])
                    if isinstance(value, dict) and "$each" in value:
                        for item in value["$each"]:
                            if item not in existing:
                                existing.append(item)
                    elif value not in existing:
                        existing.append(value)
                    doc[key] = existing
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def find(self, query: dict, projection: dict | None = None):
        matched = [dict(doc) for doc in self.documents if _matches(doc, query)]
        if projection:
            keep = {k for k, v in projection.items() if v}
            keep.add("_id")
            matched = [{k: d[k] for k in keep if k in d} for d in matched]

        class _Cursor:
            def sort(self, *_a, **_k):
                return self

            def limit(self, n: int):
                nonlocal matched
                matched = matched[:n]
                return self

            async def to_list(self, length: int | None = None):
                if length is None:
                    return list(matched)
                return list(matched)[:length]

        return _Cursor()


class FakeEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=ObjectId())


def _db(*, files=None, transactions=None, requests=None):
    return SimpleNamespace(
        receipt_files=FakeColl(files),
        transactions=FakeColl(transactions),
        receipt_requests=FakeColl(requests),
        operation_events=FakeEvents(),
        system_settings=FakeColl(),
        users=FakeColl(),
        auth_sessions=FakeColl(),
        people=FakeColl(),
        categories=FakeColl(),
    )


async def test_link_one_to_one(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db, tmp_path, filename="a.jpg", content=b"\xff\xd8\xff\xe0img", source="desktop"
    )
    tx_id = ObjectId()
    db.transactions.documents.append(
        {
            "_id": tx_id,
            "attachments": [],
            "receipt_status": "pending",
            "ownership": "ALI",
        }
    )
    db.receipt_requests.documents.append(
        {
            "_id": ObjectId(),
            "transaction_id": tx_id,
            "status": "needed",
            "asked_of_name": "ALI",
            "opened_at": datetime(2026, 1, 1),
            "reminder_count": 0,
        }
    )
    result = await manual_link_files(
        db, file_ids=[meta["id"]], transaction_ids=[tx_id], actor_id=None
    )
    assert result["linked"] == 1
    assert db.receipt_files.documents[0]["inbox_status"] == INBOX_MATCHED
    assert len(db.transactions.documents[0]["attachments"]) == 1
    assert db.receipt_requests.documents[0]["status"] == "received"
    groups = await chase_groups(db)
    assert groups == []


async def test_link_one_file_to_many_rows(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db, tmp_path, filename="shared.pdf", content=b"%PDF", source="expo_early"
    )
    tx_a, tx_b = ObjectId(), ObjectId()
    for tid, name in ((tx_a, "ALI"), (tx_b, "BETA")):
        db.transactions.documents.append(
            {
                "_id": tid,
                "attachments": [],
                "receipt_status": "pending",
                "ownership": name,
            }
        )
        db.receipt_requests.documents.append(
            {
                "_id": ObjectId(),
                "transaction_id": tid,
                "status": "needed",
                "asked_of_name": name,
                "opened_at": datetime(2026, 1, 1),
                "reminder_count": 0,
            }
        )
    result = await manual_link_files(
        db, file_ids=[meta["id"]], transaction_ids=[tx_a, tx_b], actor_id=None
    )
    assert result["linked"] == 2
    assert result["transactions"] == 2
    file_doc = db.receipt_files.documents[0]
    assert set(file_doc["transaction_ids"]) == {tx_a, tx_b}
    assert all(len(tx["attachments"]) == 1 for tx in db.transactions.documents)
    assert all(r["status"] == "received" for r in db.receipt_requests.documents)


async def test_link_many_files_to_one_row(tmp_path: Path) -> None:
    db = _db()
    m1 = await store_receipt_file_bytes(
        db, tmp_path, filename="p1.jpg", content=b"\xff\xd8\xff\xe01", source="desktop"
    )
    m2 = await store_receipt_file_bytes(
        db, tmp_path, filename="p2.jpg", content=b"\xff\xd8\xff\xe02", source="desktop"
    )
    tx_id = ObjectId()
    db.transactions.documents.append(
        {"_id": tx_id, "attachments": [], "receipt_status": "pending", "ownership": "ALI"}
    )
    db.receipt_requests.documents.append(
        {
            "_id": ObjectId(),
            "transaction_id": tx_id,
            "status": "needed",
            "asked_of_name": "ALI",
            "opened_at": datetime(2026, 1, 1),
            "reminder_count": 0,
        }
    )
    result = await manual_link_files(
        db, file_ids=[m1["id"], m2["id"]], transaction_ids=[tx_id], actor_id=None
    )
    assert result["linked"] == 2
    assert len(db.transactions.documents[0]["attachments"]) == 2
    assert db.receipt_requests.documents[0]["status"] == "received"


async def test_partial_keeps_chase_open(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db, tmp_path, filename="fuel.jpg", content=b"\xff\xd8\xff\xe0fuel", source="desktop"
    )
    tx_id = ObjectId()
    db.transactions.documents.append(
        {"_id": tx_id, "attachments": [], "receipt_status": "pending", "ownership": "ALI"}
    )
    db.receipt_requests.documents.append(
        {
            "_id": ObjectId(),
            "transaction_id": tx_id,
            "status": "needed",
            "asked_of_name": "ALI",
            "opened_at": datetime(2026, 1, 1),
            "reminder_count": 0,
        }
    )
    with pytest.raises(ValueError, match="note"):
        await manual_link_files(
            db,
            file_ids=[meta["id"]],
            transaction_ids=[tx_id],
            actor_id=None,
            partial=True,
            partial_note="x",
        )
    result = await manual_link_files(
        db,
        file_ids=[meta["id"]],
        transaction_ids=[tx_id],
        actor_id=None,
        partial=True,
        partial_note="covers fuel only",
    )
    assert result["partial"] is True
    assert db.transactions.documents[0]["receipt_status"] == "partial"
    assert db.receipt_requests.documents[0]["status"] == "needed"
    assert db.receipt_requests.documents[0]["partial_receipt"] is True
    assert db.receipt_files.documents[0]["partial_note"] == "covers fuel only"
    groups = await chase_groups(db)
    assert len(groups) == 1


async def test_early_upload_then_link(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db, tmp_path, filename="early.jpg", content=b"\xff\xd8\xff\xe0early", source="expo_early"
    )
    assert meta["id"]
    parked = await list_inbox_files(db, status="parked")
    assert parked[0]["inbox_status"] == INBOX_UNMATCHED
    assert parked[0]["transaction_ids"] == []

    tx_id = ObjectId()
    db.transactions.documents.append(
        {
            "_id": tx_id,
            "attachments": [],
            "receipt_status": "pending",
            "ownership": "ALI",
            "item": "LATRA",
            "amount": 100,
            "currency": "TZS",
            "date": datetime(2026, 3, 1),
        }
    )
    db.receipt_requests.documents.append(
        {
            "_id": ObjectId(),
            "transaction_id": tx_id,
            "status": "needed",
            "asked_of_name": "ALI",
            "opened_at": datetime(2026, 3, 1),
            "reminder_count": 0,
        }
    )
    await manual_link_files(
        db, file_ids=[meta["id"]], transaction_ids=[tx_id], actor_id=None
    )
    assert db.receipt_files.documents[0]["inbox_status"] == INBOX_MATCHED
    candidates = await search_link_transactions(db, needed_only=False)
    row = next(c for c in candidates if c["id"] == str(tx_id))
    assert row["coverage"] == "received"
    assert row["attachment_count"] == 1


async def test_search_coverage_and_needed_filter() -> None:
    needed_id = ObjectId()
    partial_id = ObjectId()
    done_id = ObjectId()
    db = _db(
        transactions=[
            {
                "_id": needed_id,
                "date": datetime(2026, 3, 2),
                "amount": 10,
                "ownership": "ALI",
                "receipt_status": "pending",
                "attachments": [],
                "item": "A",
            },
            {
                "_id": partial_id,
                "date": datetime(2026, 3, 2),
                "amount": 20,
                "ownership": "ALI",
                "receipt_status": "partial",
                "attachments": [{"id": "x"}],
                "item": "B",
            },
            {
                "_id": done_id,
                "date": datetime(2026, 3, 2),
                "amount": 30,
                "ownership": "ALI",
                "receipt_status": "received",
                "attachments": [{"id": "y"}],
                "item": "C",
            },
        ],
        requests=[
            {"_id": ObjectId(), "transaction_id": needed_id, "status": "needed"},
            {
                "_id": ObjectId(),
                "transaction_id": partial_id,
                "status": "needed",
                "partial_receipt": True,
                "partial_note": "fuel",
            },
            {"_id": ObjectId(), "transaction_id": done_id, "status": "received"},
        ],
    )
    open_rows = await search_link_transactions(db, needed_only=True)
    ids = {row["id"] for row in open_rows}
    assert str(needed_id) in ids
    assert str(partial_id) in ids
    assert str(done_id) not in ids
    partial = next(r for r in open_rows if r["id"] == str(partial_id))
    assert partial["coverage"] == "partial"
    assert partial["partial_note"] == "fuel"


def test_desk_early_upload_and_link_api(tmp_path: Path) -> None:
    db = _db(transactions=[], requests=[], files=[])
    settings = Settings(
        mongodb_uri="mongodb://127.0.0.1:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
        backup_s3_bucket="",
    )
    app = create_app(settings=settings, database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cashier",
        "role": "cashier",
    }
    tx_id = ObjectId()
    db.transactions.documents.append(
        {
            "_id": tx_id,
            "attachments": [],
            "receipt_status": "pending",
            "ownership": "ALI",
            "date": datetime(2026, 3, 1),
            "amount": 50,
            "currency": "TZS",
            "item": "Fuel",
        }
    )
    db.receipt_requests.documents.append(
        {
            "_id": ObjectId(),
            "transaction_id": tx_id,
            "status": "needed",
            "asked_of_name": "ALI",
            "opened_at": datetime(2026, 3, 1),
            "reminder_count": 0,
        }
    )
    with TestClient(app) as client:
        upload = client.post(
            "/v1/receipts/files/upload",
            files=[("files", ("early.jpg", b"\xff\xd8\xff\xe0jpeg-bytes", "image/jpeg"))],
        )
        assert upload.status_code == 201
        file_id = upload.json()["files"][0]["id"]
        assert any(e.get("action") == "receipt.park" for e in db.operation_events.documents)

        link = client.post(
            "/v1/receipts/link",
            json={
                "file_ids": [file_id],
                "transaction_ids": [str(tx_id)],
                "partial": False,
            },
        )
        assert link.status_code == 200
        assert link.json()["linked"] == 1

        candidates = client.get(
            "/v1/receipts/link-candidates", params={"needed_only": "true"}
        )
        assert candidates.status_code == 200
        # after full link, needed_only should exclude this row
        assert all(row["id"] != str(tx_id) for row in candidates.json()["items"])
