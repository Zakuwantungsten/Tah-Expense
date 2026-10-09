from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


class FakeSystemSettings:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = {doc["key"]: dict(doc) for doc in (documents or [])}
        self.updates: list[tuple[dict, dict, bool]] = []

    def find(self, _query: dict) -> "FakeCursor":
        return FakeCursor(list(self.documents.values()))

    async def find_one(self, query: dict) -> dict | None:
        key = query.get("key")
        doc = self.documents.get(key)
        return dict(doc) if doc else None

    async def update_one(self, query: dict, update: dict, *, upsert: bool = False) -> None:
        self.updates.append((dict(query), dict(update), upsert))
        key = query["key"]
        values = dict(update.get("$set") or {})
        if key in self.documents:
            self.documents[key].update(values)
        elif upsert:
            self.documents[key] = values


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    async def to_list(self, length: int | None = None) -> list[dict]:
        if length is None:
            return list(self.documents)
        return list(self.documents)[:length]


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(collection: FakeSystemSettings):
    database = SimpleNamespace(db=SimpleNamespace(system_settings=collection))
    return create_app(settings=settings(), database=database)


def test_get_all_settings_merges_defaults_and_stored() -> None:
    collection = FakeSystemSettings(
        [{"key": "restrict_items", "value": True}, {"key": "custom_flag", "value": 3}]
    )
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {"role": "cashier"}

    with TestClient(app) as client:
        response = client.get("/v1/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["default_currency"] == "TZS"
    assert body["restrict_items"] is True
    assert body["restrict_trucks"] is True
    assert body["custom_flag"] == 3


def test_get_setting_returns_default_when_missing() -> None:
    collection = FakeSystemSettings()
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {"role": "cashier"}

    with TestClient(app) as client:
        response = client.get("/v1/settings/duplicate_check_days")

    assert response.status_code == 200
    assert response.json() == {"key": "duplicate_check_days", "value": 5}


def test_get_setting_returns_stored_value() -> None:
    collection = FakeSystemSettings([{"key": "default_currency", "value": "USD"}])
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {"role": "admin"}

    with TestClient(app) as client:
        response = client.get("/v1/settings/default_currency")

    assert response.status_code == 200
    assert response.json() == {"key": "default_currency", "value": "USD"}


def test_get_unknown_setting_returns_404() -> None:
    collection = FakeSystemSettings()
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {"role": "cashier"}

    with TestClient(app) as client:
        response = client.get("/v1/settings/not_a_real_setting")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "setting_not_found"


@pytest.mark.parametrize("role", ["admin", "accountant", "cashier"])
def test_put_setting_upserts_for_authenticated(role: str) -> None:
    collection = FakeSystemSettings()
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {"role": role}

    with TestClient(app) as client:
        response = client.put(
            "/v1/settings/allowed_truck_labels",
            json={"value": ["YARD", "GARAGE", "PORT"]},
        )

    assert response.status_code == 200
    assert response.json() == {
        "key": "allowed_truck_labels",
        "value": ["YARD", "GARAGE", "PORT"],
    }
    assert collection.documents["allowed_truck_labels"]["value"] == [
        "YARD",
        "GARAGE",
        "PORT",
    ]
    assert collection.updates == [
        (
            {"key": "allowed_truck_labels"},
            {"$set": {"key": "allowed_truck_labels", "value": ["YARD", "GARAGE", "PORT"]}},
            True,
        )
    ]


def test_put_setting_rejects_invalid_key() -> None:
    collection = FakeSystemSettings()
    app = _app(collection)
    app.dependency_overrides[current_user] = lambda: {"role": "admin"}

    with TestClient(app) as client:
        response = client.put("/v1/settings/Bad-Key!", json={"value": 1})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_setting_key"


def test_settings_require_authentication() -> None:
    collection = FakeSystemSettings()
    app = _app(collection)

    with TestClient(app) as client:
        response = client.get("/v1/settings")

    assert response.status_code == 401
