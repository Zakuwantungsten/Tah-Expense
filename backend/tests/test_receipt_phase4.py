"""Phase 4 — evidence pack ZIP + export audit + prune leaves receipts alone."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.cli.backup import retention_set
from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.receipt_backup import receipt_object_key
from app.receipt_evidence import build_evidence_pack, record_evidence_export


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$or":
            return any(_matches(doc, clause) for clause in expected)
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$gte" in expected or "$lte" in expected:
                if actual is None:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
                    return False
                continue
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
        doc = dict(document)
        if "_id" not in doc:
            doc["_id"] = ObjectId()
        self.documents.append(doc)
        document["_id"] = doc["_id"]
        return SimpleNamespace(inserted_id=doc["_id"])

    async def update_one(self, query: dict, update: dict, **_kwargs) -> SimpleNamespace:
        for doc in self.documents:
            if not _matches(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

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


def _settings(tmp_path: Path, **extra) -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
        backup_s3_bucket=extra.get("backup_s3_bucket", ""),
        backup_s3_prefix="tahmeed-expense/database-backups",
        receipt_s3_prefix="receipts",
        backup_keep_count=1,
    )


def _db(*, transactions=None, requests=None, exports=None):
    return SimpleNamespace(
        transactions=FakeColl(transactions),
        receipt_requests=FakeColl(requests),
        receipt_exports=FakeColl(exports),
        receipt_files=FakeColl(),
        operation_events=FakeEvents(),
        system_settings=FakeColl(),
        backup_jobs=FakeColl(),
        categories=FakeColl(),
        people=FakeColl(),
        users=FakeColl(),
        auth_sessions=FakeColl(),
    )


async def test_build_evidence_pack_folders_index_and_readme(tmp_path: Path) -> None:
    tx_id = ObjectId()
    file_id = "fileabc"
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    content = b"receipt-bytes"
    disk = files_dir / f"{file_id}_shot.jpg"
    disk.write_bytes(content)

    db = _db(
        transactions=[
            {
                "_id": tx_id,
                "date": datetime(2026, 3, 2, 10, 0, 0),
                "amount": 45000,
                "currency": "TZS",
                "item": "LATRA",
                "payee": "VENDOR",
                "ownership": "ALI",
                "approver": "BOSS",
                "receipt_status": "received",
                "attachments": [
                    {
                        "id": file_id,
                        "filename": "shot.jpg",
                        "stored_path": f"files/{disk.name}",
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                ],
            },
            {
                "_id": ObjectId(),
                "date": datetime(2026, 3, 3, 10, 0, 0),
                "amount": 1000,
                "currency": "TZS",
                "item": "LATRA",
                "payee": "OTHER",
                "ownership": "BETA",
                "receipt_status": "pending",
                "attachments": [],
            },
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_id,
                "status": "received",
            }
        ],
    )
    payload, summary = await build_evidence_pack(
        db,
        tmp_path,
        date_from=datetime(2026, 3, 1),
        date_to=datetime(2026, 3, 31),
    )
    assert summary["row_count"] == 2
    assert summary["with_file"] == 1
    assert summary["without_file"] == 1
    assert hashlib.sha256(payload).hexdigest() == summary["sha256"]

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = set(zf.namelist())
        assert "README.txt" in names
        assert "index.csv" in names
        packed = [n for n in names if n.startswith("2026/03/LATRA/")]
        assert len(packed) == 1
        assert packed[0].endswith(".jpg")
        assert zf.read(packed[0]) == content
        index = list(csv.DictReader(io.StringIO(zf.read("index.csv").decode())))
        assert len(index) == 2
        by_tx = {row["transaction_id"]: row for row in index}
        assert by_tx[str(tx_id)]["row_has_no_file"] == "no"
        assert by_tx[str(tx_id)]["pack_path"] == packed[0]
        gap = [row for row in index if row["transaction_id"] != str(tx_id)][0]
        assert gap["row_has_no_file"] == "yes"
        assert gap["pack_path"] == ""


async def test_record_evidence_export_stores_sha_and_range() -> None:
    db = _db()
    summary = {
        "sha256": "abc",
        "size": 12,
        "row_count": 3,
        "with_file": 1,
        "without_file": 2,
        "waived": 0,
        "files_in_zip": 1,
        "date_from": datetime(2026, 1, 1),
        "date_to": datetime(2026, 1, 31),
        "item": "LATRA",
        "payee": "",
        "ownership": "ALI",
        "filename": "pack.zip",
    }
    export_id = await record_evidence_export(
        db, summary=summary, actor={"_id": ObjectId(), "username": "acct", "role": "accountant"}
    )
    doc = await db.receipt_exports.find_one({"_id": export_id})
    assert doc["sha256"] == "abc"
    assert doc["ownership"] == "ALI"
    assert doc["created_by_username"] == "acct"


def test_receipt_keys_are_outside_backup_retention_prefix(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    key = receipt_object_key(settings, "file-1")
    assert key.startswith("receipts/")
    assert not key.startswith(settings.backup_s3_prefix)
    # Prune only keeps newest N backup_jobs — receipt objects are never in that set.
    jobs = [
        {"_id": "a", "object_key": f"{settings.backup_s3_prefix}/old.dump"},
        {"_id": "b", "object_key": f"{settings.backup_s3_prefix}/new.dump"},
    ]
    keep = retention_set(jobs, keep_count=1)
    obsolete = [job for job in jobs if job["_id"] not in keep]
    assert all(not job["object_key"].startswith("receipts/") for job in obsolete)
    assert all(job["object_key"].startswith(settings.backup_s3_prefix) for job in obsolete)


def test_evidence_pack_api_returns_zip_and_audit(tmp_path: Path) -> None:
    tx_id = ObjectId()
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    disk = files_dir / "x_shot.jpg"
    disk.write_bytes(b"jpg")
    db = _db(
        transactions=[
            {
                "_id": tx_id,
                "date": datetime(2026, 4, 1, 8, 0, 0),
                "amount": 10,
                "currency": "TZS",
                "item": "PARKING",
                "payee": "LOT",
                "ownership": "ALI",
                "receipt_status": "received",
                "attachments": [
                    {
                        "id": "x",
                        "filename": "shot.jpg",
                        "stored_path": f"files/{disk.name}",
                        "sha256": hashlib.sha256(b"jpg").hexdigest(),
                        "size": 3,
                    }
                ],
            }
        ]
    )
    app = create_app(
        settings=_settings(tmp_path),
        database=SimpleNamespace(db=db),
    )
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "acct",
        "role": "accountant",
    }
    with TestClient(app) as client:
        response = client.post(
            "/v1/receipts/evidence-pack",
            json={
                "date_from": "2026-04-01",
                "date_to": "2026-04-30",
                "ownership": "ALI",
            },
        )
        listed = client.get("/v1/receipts/evidence-exports")

    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cash",
        "role": "cashier",
    }
    with TestClient(app) as client:
        cashier = client.post(
            "/v1/receipts/evidence-pack",
            json={"date_from": "2026-04-01", "date_to": "2026-04-30"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers.get("x-pack-sha256")
    assert response.headers.get("x-export-id")
    assert zipfile.is_zipfile(io.BytesIO(response.content))
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1
    assert listed.json()["items"][0]["sha256"] == response.headers["x-pack-sha256"]
    assert cashier.status_code == 403
