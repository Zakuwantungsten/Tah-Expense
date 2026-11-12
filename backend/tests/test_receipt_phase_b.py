"""Phase B — parked inbox, approve permanent, reject → quarantine."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.receipt_ops import (
    INBOX_APPROVED,
    INBOX_MATCHED,
    INBOX_REJECTED,
    INBOX_UNMATCHED,
    approve_receipt_files,
    ensure_receipt_workspace,
    list_inbox_files,
    receipt_compliance_summary,
    reject_receipt_files,
    store_receipt_file_bytes,
)


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class FakeColl:
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

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=document.get("_id"))

    async def update_one(
        self, query: dict, update: dict, *, upsert: bool = False
    ) -> SimpleNamespace:
        for doc in self.documents:
            if not _matches(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.documents if _matches(doc, query))

    def find(self, query: dict, projection: dict | None = None):
        matched = [dict(doc) for doc in self.documents if _matches(doc, query)]
        if projection:
            keep = {k for k, v in projection.items() if v}
            keep.add("_id")
            matched = [{k: d[k] for k in keep if k in d} for d in matched]

        class _Cursor:
            def sort(self, *_a, **_k):
                return self

            def limit(self, n: int):
                nonlocal matched
                matched = matched[:n]
                return self

            async def to_list(self, length: int | None = None):
                if length is None:
                    return list(matched)
                return list(matched)[:length]

        return _Cursor()


class FakeEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=ObjectId())


def _db(*, files: list[dict] | None = None, requests: list[dict] | None = None):
    return SimpleNamespace(
        receipt_files=FakeColl(files),
        receipt_requests=FakeColl(requests),
        transactions=FakeColl(),
        operation_events=FakeEvents(),
        system_settings=FakeColl(),
        users=FakeColl(),
        auth_sessions=FakeColl(),
        people=FakeColl(),
        categories=FakeColl(),
    )


def test_ensure_receipt_workspace_creates_folders(tmp_path: Path) -> None:
    paths = ensure_receipt_workspace(tmp_path)
    assert paths["files"].is_dir()
    assert paths["parked"].is_dir()
    assert paths["quarantine"].is_dir()
    assert paths["approved"].is_dir()


async def test_list_inbox_status_filters() -> None:
    db = _db(
        files=[
            {"_id": "u1", "inbox_status": INBOX_UNMATCHED, "uploaded_at": datetime(2026, 1, 1)},
            {"_id": "m1", "inbox_status": INBOX_MATCHED, "uploaded_at": datetime(2026, 1, 2)},
            {"_id": "a1", "inbox_status": INBOX_APPROVED, "uploaded_at": datetime(2026, 1, 3)},
            {"_id": "r1", "inbox_status": INBOX_REJECTED, "uploaded_at": datetime(2026, 1, 4)},
        ]
    )
    parked = await list_inbox_files(db, status="parked")
    assert {row["id"] for row in parked} == {"u1", "m1"}
    approved = await list_inbox_files(db, status="approved")
    assert [row["id"] for row in approved] == ["a1"]
    rejected = await list_inbox_files(db, status="rejected")
    assert [row["id"] for row in rejected] == ["r1"]
    all_items = await list_inbox_files(db, status="all")
    assert len(all_items) == 4


async def test_store_upload_is_parked_unmatched(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="fuel.jpg",
        content=b"\xff\xd8\xff\xe0jpeg-bytes",
        source="mobile",
    )
    doc = await db.receipt_files.find_one({"_id": meta["id"]})
    assert doc["inbox_status"] == INBOX_UNMATCHED
    assert (tmp_path / "files" / Path(meta["stored_path"]).name).is_file()
    parked = await list_inbox_files(db, status="parked")
    assert meta["id"] in {row["id"] for row in parked}


async def test_approve_marks_permanent(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db, tmp_path, filename="ok.pdf", content=b"%PDF-1.4", source="mobile"
    )
    result = await approve_receipt_files(
        db, tmp_path, file_ids=[meta["id"]], actor_id=ObjectId()
    )
    assert result["updated"] == 1
    doc = await db.receipt_files.find_one({"_id": meta["id"]})
    assert doc["inbox_status"] == INBOX_APPROVED
    assert doc.get("approved_at") is not None
    # Canonical bytes stay under files/
    stored = Path(str(doc["stored_path"]))
    root_files = ensure_receipt_workspace(tmp_path)["files"]
    assert (root_files / stored.name).is_file()


async def test_reject_copies_quarantine_keeps_canonical(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db, tmp_path, filename="bad.jpg", content=b"\xff\xd8\xff\xe0bad-img", source="desktop"
    )
    result = await reject_receipt_files(
        db,
        tmp_path,
        file_ids=[meta["id"]],
        reason="Blurry photo",
        actor_id=ObjectId(),
    )
    assert result["updated"] == 1
    doc = await db.receipt_files.find_one({"_id": meta["id"]})
    assert doc["inbox_status"] == INBOX_REJECTED
    assert doc["rejection_reason"] == "Blurry photo"
    workspace = ensure_receipt_workspace(tmp_path)
    stored_name = Path(str(doc["stored_path"])).name
    assert (workspace["files"] / stored_name).is_file()
    assert (workspace["quarantine"] / stored_name).is_file()


async def test_reject_requires_reason(tmp_path: Path) -> None:
    db = _db(
        files=[{"_id": "f1", "inbox_status": INBOX_UNMATCHED, "stored_path": "files/x.jpg"}]
    )
    with pytest.raises(ValueError, match="reason"):
        await reject_receipt_files(
            db, tmp_path, file_ids=["f1"], reason="no", actor_id=None
        )


async def test_cannot_approve_rejected(tmp_path: Path) -> None:
    db = _db(
        files=[
            {
                "_id": "f1",
                "inbox_status": INBOX_REJECTED,
                "stored_path": "files/x.jpg",
            }
        ]
    )
    with pytest.raises(ValueError, match="rejected"):
        await approve_receipt_files(db, tmp_path, file_ids=["f1"], actor_id=None)


async def test_cannot_reject_approved(tmp_path: Path) -> None:
    db = _db(
        files=[
            {
                "_id": "f1",
                "inbox_status": INBOX_APPROVED,
                "stored_path": "files/x.jpg",
            }
        ]
    )
    with pytest.raises(ValueError, match="approved"):
        await reject_receipt_files(
            db, tmp_path, file_ids=["f1"], reason="too late", actor_id=None
        )


async def test_compliance_parked_approved_rejected() -> None:
    db = _db(
        files=[
            {"_id": "u1", "inbox_status": INBOX_UNMATCHED},
            {"_id": "m1", "inbox_status": INBOX_MATCHED},
            {"_id": "a1", "inbox_status": INBOX_APPROVED},
            {"_id": "r1", "inbox_status": INBOX_REJECTED},
        ]
    )
    summary = await receipt_compliance_summary(db)
    assert summary["parked_uploads"] == 2
    assert summary["unmatched_uploads"] == 1
    assert summary["approved_files"] == 1
    assert summary["rejected_files"] == 1


def test_approve_api(tmp_path: Path) -> None:
    file_id = "api_file_1"
    files_dir = ensure_receipt_workspace(tmp_path)["files"]
    stored_name = f"{file_id}_scan.jpg"
    (files_dir / stored_name).write_bytes(b"img")
    db = _db(
        files=[
            {
                "_id": file_id,
                "original_filename": "scan.jpg",
                "inbox_status": INBOX_UNMATCHED,
                "stored_path": f"files/{stored_name}",
                "uploaded_at": datetime(2026, 3, 1),
                "size": 3,
                "source": "mobile",
            }
        ]
    )
    settings = Settings(
        mongodb_uri="mongodb://127.0.0.1:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
        backup_s3_bucket="",
    )
    app = create_app(settings=settings, database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "acct",
        "role": "accountant",
    }
    with TestClient(app) as client:
        inbox = client.get("/v1/receipts/inbox", params={"status": "parked"})
        assert inbox.status_code == 200
        assert inbox.json()["total"] == 1
        approve = client.post(
            "/v1/receipts/files/approve", json={"file_ids": [file_id]}
        )
        assert approve.status_code == 200
        assert approve.json()["updated"] == 1
    assert db.receipt_files.documents[0]["inbox_status"] == INBOX_APPROVED
    assert any(
        e.get("action") == "receipt.approve" for e in db.operation_events.documents
    )


def test_reject_api(tmp_path: Path) -> None:
    file_id = "api_file_2"
    files_dir = ensure_receipt_workspace(tmp_path)["files"]
    stored_name = f"{file_id}_scan.jpg"
    (files_dir / stored_name).write_bytes(b"img")
    db = _db(
        files=[
            {
                "_id": file_id,
                "original_filename": "scan.jpg",
                "inbox_status": INBOX_MATCHED,
                "stored_path": f"files/{stored_name}",
                "uploaded_at": datetime(2026, 3, 1),
                "size": 3,
                "source": "mobile",
            }
        ]
    )
    settings = Settings(
        mongodb_uri="mongodb://127.0.0.1:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
        backup_s3_bucket="",
    )
    app = create_app(settings=settings, database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "acct",
        "role": "accountant",
    }
    with TestClient(app) as client:
        reject = client.post(
            "/v1/receipts/files/reject",
            json={"file_ids": [file_id], "reason": "Wrong trip"},
        )
        assert reject.status_code == 200
        assert reject.json()["updated"] == 1
    assert db.receipt_files.documents[0]["inbox_status"] == INBOX_REJECTED
    assert any(
        e.get("action") == "receipt.reject" for e in db.operation_events.documents
    )
    assert (ensure_receipt_workspace(tmp_path)["quarantine"] / stored_name).is_file()
