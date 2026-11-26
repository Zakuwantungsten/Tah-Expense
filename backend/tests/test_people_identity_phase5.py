"""Phase 5 — people identity (user_id + receives_receipt_requests)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class FakePeople:
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

    async def find_one_and_update(
        self, query, update, upsert=False, return_document=None
    ):
        values = dict(update.get("$set") or {})
        for doc in self.documents:
            if all(doc.get(k) == v for k, v in query.items()):
                doc.update(values)
                return dict(doc)
        if upsert:
            stored = dict(values)
            stored["_id"] = ObjectId()
            self.documents.append(stored)
            return dict(stored)
        return None

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.documents if _matches(doc, query))

    async def delete_one(self, query):
        before = len(self.documents)
        self.documents = [
            d for d in self.documents if not all(d.get(k) == v for k, v in query.items())
        ]
        return SimpleNamespace(deleted_count=before - len(self.documents))

    def find(self, query: dict, projection: dict | None = None):
        matched = [dict(d) for d in self.documents if _matches(d, query)]

        class _Cursor:
            def sort(self, *_a, **_k):
                return self

            def skip(self, n: int):
                nonlocal matched
                matched = matched[n:]
                return self

            def limit(self, n: int):
                nonlocal matched
                matched = matched[:n]
                return self

            async def to_list(self, length=None):
                return list(matched if length is None else matched[:length])

        return _Cursor()


class FakeUsers:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                return dict(doc)
        return None


class FakeEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=ObjectId())


class FakeSystemSettings:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    async def find_one(self, query):
        key = query.get("key")
        return self.documents.get(key)

    async def update_one(self, query, update, upsert=False):
        key = query.get("key")
        values = dict(update.get("$set") or {})
        if key in self.documents:
            self.documents[key].update(values)
        elif upsert:
            self.documents[key] = values
        return SimpleNamespace(matched_count=1, modified_count=1)


class FakeDb:
    def __init__(self, **collections) -> None:
        for name, collection in collections.items():
            setattr(self, name, collection)

    def __getitem__(self, key: str):
        return getattr(self, key)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
        backup_s3_bucket="",
        receipt_reminder_hours=24,
    )


def test_people_get_and_upsert_identity(tmp_path: Path) -> None:
    user_id = ObjectId()
    people = FakePeople()
    users = FakeUsers(
        [{"_id": user_id, "username": "ali", "full_name": "ALI", "role": "cashier"}]
    )
    db = FakeDb(
        people=people,
        users=users,
        operation_events=FakeEvents(),
        system_settings=FakeSystemSettings(),
    )
    app = create_app(settings=_settings(tmp_path), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "mgr",
        "role": "admin",
    }
    with TestClient(app) as client:
        created = client.put(
            "/v1/people/ALI",
            json={
                "name": "ALI",
                "active": True,
                "phone": "0711",
                "receives_receipt_requests": True,
                "user_id": str(user_id),
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["name"] == "ALI"
        assert body["phone"] == "0711"
        assert body["receives_receipt_requests"] is True
        assert body["user_id"] == str(user_id)

        got = client.get("/v1/people/ALI")
        assert got.status_code == 200
        assert got.json()["user_id"] == str(user_id)

        bad = client.put(
            "/v1/people/ALI",
            json={
                "name": "ALI",
                "active": True,
                "user_id": str(ObjectId()),
            },
        )
        assert bad.status_code == 422


def test_people_upsert_can_clear_user_and_disable_receives(tmp_path: Path) -> None:
    user_id = ObjectId()
    people = FakePeople(
        [
            {
                "_id": ObjectId(),
                "name": "BOB",
                "active": True,
                "phone": "1",
                "receives_receipt_requests": True,
                "user_id": user_id,
            }
        ]
    )
    users = FakeUsers(
        [{"_id": user_id, "username": "bob", "full_name": "BOB", "role": "cashier"}]
    )
    db = FakeDb(
        people=people,
        users=users,
        operation_events=FakeEvents(),
        system_settings=FakeSystemSettings(),
    )
    app = create_app(settings=_settings(tmp_path), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "mgr",
        "role": "accountant",
    }
    with TestClient(app) as client:
        updated = client.put(
            "/v1/people/BOB",
            json={
                "name": "BOB",
                "active": True,
                "phone": "",
                "receives_receipt_requests": False,
                "user_id": None,
            },
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["receives_receipt_requests"] is False
        assert body.get("user_id") in (None, "")
        assert people.documents[0]["receives_receipt_requests"] is False
