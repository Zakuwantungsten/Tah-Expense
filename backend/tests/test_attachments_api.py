"""Phase 5.1–5.2 — attachment list / download / upload / remove endpoints."""

from __future__ import annotations

from datetime import datetime
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
        if actual != expected:
            return False
    return True


class FakeOperationEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=ObjectId())


class FakeTransactions:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def find_one(
        self, query: dict, projection: dict | None = None
    ) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                result = dict(doc)
                if projection:
                    keep = {k for k, v in projection.items() if v}
                    if "_id" not in projection or projection.get("_id"):
                        keep.add("_id")
                    result = {k: result[k] for k in keep if k in result}
                return result
        return None

    async def update_one(self, query: dict, update: dict) -> SimpleNamespace:
        for doc in self.documents:
            if not _matches(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            if "$push" in update:
                for key, value in update["$push"].items():
                    doc.setdefault(key, []).append(value)
            if "$pull" in update:
                for key, criteria in update["$pull"].items():
                    items = list(doc.get(key) or [])
                    if isinstance(criteria, dict):
                        doc[key] = [
                            item
                            for item in items
                            if not all(
                                item.get(field) == expected
                                for field, expected in criteria.items()
                            )
                        ]
                    else:
                        doc[key] = [item for item in items if item != criteria]
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
    )


def _app(collection: FakeTransactions, tmp_path: Path):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=collection,
            operation_events=FakeOperationEvents(),
        )
    )
    return create_app(settings=settings(tmp_path), database=database)


def _user(*, role: str = "cashier") -> dict:
    return {"_id": ObjectId(), "username": "u1", "role": role}


def test_list_attachments_empty(tmp_path: Path) -> None:
    tx_id = ObjectId()
    collection = FakeTransactions([{"_id": tx_id, "attachments": []}])
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user()

    with TestClient(app) as client:
        response = client.get(f"/v1/transactions/{tx_id}/attachments")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_attachments_returns_meta(tmp_path: Path) -> None:
    tx_id = ObjectId()
    uploaded = datetime(2026, 4, 1, 12, 0, 0)
    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "attachments": [
                    {
                        "id": "abc123",
                        "filename": "receipt.pdf",
                        "stored_name": "abc123_receipt.pdf",
                        "size": 42,
                        "uploaded_at": uploaded,
                    }
                ],
            }
        ]
    )
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user(role="accountant")

    with TestClient(app) as client:
        response = client.get(f"/v1/transactions/{tx_id}/attachments")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "abc123"
    assert body["items"][0]["filename"] == "receipt.pdf"
    assert body["items"][0]["size"] == 42
    assert "2026-04-01" in body["items"][0]["uploaded_at"]


def test_list_attachments_missing_tx(tmp_path: Path) -> None:
    collection = FakeTransactions()
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user()

    with TestClient(app) as client:
        response = client.get(f"/v1/transactions/{ObjectId()}/attachments")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_download_attachment_file(tmp_path: Path) -> None:
    tx_id = ObjectId()
    att_id = "deadbeef"
    stored = f"{att_id}_receipt.pdf"
    tx_folder = tmp_path / str(tx_id)
    tx_folder.mkdir(parents=True)
    file_path = tx_folder / stored
    file_path.write_bytes(b"%PDF-1.4 test")

    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "attachments": [
                    {
                        "id": att_id,
                        "filename": "receipt.pdf",
                        "stored_name": stored,
                        "size": file_path.stat().st_size,
                    }
                ],
            }
        ]
    )
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user()

    with TestClient(app) as client:
        response = client.get(
            f"/v1/transactions/{tx_id}/attachments/{att_id}/file"
        )

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 test"
    assert "receipt.pdf" in response.headers.get("content-disposition", "")


