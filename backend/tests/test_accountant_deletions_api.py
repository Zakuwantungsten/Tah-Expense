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


def _manager(user_id: ObjectId | None = None) -> dict:
    return {
        "_id": user_id or ObjectId(),
        "username": "acct1",
        "role": "accountant",
    }


def test_deletions_list_meta_and_restore() -> None:
    c1 = ObjectId()
    tx_a = ObjectId()
    tx_b = ObjectId()
    docs = [
        {
            "_id": tx_a,
            "deletion_requested": True,
            "trashed": False,
            "deletion_requested_at": datetime(2026, 6, 2),
            "date": datetime(2026, 5, 1),
            "truck_number": "T100 AAA",
            "description": "Delete me",
            "item": "Diesel",
            "category_name": "Fuel",
            "cashier_id": c1,
        },
        {
            "_id": tx_b,
            "deletion_requested": True,
            "trashed": False,
            "deletion_requested_at": datetime(2026, 6, 3),
            "date": datetime(2026, 5, 2),
            "truck_number": "T101 BBB",
            "description": "Also delete",
            "item": "Oil",
            "cashier_id": c1,
        },
        {
            "_id": ObjectId(),
            "deletion_requested": True,
            "trashed": True,
            "description": "Already trashed",
            "truck_number": "T999",
        },
    ]
    app = _app(FakeTransactions(docs))
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        listed = client.get("/v1/accountant/deletions")
        count = client.get("/v1/accountant/deletions/count")
        badge = client.get("/v1/accountant/counts/deletions")
        trucks = client.get("/v1/accountant/deletions/meta/trucks")
        items = client.get("/v1/accountant/deletions/meta/items")
        descs = client.get("/v1/accountant/deletions/meta/descriptions")
        cashiers = client.get("/v1/accountant/deletions/meta/cashiers")
        restored = client.post(f"/v1/accountant/deletions/{tx_b}/restore")

    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert count.json()["count"] == 2
    assert badge.json()["count"] == 2
    assert trucks.json()["items"] == ["T100 AAA", "T101 BBB"]
    assert "Diesel" in items.json()["items"]
    assert "Delete me" in descs.json()["items"]
    assert cashiers.json()["items"] == [str(c1)]
    assert restored.json()["ok"] is True


def test_confirm_deletion_trashes_and_removes_pending_clones() -> None:
    original_id = ObjectId()
    pending_id = ObjectId()
    manager = _manager()
    collection = FakeTransactions(
        [
            {
                "_id": original_id,
                "deletion_requested": True,
                "trashed": False,
                "description": "Original",
            },
            {
                "_id": pending_id,
                "original_transaction_id": original_id,
                "verified": False,
                "description": "Pending clone",
            },
        ]
    )
    events = FakeEvents()
    app = _app(collection, events)
    app.dependency_overrides[current_user] = lambda: manager

    with TestClient(app) as client:
        confirm = client.post(f"/v1/accountant/deletions/{original_id}/confirm")
        bulk = client.post(
            "/v1/accountant/deletions/bulk-confirm",
            json={"ids": [str(ObjectId())]},
        )

    assert confirm.json()["ok"] is True
    assert all(d["_id"] != pending_id for d in collection.documents)
    original = next(d for d in collection.documents if d["_id"] == original_id)
    assert original["trashed"] is True
    assert original["deletion_requested"] is False
    assert original["trashed_by"] == manager["_id"]
    assert bulk.json()["updated"] == 0
    assert any(e["action"] == "txn.trash" for e in events.documents)


def test_bulk_restore_deletions() -> None:
    a = ObjectId()
    b = ObjectId()
    collection = FakeTransactions(
        [
            {"_id": a, "deletion_requested": True, "trashed": False},
            {"_id": b, "deletion_requested": True, "trashed": False},
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        response = client.post(
            "/v1/accountant/deletions/bulk-restore",
            json={"ids": [str(a), str(b)]},
        )

    assert response.json()["updated"] == 2
    assert all(d["deletion_requested"] is False for d in collection.documents)


def test_deletions_forbid_cashier() -> None:
    app = _app(FakeTransactions())
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cash",
        "role": "cashier",
    }

    with TestClient(app) as client:
        response = client.get("/v1/accountant/deletions")

    assert response.status_code == 403
