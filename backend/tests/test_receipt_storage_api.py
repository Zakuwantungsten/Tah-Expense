"""Receipt disk status / path configuration API."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


class FakeSystemSettings:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = {doc["key"]: dict(doc) for doc in (documents or [])}

    async def find_one(self, query: dict) -> dict | None:
        doc = self.documents.get(query.get("key"))
        return dict(doc) if doc else None

    async def update_one(self, query: dict, update: dict, *, upsert: bool = False) -> None:
        key = query["key"]
        values = dict(update.get("$set") or {})
        if key in self.documents:
            self.documents[key].update(values)
        elif upsert:
            self.documents[key] = values

    async def delete_one(self, query: dict) -> SimpleNamespace:
        key = query.get("key")
        existed = key in self.documents
        self.documents.pop(key, None)
        return SimpleNamespace(deleted_count=1 if existed else 0)


class FakeTransactions:
    async def count_documents(self, _query: dict) -> int:
        return 2


class FakeOperationEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=ObjectId())


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path / "env-attachments",
        backup_s3_bucket="",
    )


def _app(tmp_path: Path, collection: FakeSystemSettings | None = None):
    settings_col = collection or FakeSystemSettings()
    database = SimpleNamespace(
        db=SimpleNamespace(
            system_settings=settings_col,
            transactions=FakeTransactions(),
            operation_events=FakeOperationEvents(),
        )
    )
    return create_app(settings=_settings(tmp_path), database=database), settings_col


def _user(*, role: str = "admin") -> dict:
    return {"_id": ObjectId(), "username": "ops", "role": role}


def test_storage_status_uses_env_path(tmp_path: Path) -> None:
    env_dir = tmp_path / "env-attachments"
    env_dir.mkdir()
    app, _ = _app(tmp_path)
    app.dependency_overrides[current_user] = lambda: _user(role="accountant")

    with TestClient(app) as client:
        response = client.get("/v1/receipts/storage")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "env"
    assert Path(body["effective_path"]) == env_dir
    assert body["probe"]["exists"] is True
    assert body["pending_s3_rows"] == 2
    assert isinstance(body["candidates"], list)
    assert body["candidates"]


def test_cashier_cannot_view_storage(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    app.dependency_overrides[current_user] = lambda: _user(role="cashier")

    with TestClient(app) as client:
        response = client.get("/v1/receipts/storage")

    assert response.status_code == 403


def test_put_storage_sets_override_and_creates_dir(tmp_path: Path) -> None:
    target = tmp_path / "chosen-disk" / "tahmeed-receipts"
    app, settings_col = _app(tmp_path)
    app.dependency_overrides[current_user] = lambda: _user(role="admin")

    with TestClient(app) as client:
        response = client.put(
            "/v1/receipts/storage",
            json={"path": str(target), "create": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "override"
    assert Path(body["effective_path"]) == target
    assert body["probe"]["write_ok"] is True
    assert target.is_dir()
    assert settings_col.documents["attachments_directory"]["value"] == str(target)


def test_probe_rejects_relative_path(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    app.dependency_overrides[current_user] = lambda: _user()

    with TestClient(app) as client:
        response = client.post(
            "/v1/receipts/storage/probe",
            json={"path": "relative/path"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_path"


def test_clear_override_returns_to_env(tmp_path: Path) -> None:
    env_dir = tmp_path / "env-attachments"
    env_dir.mkdir()
    override = tmp_path / "override"
    override.mkdir()
    app, settings_col = _app(
        tmp_path,
        FakeSystemSettings([{"key": "attachments_directory", "value": str(override)}]),
    )
    app.dependency_overrides[current_user] = lambda: _user(role="accountant")

    with TestClient(app) as client:
        before = client.get("/v1/receipts/storage")
        assert before.json()["source"] == "override"
        cleared = client.put(
            "/v1/receipts/storage",
            json={"path": str(override), "clear_override": True},
        )

    assert cleared.status_code == 200
    assert cleared.json()["source"] == "env"
    assert "attachments_directory" not in settings_col.documents


def test_put_rejects_unwritable_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "nope" / "missing"
    app, _ = _app(tmp_path)
    app.dependency_overrides[current_user] = lambda: _user()

    with TestClient(app) as client:
        response = client.put(
            "/v1/receipts/storage",
            json={"path": str(missing), "create": False},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "path_not_writable"
