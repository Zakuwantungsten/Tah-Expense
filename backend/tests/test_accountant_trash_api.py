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

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        return FakeCursor(
            [dict(doc) for doc in self.documents if _matches(doc, query)]
        )

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

    async def delete_one(self, query: dict) -> SimpleNamespace:
        for index, doc in enumerate(self.documents):
            if _matches(doc, query):
                del self.documents[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

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


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct1", "role": "accountant"}


def test_trash_list_restore_and_purge() -> None:
    a = ObjectId()
    b = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": a,
                "trashed": True,
                "trashed_at": datetime(2026, 6, 2),
                "date": datetime(2026, 5, 1),
                "description": "Trashed diesel",
                "truck_number": "T100 AAA",
            },
            {
                "_id": b,
                "trashed": True,
                "trashed_at": datetime(2026, 6, 3),
                "date": datetime(2026, 5, 2),
                "description": "Trashed oil",
                "truck_number": "T101 BBB",
            },
            {
                "_id": ObjectId(),
                "trashed": False,
                "description": "Not trash",
            },
        ]
    )
    events = FakeEvents()
    app = _app(collection, events)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        listed = client.get("/v1/accountant/trash")
        count = client.get("/v1/accountant/trash/count")
        badge = client.get("/v1/accountant/counts/trash")
        filtered = client.get("/v1/accountant/trash", params={"search": "diesel"})
        restored = client.post(f"/v1/accountant/trash/{a}/restore")
        purged = client.delete(f"/v1/accountant/trash/{b}")

    assert listed.json()["total"] == 2
    assert count.json()["count"] == 2
    assert badge.json()["count"] == 2
    assert filtered.json()["total"] == 1
    assert restored.json()["ok"] is True
    assert purged.json()["ok"] is True
    assert all(d["_id"] != b for d in collection.documents)
    assert any(e["action"] == "txn.trash_purge" for e in events.documents)


def test_trash_bulk_restore_and_delete() -> None:
    a = ObjectId()
    b = ObjectId()
    collection = FakeTransactions(
        [
            {"_id": a, "trashed": True, "trashed_at": datetime(2026, 6, 1)},
            {"_id": b, "trashed": True, "trashed_at": datetime(2026, 6, 2)},
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        restored = client.post(
            "/v1/accountant/trash/bulk-restore",
            json={"ids": [str(a)]},
        )
        deleted = client.post(
            "/v1/accountant/trash/bulk-delete",
            json={"ids": [str(b)]},
        )

    assert restored.json()["updated"] == 1
    assert deleted.json()["deleted"] == 1
    assert len(collection.documents) == 1
    assert collection.documents[0]["trashed"] is False


def test_verified_lookup_filters_year_month_category() -> None:
    docs = [
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2026, 3, 10),
            "created_at": datetime(2026, 3, 10, 8),
            "category_name": "Fuel",
            "description": "Keep",
        },
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2026, 4, 10),
            "created_at": datetime(2026, 4, 10, 8),
            "category_name": "Oil",
            "description": "Wrong month",
        },
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": True,
            "trashed": False,
            "date": datetime(2026, 3, 11),
            "category_name": "Fuel",
            "description": "Deletion requested out",
        },
        {
            "_id": ObjectId(),
            "verified": False,
            "date": datetime(2026, 3, 12),
            "category_name": "Fuel",
            "description": "Unverified out",
        },
    ]
    app = _app(FakeTransactions(docs))
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        response = client.get(
            "/v1/accountant/verified",
            params={"year": 2026, "month": 3, "category": "Fuel"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["description"] == "Keep"


def test_get_by_ids_still_available_for_managers() -> None:
    """Phase 2 shared endpoint — desktop get_transactions_by_ids reuses this."""
    a = ObjectId()
    b = ObjectId()
    collection = FakeTransactions(
        [
            {"_id": a, "description": "A"},
            {"_id": b, "description": "B"},
        ]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        response = client.get(f"/v1/transactions?ids={a},{b}")

    assert response.status_code == 200
    assert response.json()["total"] == 2
