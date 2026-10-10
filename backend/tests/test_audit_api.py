from datetime import datetime, timezone
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.security import hash_password


class FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    def sort(self, *args, **kwargs):
        key = args[0][0] if args and isinstance(args[0], (list, tuple)) else args[0]
        reverse = True
        if len(args) > 1:
            reverse = args[1] < 0
        self._docs.sort(key=lambda d: d.get(key) or datetime.min, reverse=reverse)
        return self

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs)


class FakeOperationEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        stored = dict(document)
        stored["_id"] = ObjectId()
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def find(self, query=None):
        docs = list(self.documents)
        query = query or {}
        if "action" in query:
            action_q = query["action"]
            if isinstance(action_q, dict) and "$regex" in action_q:
                needle = str(action_q["$regex"]).lower()
                docs = [d for d in docs if needle in str(d.get("action", "")).lower()]
            else:
                docs = [d for d in docs if d.get("action") == action_q]
        if "outcome" in query:
            docs = [d for d in docs if d.get("outcome") == query["outcome"]]
        if "actor_username" in query:
            actor_q = query["actor_username"]
            if isinstance(actor_q, dict) and "$regex" in actor_q:
                needle = str(actor_q["$regex"]).lower()
                docs = [
                    d
                    for d in docs
                    if needle in str(d.get("actor_username", "")).lower()
                ]
        return FakeCursor(docs)


class FakeUsers:
    def __init__(self, users: list[dict]) -> None:
        self.users = users

    async def find_one(self, query):
        for user in self.users:
            if all(user.get(k) == v for k, v in query.items()):
                return user
        return None

    async def update_one(self, *args, **kwargs):
        return SimpleNamespace(matched_count=1)


class FakeSessions:
    async def insert_one(self, document):
        return SimpleNamespace(inserted_id=document.get("_id") or ObjectId())


class FakeLockouts:
    async def find_one(self, query):
        return None

    async def update_one(self, *args, **kwargs):
        return SimpleNamespace(matched_count=1)

    async def delete_one(self, *args, **kwargs):
        return SimpleNamespace(deleted_count=1)


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


class FakeSettingsFind:
    async def to_list(self, length=None):
        return []


def _app(collection: FakeOperationEvents, users: list[dict] | None = None):
    db = SimpleNamespace(
        operation_events=collection,
        users=FakeUsers(users or []),
        auth_sessions=FakeSessions(),
        auth_lockouts=FakeLockouts(),
        system_settings=SimpleNamespace(find=lambda *a, **k: FakeSettingsFind()),
    )
    database = SimpleNamespace(db=db)
    return create_app(settings=settings(), database=database)


def test_create_audit_event_inserts_document() -> None:
    collection = FakeOperationEvents()
    app = _app(collection)
    user_id = ObjectId()
    app.dependency_overrides[current_user] = lambda: {
        "_id": user_id,
        "username": "ada",
        "role": "accountant",
    }

    with TestClient(app) as client:
        response = client.post(
            "/v1/audit/events",
            json={
                "action": "transaction.approve",
                "entity_type": "transaction",
                "entity_ids": [str(ObjectId())],
                "outcome": "ok",
                "details": {"count": 1},
                "app_version": "1.2.3",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert len(collection.documents) == 1
    doc = collection.documents[0]
    assert doc["action"] == "transaction.approve"
    assert doc["actor_id"] == str(user_id)
    assert doc["actor_username"] == "ada"
    assert doc["actor_role"] == "accountant"
    assert doc["client"] == "desktop"
    assert doc["app_version"] == "1.2.3"
    assert doc["details"] == {"count": 1}
    assert doc["request_id"]
    assert doc["ts"] is not None


def test_create_audit_event_keeps_explicit_actor() -> None:
    collection = FakeOperationEvents()
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "server-user",
        "role": "admin",
    }
    actor = str(ObjectId())

    with TestClient(app) as client:
        response = client.post(
            "/v1/audit/events",
            json={
                "action": "transaction.save",
                "actor_id": actor,
                "actor_username": "cashier1",
                "actor_role": "cashier",
            },
        )

    assert response.status_code == 201
    doc = collection.documents[0]
    assert doc["actor_id"] == actor
    assert doc["actor_username"] == "cashier1"
    assert doc["actor_role"] == "cashier"


def test_create_audit_event_requires_authentication() -> None:
    collection = FakeOperationEvents()
    app = _app(collection)

    with TestClient(app) as client:
        response = client.post("/v1/audit/events", json={"action": "noop"})

    assert response.status_code == 401
    assert collection.documents == []


def test_create_audit_event_requires_action() -> None:
    collection = FakeOperationEvents()
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "ada",
        "role": "admin",
    }

    with TestClient(app) as client:
        response = client.post("/v1/audit/events", json={})

    assert response.status_code == 422
    assert collection.documents == []


def test_list_audit_events_admin_only() -> None:
    collection = FakeOperationEvents()
    collection.documents.append(
        {
            "_id": ObjectId(),
            "ts": datetime(2026, 7, 1, tzinfo=timezone.utc),
            "action": "auth.login",
            "actor_username": "admin",
            "actor_role": "admin",
            "outcome": "ok",
            "entity_ids": [],
            "details": {},
        }
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "acct",
        "role": "accountant",
    }
    with TestClient(app) as client:
        denied = client.get("/v1/audit/events")
    assert denied.status_code == 403

    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "admin",
        "role": "admin",
    }
    with TestClient(app) as client:
        allowed = client.get("/v1/audit/events", params={"action": "auth"})
    assert allowed.status_code == 200
    body = allowed.json()
    assert len(body) == 1
    assert body[0]["action"] == "auth.login"


def test_login_writes_audit_success_and_failure() -> None:
    collection = FakeOperationEvents()
    user = {
        "_id": ObjectId(),
        "username": "cashier1",
        "active": True,
        "role": "cashier",
        "password_hash": hash_password("GoodPass12"),
        "full_name": "Cashier One",
    }
    app = _app(collection, users=[user])
    with TestClient(app) as client:
        bad = client.post(
            "/v1/auth/login",
            json={"username": "cashier1", "password": "wrong"},
        )
        assert bad.status_code == 401
        ok = client.post(
            "/v1/auth/login",
            json={"username": "cashier1", "password": "GoodPass12"},
        )
        assert ok.status_code == 200

    actions = [(d["action"], d["outcome"]) for d in collection.documents]
    assert ("auth.login", "fail") in actions
    assert ("auth.login", "ok") in actions
