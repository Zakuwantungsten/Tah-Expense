from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


class FakeOperationEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        stored = dict(document)
        stored["_id"] = ObjectId()
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(collection: FakeOperationEvents):
    database = SimpleNamespace(db=SimpleNamespace(operation_events=collection))
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
