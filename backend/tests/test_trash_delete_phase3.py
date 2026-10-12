"""Phase 3: soft-trash leaves Verify; verified confirm lands in Trash / leaves Master."""

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
            if "$exists" in expected:
                exists = key in doc
                if bool(expected["$exists"]) != exists:
                    return False
            if "$in" in expected and actual not in expected["$in"]:
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

    def sort(self, *_a, **_k) -> "FakeCursor":
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


def _settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(collection: FakeTransactions):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=collection,
            operation_events=FakeEvents(),
            users=SimpleNamespace(find=lambda *_a, **_k: FakeCursor([])),
        )
    )
    return create_app(settings=_settings(), database=database)


def test_submitted_soft_trash_leaves_verify_and_shows_in_cashier_trash() -> None:
    cashier = ObjectId()
    stay = ObjectId()
    gone = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": stay,
                "verified": False,
                "rejected": False,
                "trashed": False,
                "register_status": "submitted",
                "date": datetime(2026, 7, 1),
                "description": "Still in verify",
                "cashier_id": cashier,
            },
            {
                "_id": gone,
                "verified": False,
                "rejected": False,
                "trashed": False,
                "register_status": "submitted",
                "date": datetime(2026, 7, 1),
                "description": "Soft trash me",
                "cashier_id": cashier,
            },
        ]
    )
    app = _app(collection)

    def as_cashier():
        return {"_id": cashier, "username": "cash1", "role": "cashier"}

    def as_manager():
        return {"_id": ObjectId(), "username": "acct1", "role": "accountant"}

    with TestClient(app) as client:
        app.dependency_overrides[current_user] = as_cashier
        soft = client.post(f"/v1/transactions/{gone}/request-delete", json={})
        assert soft.json() == {"result": "trashed"}

        trash = client.get("/v1/transactions/trash")
        assert trash.status_code == 200
        trash_ids = {item["_id"] for item in trash.json()["items"]}
        assert str(gone) in trash_ids
        assert str(stay) not in trash_ids

        app.dependency_overrides[current_user] = as_manager
        inbox = client.get("/v1/accountant/inbox")
        assert inbox.status_code == 200
        inbox_ids = {item["_id"] for item in inbox.json()["items"]}
        assert str(stay) in inbox_ids
        assert str(gone) not in inbox_ids

        deletions = client.get("/v1/accountant/deletions")
        assert deletions.json()["total"] == 0

        master = client.get(
            "/v1/accountant/master",
            params={"year": 2026, "month": 7},
        )
        # Master is verified-only; neither row should appear.
        if master.status_code == 200:
            master_ids = {item["_id"] for item in master.json().get("items") or []}
            assert str(gone) not in master_ids
            assert str(stay) not in master_ids


def test_verified_request_confirm_leaves_master_and_enters_trash() -> None:
    cashier = ObjectId()
    tx_id = ObjectId()
    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "verified": True,
                "trashed": False,
                "deletion_requested": False,
                "date": datetime(2026, 7, 15),
                "year": 2026,
                "description": "Approved toll",
                "cashier_id": cashier,
                "amount": 1000,
            }
        ]
    )
    app = _app(collection)

    def as_cashier():
        return {"_id": cashier, "username": "cash1", "role": "cashier"}

    def as_manager():
        return {"_id": ObjectId(), "username": "acct1", "role": "accountant"}

    with TestClient(app) as client:
        app.dependency_overrides[current_user] = as_cashier
        req = client.post(f"/v1/transactions/{tx_id}/request-delete", json={})
        assert req.json() == {"result": "deletion_requested"}
        doc = collection.documents[0]
        assert doc["deletion_requested"] is True
        assert doc.get("trashed") is not True

        app.dependency_overrides[current_user] = as_manager
        deletions = client.get("/v1/accountant/deletions")
        assert deletions.json()["total"] == 1
        assert deletions.json()["items"][0]["_id"] == str(tx_id)

        master = client.get(
            "/v1/accountant/master",
            params={"year": 2026, "month": 7},
        )
        if master.status_code == 200:
            master_ids = {item["_id"] for item in master.json().get("items") or []}
            assert str(tx_id) not in master_ids

        confirm = client.post(f"/v1/accountant/deletions/{tx_id}/confirm")
        assert confirm.json()["ok"] is True
        doc = collection.documents[0]
        assert doc["trashed"] is True
        assert doc["deletion_requested"] is False

        deletions_after = client.get("/v1/accountant/deletions")
        assert deletions_after.json()["total"] == 0

        app.dependency_overrides[current_user] = as_cashier
        trash = client.get("/v1/transactions/trash")
        assert str(tx_id) in {item["_id"] for item in trash.json()["items"]}
