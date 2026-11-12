"""Phase 3 — chase / inbox / mark-reminded / verify receipt gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.receipt_ops import (
    chase_groups,
    list_inbox_files,
    mark_requests_reminded,
    reject_if_needed_receipts,
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

    async def update_one(
        self, query: dict, update: dict, *, upsert: bool = False
    ) -> SimpleNamespace:
        for doc in self.documents:
            if not _matches(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            if "$inc" in update:
                for key, value in update["$inc"].items():
                    doc[key] = int(doc.get(key) or 0) + int(value)
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query: dict, update: dict) -> SimpleNamespace:
        count = 0
        for doc in self.documents:
            if not _matches(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            if "$inc" in update:
                for key, value in update["$inc"].items():
                    doc[key] = int(doc.get(key) or 0) + int(value)
            count += 1
        return SimpleNamespace(matched_count=count, modified_count=count)

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


def _db(
    *,
    transactions: list[dict] | None = None,
    categories: list[dict] | None = None,
    requests: list[dict] | None = None,
    files: list[dict] | None = None,
):
    return SimpleNamespace(
        transactions=FakeColl(transactions),
        categories=FakeColl(categories),
        receipt_requests=FakeColl(requests),
        receipt_files=FakeColl(files),
        operation_events=FakeEvents(),
        system_settings=FakeColl(),
        users=FakeColl(),
        auth_sessions=FakeColl(),
        people=FakeColl(),
    )


async def test_chase_groups_by_ownership_oldest_first() -> None:
    now = datetime.now(UTC)
    old_id = ObjectId()
    new_id = ObjectId()
    db = _db(
        requests=[
            {
                "_id": new_id,
                "status": "needed",
                "asked_of_name": "ALI",
                "opened_at": now - timedelta(hours=2),
                "reminder_count": 0,
                "transaction_id": ObjectId(),
            },
            {
                "_id": old_id,
                "status": "needed",
                "asked_of_name": "ALI",
                "opened_at": now - timedelta(hours=50),
                "last_reminded_at": now - timedelta(hours=10),
                "reminder_count": 2,
                "transaction_id": ObjectId(),
            },
            {
                "_id": ObjectId(),
                "status": "needed",
                "asked_of_name": "BETA",
                "opened_at": now - timedelta(hours=5),
                "reminder_count": 0,
                "transaction_id": ObjectId(),
            },
            {
                "_id": ObjectId(),
                "status": "received",
                "asked_of_name": "ALI",
                "opened_at": now - timedelta(hours=100),
            },
        ]
    )
    groups = await chase_groups(db)
    assert [g["ownership"] for g in groups] == ["ALI", "BETA"]
    ali = groups[0]
    assert ali["open_count"] == 2
    assert ali["reminder_count"] == 2
    assert old_id.binary  # sanity
    assert str(old_id) in ali["request_ids"]
    assert ali["oldest_age_hours"] >= 49


async def test_list_inbox_unmatched_filter() -> None:
    db = _db(
        files=[
            {
                "_id": "f1",
                "original_filename": "a.jpg",
                "inbox_status": "unmatched",
                "uploaded_at": datetime(2026, 1, 2),
                "size": 10,
                "source": "mobile",
            },
            {
                "_id": "f2",
                "original_filename": "b.jpg",
                "inbox_status": "matched",
                "uploaded_at": datetime(2026, 1, 3),
                "size": 20,
                "source": "mobile",
            },
        ]
    )
    all_items = await list_inbox_files(db, unmatched_only=False)
    assert {row["id"] for row in all_items} == {"f1", "f2"}
    open_only = await list_inbox_files(db, unmatched_only=True)
    assert [row["id"] for row in open_only] == ["f1"]


async def test_mark_requests_reminded_by_ownership() -> None:
    rid = ObjectId()
    db = _db(
        requests=[
            {
                "_id": rid,
                "status": "needed",
                "asked_of_name": "ALI",
                "reminder_count": 1,
            },
            {
                "_id": ObjectId(),
                "status": "needed",
                "asked_of_name": "BETA",
                "reminder_count": 0,
            },
        ]
    )
    updated = await mark_requests_reminded(db, ownership="ALI")
    assert updated == 1
    doc = await db.receipt_requests.find_one({"_id": rid})
    assert doc["reminder_count"] == 2
    assert doc.get("last_reminded_at") is not None


async def test_reject_if_needed_receipts_blocks_required_item() -> None:
    cat_id = ObjectId()
    tx_id = ObjectId()
    db = _db(
        categories=[{"_id": cat_id, "name": "LATRA", "requires_receipt": True}],
        transactions=[
            {
                "_id": tx_id,
                "category_id": cat_id,
                "item": "LATRA",
                "attachments": [],
            }
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_id,
                "status": "needed",
            }
        ],
    )
    with pytest.raises(ValueError, match="still need a receipt"):
        await reject_if_needed_receipts(db, [tx_id])


async def test_reject_if_needed_receipts_allows_when_received() -> None:
    cat_id = ObjectId()
    tx_id = ObjectId()
    db = _db(
        categories=[{"_id": cat_id, "name": "LATRA", "requires_receipt": True}],
        transactions=[
            {
                "_id": tx_id,
                "category_id": cat_id,
                "item": "LATRA",
                "attachments": [{"id": "x"}],
            }
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_id,
                "status": "received",
            }
        ],
    )
    await reject_if_needed_receipts(db, [tx_id])


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
        backup_s3_bucket="",
        receipt_reminder_hours=24,
    )


def _api_app(tmp_path: Path, db):
    database = SimpleNamespace(db=db)
    return create_app(settings=_settings(tmp_path), database=database)


def _user(*, role: str = "cashier") -> dict:
    return {"_id": ObjectId(), "username": "desk", "role": role}


def test_cashier_can_list_chase_and_inbox(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    db = _db(
        requests=[
            {
                "_id": ObjectId(),
                "status": "needed",
                "asked_of_name": "ALI",
                "opened_at": now - timedelta(hours=8),
                "reminder_count": 0,
                "transaction_id": ObjectId(),
            }
        ],
        files=[
            {
                "_id": "f1",
                "original_filename": "shot.jpg",
                "inbox_status": "unmatched",
                "uploaded_at": now,
                "size": 100,
                "source": "mobile",
            }
        ],
    )
    app = _api_app(tmp_path, db)
    app.dependency_overrides[current_user] = lambda: _user(role="cashier")
    with TestClient(app) as client:
        chase = client.get("/v1/receipts/chase")
        inbox = client.get("/v1/receipts/inbox", params={"unmatched_only": True})
        mark = client.post(
            "/v1/receipts/mark-reminded",
            json={"ownership": "ALI"},
        )
    assert chase.status_code == 200
    assert chase.json()["total"] == 1
    assert chase.json()["items"][0]["ownership"] == "ALI"
    assert inbox.status_code == 200
    assert inbox.json()["items"][0]["id"] == "f1"
    assert mark.status_code == 200
    assert mark.json()["updated"] == 1


def test_approve_blocked_when_receipt_needed(tmp_path: Path) -> None:
    cat_id = ObjectId()
    tx_id = ObjectId()
    db = _db(
        categories=[{"_id": cat_id, "name": "LATRA", "requires_receipt": True}],
        transactions=[
            {
                "_id": tx_id,
                "category_id": cat_id,
                "item": "LATRA",
                "payee": "VENDOR",
                "verified": False,
                "rejected": False,
                "attachments": [],
            }
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_id,
                "status": "needed",
            }
        ],
    )
    app = _api_app(tmp_path, db)
    app.dependency_overrides[current_user] = lambda: _user(role="accountant")
    with TestClient(app) as client:
        response = client.post(f"/v1/accountant/transactions/{tx_id}/approve")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "receipt_needed"