def test_download_attachment_missing_file(tmp_path: Path) -> None:
    tx_id = ObjectId()
    att_id = "missing1"
    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "attachments": [
                    {
                        "id": att_id,
                        "filename": "gone.pdf",
                        "stored_name": f"{att_id}_gone.pdf",
                        "size": 1,
                    }
                ],
            }
        ]
    )
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user()

    with TestClient(app) as client:
        response = client.get(
            f"/v1/transactions/{tx_id}/attachments/{att_id}/file"
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_missing"


def test_download_attachment_unknown_id(tmp_path: Path) -> None:
    tx_id = ObjectId()
    collection = FakeTransactions([{"_id": tx_id, "attachments": []}])
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user()

    with TestClient(app) as client:
        response = client.get(
            f"/v1/transactions/{tx_id}/attachments/nope/file"
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "attachment_not_found"


def test_upload_attachment(tmp_path: Path) -> None:
    tx_id = ObjectId()
    collection = FakeTransactions([{"_id": tx_id, "attachments": []}])
    app = _app(collection, tmp_path)
    user = _user()
    app.dependency_overrides[current_user] = lambda: user

    with TestClient(app) as client:
        response = client.post(
            f"/v1/transactions/{tx_id}/attachments",
            files={"file": ("receipt.pdf", b"%PDF-upload", "application/pdf")},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "receipt.pdf"
    assert body["size"] == len(b"%PDF-upload")
    assert body["id"]
    assert body["stored_name"].startswith(body["id"])
    assert len(collection.documents[0]["attachments"]) == 1
    stored = tmp_path / str(tx_id) / body["stored_name"]
    assert stored.is_file()
    assert stored.read_bytes() == b"%PDF-upload"

    with TestClient(app) as client:
        downloaded = client.get(
            f"/v1/transactions/{tx_id}/attachments/{body['id']}/file"
        )
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-upload"


def test_upload_rejects_bad_extension(tmp_path: Path) -> None:
    tx_id = ObjectId()
    collection = FakeTransactions([{"_id": tx_id, "attachments": []}])
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user()

    with TestClient(app) as client:
        response = client.post(
            f"/v1/transactions/{tx_id}/attachments",
            files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_attachment"
    assert collection.documents[0]["attachments"] == []


def test_upload_forbidden_for_unknown_role(tmp_path: Path) -> None:
    tx_id = ObjectId()
    collection = FakeTransactions([{"_id": tx_id, "attachments": []}])
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user(role="viewer")

    with TestClient(app) as client:
        response = client.post(
            f"/v1/transactions/{tx_id}/attachments",
            files={"file": ("receipt.pdf", b"x", "application/pdf")},
        )

    assert response.status_code == 403


def test_remove_attachment(tmp_path: Path) -> None:
    tx_id = ObjectId()
    att_id = "toremove1"
    stored = f"{att_id}_scan.png"
    tx_folder = tmp_path / str(tx_id)
    tx_folder.mkdir(parents=True)
    file_path = tx_folder / stored
    file_path.write_bytes(b"PNGDATA")

    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "attachments": [
                    {
                        "id": att_id,
                        "filename": "scan.png",
                        "stored_name": stored,
                        "size": 7,
                    }
                ],
            }
        ]
    )
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user(role="admin")

    with TestClient(app) as client:
        response = client.delete(
            f"/v1/transactions/{tx_id}/attachments/{att_id}"
        )

    assert response.status_code == 204
    assert collection.documents[0]["attachments"] == []
    assert not file_path.exists()


def test_remove_attachment_not_found(tmp_path: Path) -> None:
    tx_id = ObjectId()
    collection = FakeTransactions([{"_id": tx_id, "attachments": []}])
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user()

    with TestClient(app) as client:
        response = client.delete(
            f"/v1/transactions/{tx_id}/attachments/missing"
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "attachment_not_found"


def test_cleanup_orphans_removes_extra_files(tmp_path: Path) -> None:
    tx_id = ObjectId()
    known = "keep_me.pdf"
    orphan = "orphan.bin"
    tx_folder = tmp_path / str(tx_id)
    tx_folder.mkdir(parents=True)
    (tx_folder / known).write_bytes(b"keep")
    (tx_folder / orphan).write_bytes(b"junk")
    staging = tmp_path / ".staging"
    staging.mkdir()
    stale = staging / "old.part"
    stale.write_bytes(b"stale")
    # Force stale mtime older than 24h.
    import os
    import time

    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    collection = FakeTransactions(
        [
            {
                "_id": tx_id,
                "attachments": [
                    {
                        "id": "k1",
                        "filename": "keep.pdf",
                        "stored_name": known,
                        "size": 4,
                    }
                ],
            }
        ]
    )
    app = _app(collection, tmp_path)
    app.dependency_overrides[current_user] = lambda: _user(role="admin")

    with TestClient(app) as client:
        dry = client.post(
            "/v1/transactions/attachments/cleanup-orphans",
            params={"dry_run": True},
        )
        assert dry.status_code == 200
        assert dry.json()["orphan_files_removed"] == 1
        assert dry.json()["staging_removed"] == 1
        assert (tx_folder / orphan).exists()
        assert stale.exists()

        live = client.post("/v1/transactions/attachments/cleanup-orphans")

    assert live.status_code == 200
    body = live.json()
    assert body["orphan_files_removed"] == 1
    assert body["staging_removed"] == 1
    assert (tx_folder / known).exists()
    assert not (tx_folder / orphan).exists()
    assert not stale.exists()


def test_cleanup_orphans_forbidden_for_cashier(tmp_path: Path) -> None:
    app = _app(FakeTransactions(), tmp_path)
    app.dependency_overrides[current_user] = lambda: _user(role="cashier")

    with TestClient(app) as client:
        response = client.post("/v1/transactions/attachments/cleanup-orphans")

    assert response.status_code == 403
