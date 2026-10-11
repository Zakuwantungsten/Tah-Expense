"""Cashier trash + accountant restore-request flow."""

from datetime import datetime
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(doc, clause) for clause in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, clause) for clause in expected):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$regex" in expected:
                import re

                pattern = expected["$regex"]
                flags = re.I if "i" in str(expected.get("$options", "")) else 0
                if actual is None or re.search(pattern, str(actual), flags) is None:
                    return False
                continue
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


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = list(documents)
        self._skip = 0
        self._limit: int | None = None

    def sort(self, *_args: object, **_kwargs: object) -> "FakeCursor":
        return self

    def skip(self, value: int) -> "FakeCursor":
        self._skip = value
        return self

    def limit(self, value: int) -> "FakeCursor":
        self._limit = value
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        docs = list(self.documents)[self._skip :]
        if self._limit is not None:
            docs = docs[: self._limit]
        if length is None:
            return docs
        return docs[:length]


class FakeTransactions:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def find_one(self, query: dict) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        return FakeCursor(
            [dict(doc) for doc in self.documents if _matches(doc, query)]
        )

    async def distinct(self, field: str, query: dict | None = None) -> list:
        query = query or {}
        values: list = []
        for doc in self.documents:
            if _matches(doc, query):
                value = doc.get(field)
                if value and value not in values:
                    values.append(value)
        return values

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.documents if _matches(doc, query))

    async def update_one(self, query: dict, update: dict) -> SimpleNamespace:
        for doc in self.documents:
            if _matches(doc, query):
                doc.update(update.get("$set") or {})
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query: dict, update: dict) -> SimpleNamespace:
        count = 0
        for doc in self.documents:
            if _matches(doc, query):
                doc.update(update.get("$set") or {})
                count += 1
        return SimpleNamespace(matched_count=count, modified_count=count)

    async def delete_many(self, query: dict) -> SimpleNamespace:
        keep: list[dict] = []
        deleted = 0
        for doc in self.documents:
            if _matches(doc, query):
                deleted += 1
            else:
                keep.append(doc)
        self.documents = keep
        return SimpleNamespace(deleted_count=deleted)


class FakeEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=ObjectId())


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(collection: FakeTransactions, events: FakeEvents | None = None):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=collection,
            operation_events=events or FakeEvents(),
        )
    )
    return create_app(settings=settings(), database=database)


def test_cashier_trash_list_and_request_restore() -> None:
    cashier = ObjectId()
    other = ObjectId()
    mine = ObjectId()
    theirs = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": mine,
                "trashed": True,
                "cashier_id": cashier,
                "description": "My deleted toll",
                "truck_number": "T100",
                "date": datetime(2026, 5, 1),
                "trashed_at": datetime(2026, 5, 2),
                "restore_requested": False,
            },
            {
                "_id": theirs,
                "trashed": True,
                "cashier_id": other,
                "description": "Other cashier",
                "truck_number": "T200",
                "date": datetime(2026, 5, 1),
                "trashed_at": datetime(2026, 5, 2),
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {
        "_id": cashier,
        "username": "cash1",
        "role": "cashier",
    }
    with TestClient(app) as client:
        listed = client.get("/v1/transactions/trash")
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["_id"] == str(mine)

        req = client.post(f"/v1/transactions/{mine}/request-restore", json={})
        assert req.status_code == 200
        assert req.json()["result"] == "restore_requested"
        assert collection.documents[0]["restore_requested"] is True
        assert collection.documents[0]["restore_requested_by"] == cashier

        again = client.post(f"/v1/transactions/{mine}/request-restore", json={})
        assert again.json()["result"] == "already_requested"


def test_accountant_confirm_and_reject_restore() -> None:
    cashier = ObjectId()
    tx_a = ObjectId()
    tx_b = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": tx_a,
                "trashed": True,
                "restore_requested": True,
                "restore_requested_at": datetime(2026, 6, 1),
                "restore_requested_by": cashier,
                "cashier_id": cashier,
                "verified": True,
                "description": "Restore me",
                "truck_number": "T100",
                "date": datetime(2026, 5, 1),
            },
            {
                "_id": tx_b,
                "trashed": True,
                "restore_requested": True,
                "restore_requested_at": datetime(2026, 6, 2),
                "restore_requested_by": cashier,
                "cashier_id": cashier,
                "verified": True,
                "description": "Keep me",
                "truck_number": "T101",
                "date": datetime(2026, 5, 2),
            },
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "acct1",
        "role": "accountant",
    }
    with TestClient(app) as client:
        listed = client.get("/v1/accountant/restores")
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 2

        confirmed = client.post(f"/v1/accountant/restores/{tx_a}/confirm")
        assert confirmed.json()["ok"] is True
        a = next(d for d in collection.documents if d["_id"] == tx_a)
        assert a["trashed"] is False
        assert a["restore_requested"] is False

        rejected = client.post(f"/v1/accountant/restores/{tx_b}/reject")
        assert rejected.json()["ok"] is True
        b = next(d for d in collection.documents if d["_id"] == tx_b)
        assert b["trashed"] is True
        assert b["restore_requested"] is False

        pending = client.get("/v1/accountant/counts/pending")
        assert pending.status_code == 200
        # no inbox / deletions / restores left for these docs
        assert pending.json()["count"] == 0


def test_cashier_cancel_restore_request() -> None:
    cashier = ObjectId()
    tx_id = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "trashed": True,
                "cashier_id": cashier,
                "restore_requested": True,
                "restore_requested_at": datetime(2026, 6, 1),
                "restore_requested_by": cashier,
            }
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {
        "_id": cashier,
        "username": "cash1",
        "role": "cashier",
    }
    with TestClient(app) as client:
        res = client.post(f"/v1/transactions/{tx_id}/cancel-restore")
        assert res.json()["ok"] is True
        assert collection.documents[0]["restore_requested"] is False
