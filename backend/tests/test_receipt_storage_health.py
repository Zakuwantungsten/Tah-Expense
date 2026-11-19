"""Receipt disk health — mount / marker detection + API surface."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.receipt_storage_health import (
    VOLUME_MARKER,
    VOLUME_TOKEN_KEY,
    evaluate_probe_health,
    refresh_storage_health,
)


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


class FakeCounters:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    async def find_one(self, query: dict) -> dict | None:
        doc = self.documents.get(query.get("_id"))
        return dict(doc) if doc else None

    async def update_one(self, query: dict, update: dict, *, upsert: bool = False) -> None:
        key = query["_id"]
        values = dict(update.get("$set") or {})
        if key in self.documents:
            self.documents[key].update(values)
        elif upsert:
            self.documents[key] = {"_id": key, **values}


class FakeTransactions:
    async def count_documents(self, _query: dict) -> int:
        return 0


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path / "attachments",
        backup_s3_bucket="",
    )


def _app(tmp_path: Path, *, attachments: Path | None = None):
    root = attachments or (tmp_path / "attachments")
    root.mkdir(parents=True, exist_ok=True)
    settings_col = FakeSystemSettings()
    counters = FakeCounters()
    database = SimpleNamespace(
        db=SimpleNamespace(
            system_settings=settings_col,
            system_counters=counters,
            transactions=FakeTransactions(),
            operation_events=SimpleNamespace(
                insert_one=lambda _doc: SimpleNamespace(inserted_id=ObjectId())
            ),
        )
    )
    app = create_app(settings=_settings(tmp_path), database=database)
    # Point env path at the prepared folder.
    app.state.settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=root,
        backup_s3_bucket="",
    )
    return app, settings_col, counters, root


def _user(*, role: str = "cashier") -> dict:
    return {"_id": ObjectId(), "username": "u", "role": role}


def test_evaluate_probe_detects_missing_marker(tmp_path: Path) -> None:
    path = tmp_path / "disk"
    path.mkdir()
    probe = {
        "exists": True,
        "is_dir": True,
        "write_ok": True,
        "mount_hint": str(path),
    }
    healthy, reason = evaluate_probe_health(path, probe, expected_token="abc")
    assert healthy is False
    assert reason == "volume_marker_missing"


def test_refresh_bootstraps_marker_when_healthy(tmp_path: Path) -> None:
    import asyncio

    app, settings_col, counters, root = _app(tmp_path)
    db = SimpleNamespace(
        system_settings=settings_col,
        system_counters=counters,
    )

    health = asyncio.run(refresh_storage_health(app.state.settings, db, force=True))
    assert health["healthy"] is True
    assert (root / VOLUME_MARKER).is_file()
    assert VOLUME_TOKEN_KEY in settings_col.documents
    assert counters.documents["receipt_storage_health"]["healthy"] is True


def test_health_endpoint_allows_cashier(tmp_path: Path) -> None:
    app, _, _, _ = _app(tmp_path)
    app.dependency_overrides[current_user] = lambda: _user(role="cashier")

    with TestClient(app) as client:
        response = client.get("/v1/receipts/storage/health")

    assert response.status_code == 200
    body = response.json()
    assert body["healthy"] is True
    assert body["degraded"] is False


def test_mobile_health_endpoint(tmp_path: Path) -> None:
    app, _, _, _ = _app(tmp_path)
    app.dependency_overrides[current_user] = lambda: _user(role="cashier")

    with TestClient(app) as client:
        response = client.get("/v1/mobile/storage-health")

    assert response.status_code == 200
    assert response.json()["healthy"] is True


def test_upload_blocked_when_path_missing(tmp_path: Path) -> None:
    missing = tmp_path / "gone-disk"
    app, _, counters, _ = _app(tmp_path, attachments=missing)
    # Remove after app pointed at it.
    if missing.exists():
        missing.rmdir()
    app.dependency_overrides[current_user] = lambda: _user(role="cashier")

    with TestClient(app) as client:
        health = client.get("/v1/receipts/storage/health")
        assert health.status_code == 200
        assert health.json()["healthy"] is False
        response = client.post(
            "/v1/receipts/files/upload",
            files=[("files", ("a.jpg", b"not-a-real-jpeg", "image/jpeg"))],
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "receipt_storage_unavailable"
    assert counters.documents["receipt_storage_health"]["healthy"] is False


def test_notification_counts_include_storage_flag(tmp_path: Path) -> None:
    missing = tmp_path / "missing-attachments"
    app, _, counters, _ = _app(tmp_path, attachments=missing)
    if missing.exists():
        missing.rmdir()
    counters.documents["badges"] = {
        "_id": "badges",
        "verify": 0,
        "edited": 0,
        "drafts": 0,
        "parked": 0,
        "deletion_requests": 0,
        "restore_requests": 0,
    }
    app.dependency_overrides[current_user] = lambda: _user(role="accountant")

    with TestClient(app) as client:
        # Lifespan probe marks the missing path unhealthy.
        response = client.get("/v1/accountant/notification-counts")

    assert response.status_code == 200
    assert response.json()["receipt_storage_down"] == 1
    assert counters.documents["receipt_storage_health"]["healthy"] is False
