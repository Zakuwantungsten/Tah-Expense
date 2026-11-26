"""Phase 3 — intended_ownership stamp on early park."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.receipt_ops import (
    INBOX_UNMATCHED,
    ensure_receipt_workspace,
    list_inbox_files,
    park_receipt_uploads,
    resolve_intended_ownership,
    store_receipt_file_bytes,
)


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$exists" in expected:
                exists = key in doc
                if bool(expected["$exists"]) != exists:
                    if expected["$exists"] is False and not exists:
                        continue
                    return False
                continue
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
        doc = dict(document)
        self.documents.append(doc)
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.documents if _matches(doc, query))

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


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
        backup_s3_bucket="",
        receipt_reminder_hours=24,
    )


def _db(*, people: list[dict] | None = None, files: list[dict] | None = None):
    return SimpleNamespace(
        receipt_files=FakeColl(files),
        receipt_requests=FakeColl(),
        transactions=FakeColl(),
        people=FakeColl(people),
        operation_events=FakeEvents(),
        system_settings=FakeColl(),
        users=FakeColl(),
        auth_sessions=FakeColl(),
        categories=FakeColl(),
    )


@pytest.mark.asyncio
async def test_store_stamps_intended_ownership_on_unmatched(tmp_path: Path) -> None:
    ensure_receipt_workspace(tmp_path)
    person_id = ObjectId()
    db = _db(people=[{"_id": person_id, "name": "ALI", "active": True}])
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="early.jpg",
        content=b"\xff\xd8\xff\xe0early",
        source="expo_early",
        intended_ownership="ali",
        asked_of_person_id=person_id,
    )
    assert meta["intended_ownership"] == "ALI"
    doc = db.receipt_files.documents[0]
    assert doc["inbox_status"] == INBOX_UNMATCHED
    assert doc["intended_ownership"] == "ALI"
    assert doc["asked_of_person_id"] == person_id


@pytest.mark.asyncio
async def test_linked_store_skips_intended_ownership(tmp_path: Path) -> None:
    ensure_receipt_workspace(tmp_path)
    tx_id = ObjectId()
    db = _db()
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="linked.jpg",
        content=b"\xff\xd8\xff\xe0linked",
        source="desktop",
        transaction_id=tx_id,
        intended_ownership="ALI",
    )
    assert "intended_ownership" not in meta
    assert db.receipt_files.documents[0]["intended_ownership"] is None


@pytest.mark.asyncio
async def test_resolve_intended_ownership_from_user() -> None:
    user_id = ObjectId()
    person_id = ObjectId()
    db = _db(
        people=[
            {
                "_id": person_id,
                "name": "ALI",
                "user_id": user_id,
                "active": True,
            }
        ]
    )
    name, pid = await resolve_intended_ownership(
        db,
        user={"_id": user_id, "full_name": "Ali", "username": "ali"},
    )
    assert name == "ALI"
    assert pid == person_id


@pytest.mark.asyncio
async def test_park_batch_stamps_ownership(tmp_path: Path) -> None:
    ensure_receipt_workspace(tmp_path)
    db = _db(people=[{"_id": ObjectId(), "name": "BOB", "active": True}])
    saved = await park_receipt_uploads(
        db,
        tmp_path,
        uploads=[("a.jpg", b"\xff\xd8\xff\xe0aaa"), ("b.jpg", b"\xff\xd8\xff\xe0bbb")],
        actor_id=ObjectId(),
        intended_ownership="bob",
    )
    assert len(saved) == 2
    assert all(m.get("intended_ownership") == "BOB" for m in saved)
    inbox = await list_inbox_files(db, status="parked", ownership="BOB")
    assert len(inbox) == 2


def test_desk_park_api_accepts_ownership(tmp_path: Path) -> None:
    db = _db(people=[{"_id": ObjectId(), "name": "ALI", "active": True}])
    app = create_app(settings=_settings(tmp_path), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "desk",
        "role": "cashier",
    }
    with TestClient(app) as client:
        response = client.post(
            "/v1/receipts/files/upload",
            data={"ownership": "ali"},
            files=[
                ("files", ("early.jpg", b"\xff\xd8\xff\xe0jpeg-bytes", "image/jpeg"))
            ],
        )
    assert response.status_code == 201
    payload = response.json()
    assert payload["intended_ownership"] == "ALI"
    assert payload["files"][0]["intended_ownership"] == "ALI"
    assert db.receipt_files.documents[0]["intended_ownership"] == "ALI"


def test_mobile_early_upload_stamps_from_user(tmp_path: Path) -> None:
    user_id = ObjectId()
    db = _db(
        people=[
            {
                "_id": ObjectId(),
                "name": "ALI",
                "user_id": user_id,
                "active": True,
            }
        ]
    )
    app = create_app(settings=_settings(tmp_path), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": user_id,
        "username": "ali",
        "full_name": "Ali",
        "role": "cashier",
    }
    with TestClient(app) as client:
        response = client.post(
            "/v1/mobile/receipt-files",
            files=[
                ("files", ("early.jpg", b"\xff\xd8\xff\xe0jpeg-bytes", "image/jpeg"))
            ],
        )
    assert response.status_code == 201
    payload = response.json()
    assert payload["intended_ownership"] == "ALI"
    assert db.receipt_files.documents[0]["intended_ownership"] == "ALI"
