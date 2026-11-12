"""Phase 1 — receipt requests, status sync, and mobile upload path."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.receipt_ops import (
    ensure_receipt_request,
    sync_receipt_status_after_attach,
    sync_receipt_status_after_detach,
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
            if "$lte" in expected:
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
        if "_id" not in doc:
            doc["_id"] = ObjectId()
        self.documents.append(doc)
        document["_id"] = doc["_id"]
        return SimpleNamespace(inserted_id=doc["_id"])

    async def update_one(
        self, query: dict, update: dict, *, upsert: bool = False
    ) -> SimpleNamespace:
        for doc in self.documents:
            if not _matches(doc, query):
                continue
            if "$set" in update:
                for key, value in update["$set"].items():
                    if key.startswith("attachments.$."):
                        field = key.split(".", 2)[-1]
                        att_id = query.get("attachments.id")
                        for item in doc.get("attachments") or []:
                            if item.get("id") == att_id:
                                item[field] = value
                    else:
                        doc[key] = value
            if "$push" in update:
                for key, value in update["$push"].items():
                    doc.setdefault(key, []).append(value)
            if "$addToSet" in update:
                for key, value in update["$addToSet"].items():
                    items = list(doc.get(key) or [])
                    if value not in items:
                        items.append(value)
                    doc[key] = items
            if "$inc" in update:
                for key, value in update["$inc"].items():
                    doc[key] = int(doc.get(key) or 0) + int(value)
            if "$pull" in update:
                for key, criteria in update["$pull"].items():
                    items = list(doc.get(key) or [])
                    if isinstance(criteria, dict):
                        doc[key] = [
                            item
                            for item in items
                            if not all(
                                item.get(field) == expected
                                for field, expected in criteria.items()
                            )
                        ]
            return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            doc = dict(query)
            doc.update(update.get("$set") or {})
            if "_id" not in doc:
                doc["_id"] = ObjectId()
            self.documents.append(doc)
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def find(self, query: dict, projection: dict | None = None):
        matched = [dict(doc) for doc in self.documents if _matches(doc, query)]

        class _Cursor:
            def sort(self, *_a, **_k):
                return self

            def limit(self, _n: int):
                return self

            def __aiter__(self):
                async def _gen():
                    for item in matched:
                        yield item

                return _gen()

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


def _db(
    *,
    transactions: list[dict] | None = None,
    categories: list[dict] | None = None,
    people: list[dict] | None = None,
    requests: list[dict] | None = None,
):
    return SimpleNamespace(
        transactions=FakeColl(transactions),
        categories=FakeColl(categories),
        people=FakeColl(people),
        receipt_requests=FakeColl(requests),
        receipt_files=FakeColl(),
        operation_events=FakeEvents(),
        users=FakeColl(),
        auth_sessions=FakeColl(),
        system_settings=FakeColl(),
    )


async def test_ensure_request_opens_when_item_requires_receipt() -> None:
    cat_id = ObjectId()
    tx_id = ObjectId()
    db = _db(
        categories=[{"_id": cat_id, "name": "LATRA", "requires_receipt": True}],
        people=[{"_id": ObjectId(), "name": "ALI", "active": True}],
        transactions=[
            {
                "_id": tx_id,
                "ownership": "ALI",
                "category_id": cat_id,
                "item": "LATRA",
                "attachments": [],
                "receipt_status": "pending",
            }
        ],
    )
    tx = await db.transactions.find_one({"_id": tx_id})
    req = await ensure_receipt_request(db, tx)
    assert req is not None
    assert req["status"] == "needed"
    assert req["asked_of_name"] == "ALI"
    assert req["transaction_id"] == tx_id
    again = await ensure_receipt_request(db, tx)
    assert again["_id"] == req["_id"]
    assert len(db.receipt_requests.documents) == 1
    assert req.get("immediate_notify_pending") is True


async def test_ensure_request_opens_for_ownership_pending_without_category_flag() -> None:
    """Master-style: ownership + pending receipt opens chase even if item has no flag."""
    tx_id = ObjectId()
    db = _db(
        categories=[{"_id": ObjectId(), "name": "MISC", "requires_receipt": False}],
        people=[{"_id": ObjectId(), "name": "SAADA", "active": True}],
        transactions=[
            {
                "_id": tx_id,
                "ownership": "SAADA",
                "item": "MISC",
                "attachments": [],
                "receipt_status": "pending",
            }
        ],
    )
    req = await ensure_receipt_request(db, await db.transactions.find_one({"_id": tx_id}))
    assert req is not None
    assert req["status"] == "needed"
    assert req["asked_of_name"] == "SAADA"
    assert req.get("immediate_notify_pending") is True


async def test_ensure_request_opens_for_ui_label_receipt_requires() -> None:
    """UI label RECEIPT means requires a receipt — open chase + notify."""
    from app.receipt_ops import normalize_receipt_status

    assert normalize_receipt_status("RECEIPT") == "receipt"
    assert normalize_receipt_status("NO RECEIPT") == "no_receipt"
    assert normalize_receipt_status("PENDING") == "pending"
    assert normalize_receipt_status("RECEIVED") == "received"
    tx_id = ObjectId()
    db = _db(
        categories=[{"_id": ObjectId(), "name": "MISC", "requires_receipt": False}],
        people=[{"_id": ObjectId(), "name": "OWNER TEST", "active": True}],
        transactions=[
            {
                "_id": tx_id,
                "ownership": "OWNER TEST",
                "item": "FACILITATION",
                "attachments": [],
                "receipt_status": "RECEIPT",
            }
        ],
    )
    req = await ensure_receipt_request(db, await db.transactions.find_one({"_id": tx_id}))
    assert req is not None
    assert req["status"] == "needed"
    assert req.get("immediate_notify_pending") is True


async def test_ensure_request_skips_no_receipt() -> None:
    tx_id = ObjectId()
    db = _db(
        categories=[{"_id": ObjectId(), "name": "MISC", "requires_receipt": False}],
        people=[{"_id": ObjectId(), "name": "OWNER TEST", "active": True}],
        transactions=[
            {
                "_id": tx_id,
                "ownership": "OWNER TEST",
                "item": "FACILITATION",
                "attachments": [],
                "receipt_status": "NO RECEIPT",
            }
        ],
    )
    req = await ensure_receipt_request(db, await db.transactions.find_one({"_id": tx_id}))
    assert req is None


async def test_ensure_request_skips_when_files_already_linked() -> None:
    cat_id = ObjectId()
    tx_id = ObjectId()
    db = _db(
        categories=[{"_id": cat_id, "name": "LATRA", "requires_receipt": True}],
        transactions=[
            {
                "_id": tx_id,
                "ownership": "ALI",
                "category_id": cat_id,
                "attachments": [{"id": "x", "filename": "a.pdf"}],
            }
        ],
    )
    req = await ensure_receipt_request(db, await db.transactions.find_one({"_id": tx_id}))
    assert req is None


async def test_attach_sets_received_and_detach_restores_receipt() -> None:
    tx_id = ObjectId()
    cat_id = ObjectId()
    db = _db(
        categories=[{"_id": cat_id, "name": "LATRA", "requires_receipt": True}],
        transactions=[
            {
                "_id": tx_id,
                "ownership": "ALI",
                "category_id": cat_id,
                "attachments": [],
                "receipt_status": "pending",
            }
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_id,
                "status": "needed",
                "asked_of_name": "ALI",
                "file_ids": [],
            }
        ],
    )
    db.transactions.documents[0]["attachments"] = [{"id": "a1", "filename": "r.jpg"}]
    await sync_receipt_status_after_attach(db, tx_id)
    assert db.transactions.documents[0]["receipt_status"] == "received"
    assert db.receipt_requests.documents[0]["status"] == "received"

    db.transactions.documents[0]["attachments"] = []
    await sync_receipt_status_after_detach(db, tx_id)
    assert db.transactions.documents[0]["receipt_status"] == "receipt"
    assert db.receipt_requests.documents[0]["status"] == "needed"


async def test_detach_honors_waived_missing() -> None:
    tx_id = ObjectId()
    db = _db(
        transactions=[
            {
                "_id": tx_id,
                "receipt_status": "missing",
                "attachments": [],
                "ownership": "ALI",
            }
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_id,
                "status": "waived",
                "asked_of_name": "ALI",
            }
        ],
    )
    await sync_receipt_status_after_detach(db, tx_id)
    assert db.transactions.documents[0]["receipt_status"] == "missing"
    assert db.receipt_requests.documents[0]["status"] == "waived"


def test_mobile_list_upload_sets_received(tmp_path: Path) -> None:
    cat_id = ObjectId()
    tx_id = ObjectId()
    person_id = ObjectId()
    req_id = ObjectId()
    user_id = ObjectId()
    db = _db(
        categories=[{"_id": cat_id, "name": "LATRA", "requires_receipt": True}],
        people=[
            {
                "_id": person_id,
                "name": "ALI",
                "user_id": user_id,
                "receives_receipt_requests": True,
                "expo_push_tokens": [],
            }
        ],
        transactions=[
            {
                "_id": tx_id,
                "date": datetime(2026, 3, 2),
                "amount": 45000,
                "currency": "TZS",
                "item": "LATRA",
                "category_id": cat_id,
                "ownership": "ALI",
                "payee": "TRA",
                "attachments": [],
                "receipt_status": "pending",
            }
        ],
        requests=[
            {
                "_id": req_id,
                "transaction_id": tx_id,
                "asked_of_name": "ALI",
                "asked_of_person_id": person_id,
                "status": "needed",
                "opened_at": datetime(2026, 3, 2),
                "file_ids": [],
                "reminder_count": 0,
            }
        ],
    )
    app = create_app(settings=_settings(tmp_path), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": user_id,
        "username": "ali",
        "full_name": "ALI",
        "role": "cashier",
    }

    with TestClient(app) as client:
        listed = client.get("/v1/mobile/receipt-requests")
        assert listed.status_code == 200
        body = listed.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == str(req_id)

        uploaded = client.post(
            f"/v1/mobile/receipt-requests/{req_id}/files",
            files=[("files", ("receipt.jpg", b"\xff\xd8\xff\xe0JPEGDATA", "image/jpeg"))],
        )
        assert uploaded.status_code == 201, uploaded.text
        payload = uploaded.json()
        assert payload["request"]["status"] == "received"
        assert payload["files"]
        assert db.transactions.documents[0]["receipt_status"] == "received"
        assert db.receipt_requests.documents[0]["status"] == "received"
        stored = tmp_path / payload["files"][0]["stored_name"]
        assert stored.is_file()
        assert stored.read_bytes() == b"\xff\xd8\xff\xe0JPEGDATA"


def test_mobile_waive_sets_missing(tmp_path: Path) -> None:
    tx_id = ObjectId()
    req_id = ObjectId()
    user_id = ObjectId()
    db = _db(
        people=[{"_id": ObjectId(), "name": "ALI", "user_id": user_id}],
        transactions=[
            {
                "_id": tx_id,
                "ownership": "ALI",
                "attachments": [],
                "receipt_status": "pending",
                "item": "LATRA",
                "amount": 1,
                "currency": "TZS",
            }
        ],
        requests=[
            {
                "_id": req_id,
                "transaction_id": tx_id,
                "asked_of_name": "ALI",
                "status": "needed",
                "file_ids": [],
            }
        ],
    )
    app = create_app(settings=_settings(tmp_path), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": user_id,
        "username": "ali",
        "full_name": "ALI",
        "role": "cashier",
    }
    with TestClient(app) as client:
        response = client.post(
            f"/v1/mobile/receipt-requests/{req_id}/waive",
            json={"reason": "Lost the paper"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "waived"
    assert db.transactions.documents[0]["receipt_status"] == "missing"


def test_heic_is_allowed_extension() -> None:
    from app.attachment_ops import allowed_extension

    assert allowed_extension("photo.heic")
    assert allowed_extension("photo.HEIF")
