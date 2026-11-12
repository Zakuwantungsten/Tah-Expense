"""Phase 0 — S3 second copy of receipt bytes after the local file is in place."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.attachment_ops import add_attachment_bytes
from app.config import Settings
from app.receipt_backup import (
    mirror_attachment,
    receipt_object_key,
    retry_pending_receipt_backups,
)


def _settings(tmp_path: Path, **extra: object) -> Settings:
    values: dict[str, object] = {
        "mongodb_uri": "mongodb://localhost:27017",
        "jwt_secret": "a-test-secret-that-is-definitely-32-characters",
        "attachments_directory": tmp_path,
        "backup_directory": tmp_path / "backups",
        "backup_lock_file": tmp_path / "backup.lock",
        "backup_s3_bucket": "receipts-bucket",
        "backup_s3_prefix": "tahmeed-expense/database-backups",
        "receipt_s3_prefix": "receipts",
    }
    values.update(extra)
    return Settings(**values)


class _Cursor:
    def __init__(self, documents: list[dict]) -> None:
        self._documents = documents

    def __aiter__(self):
        async def iterate():
            for document in self._documents:
                yield document

        return iterate()


class _Transactions:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def update_one(self, query: dict, update: dict) -> SimpleNamespace:
        attachment_id = query.get("attachments.id")
        for doc in self.documents:
            if doc.get("_id") != query.get("_id"):
                continue
            if attachment_id is not None:
                for item in doc.get("attachments") or []:
                    if item.get("id") != attachment_id:
                        continue
                    for key, value in update.get("$set", {}).items():
                        item[key.rsplit(".", 1)[-1]] = value
                    return SimpleNamespace(matched_count=1, modified_count=1)
                return SimpleNamespace(matched_count=0, modified_count=0)
            if "$push" in update:
                for key, value in update["$push"].items():
                    doc.setdefault(key, []).append(value)
            if "$pull" in update:
                for key, criteria in update["$pull"].items():
                    doc[key] = [
                        item
                        for item in doc.get(key) or []
                        if item.get("id") != criteria.get("id")
                    ]
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def find(self, _query: dict, _projection: dict | None = None) -> _Cursor:
        pending = []
        for doc in self.documents:
            needs = False
            for item in doc.get("attachments") or []:
                key = item.get("s3_key") if isinstance(item, dict) else None
                if not (isinstance(key, str) and key.strip()):
                    needs = True
                    break
            if needs:
                pending.append(doc)
        return _Cursor(pending)


class _S3:
    def __init__(self, *, fail: bool = False, bad_head: bool = False) -> None:
        self.fail = fail
        self.bad_head = bad_head
        self.uploads: list[dict] = []

    def upload_file(self, filename: str, bucket: str, key: str, ExtraArgs: dict | None = None):
        if self.fail:
            raise RuntimeError("s3 unavailable")
        path = Path(filename)
        self.uploads.append(
            {
                "bucket": bucket,
                "key": key,
                "size": path.stat().st_size,
                "metadata": (ExtraArgs or {}).get("Metadata") or {},
                "bytes": path.read_bytes(),
            }
        )

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        upload = next(item for item in self.uploads if item["key"] == Key and item["bucket"] == Bucket)
        size = 1 if self.bad_head else upload["size"]
        return {"ContentLength": size, "Metadata": dict(upload["metadata"])}


def _db(documents: list[dict] | None = None) -> SimpleNamespace:
    return SimpleNamespace(transactions=_Transactions(documents))


def test_object_key_is_outside_the_mongodump_prefix(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    key = receipt_object_key(settings, "abc123")
    assert key == "receipts/abc123"
    assert not key.startswith(settings.backup_s3_prefix)


def test_prefix_rejects_parent_segments() -> None:
    with pytest.raises(ValidationError):
        Settings(
            mongodb_uri="mongodb://localhost:27017",
            jwt_secret="a-test-secret-that-is-definitely-32-characters",
            receipt_s3_prefix="../receipts",
        )


async def test_mirror_records_key_and_keeps_the_local_file(tmp_path: Path) -> None:
    tx_id = ObjectId()
    db = _db([{"_id": tx_id, "attachments": []}])
    content = b"%PDF-phase0"
    meta = await add_attachment_bytes(
        db, tmp_path, tx_id, filename="receipt.pdf", content=content
    )
    path = tmp_path / str(tx_id) / meta["stored_name"]
    client = _S3()

    key = await mirror_attachment(
        _settings(tmp_path), db, tx_id, tmp_path, meta, client=client
    )

    assert key == f"receipts/{meta['id']}"
    assert meta["s3_key"] == key
    assert path.is_file()
    assert path.read_bytes() == content
    assert client.uploads[0]["bucket"] == "receipts-bucket"
    assert client.uploads[0]["key"] == key
    assert client.uploads[0]["metadata"]["sha256"] == meta["sha256"]
    assert client.uploads[0]["bytes"] == content
    stored = db.transactions.documents[0]["attachments"][0]
    assert stored["s3_key"] == key
    assert stored["sha256"] == meta["sha256"]


async def test_mirror_failure_leaves_s3_key_empty_and_keeps_the_file(tmp_path: Path) -> None:
    tx_id = ObjectId()
    db = _db([{"_id": tx_id, "attachments": []}])
    meta = await add_attachment_bytes(
        db, tmp_path, tx_id, filename="receipt.pdf", content=b"%PDF-keep-me"
    )
    path = tmp_path / str(tx_id) / meta["stored_name"]

    key = await mirror_attachment(
        _settings(tmp_path), db, tx_id, tmp_path, meta, client=_S3(fail=True)
    )

    assert key == ""
    assert meta["s3_key"] == ""
    assert path.is_file()
    assert path.read_bytes() == b"%PDF-keep-me"
    assert db.transactions.documents[0]["attachments"][0]["s3_key"] == ""


async def test_verify_mismatch_does_not_record_s3_key(tmp_path: Path) -> None:
    tx_id = ObjectId()
    db = _db([{"_id": tx_id, "attachments": []}])
    meta = await add_attachment_bytes(
        db, tmp_path, tx_id, filename="receipt.pdf", content=b"%PDF-mismatch"
    )
    path = tmp_path / str(tx_id) / meta["stored_name"]

    key = await mirror_attachment(
        _settings(tmp_path), db, tx_id, tmp_path, meta, client=_S3(bad_head=True)
    )

    assert key == ""
    assert meta["s3_key"] == ""
    assert path.is_file()


async def test_retry_uploads_only_files_missing_a_key(tmp_path: Path) -> None:
    tx_id = ObjectId()
    db = _db([{"_id": tx_id, "attachments": []}])
    pending = await add_attachment_bytes(
        db, tmp_path, tx_id, filename="pending.pdf", content=b"%PDF-pending"
    )
    done = await add_attachment_bytes(
        db, tmp_path, tx_id, filename="done.pdf", content=b"%PDF-done"
    )
    done["s3_key"] = "receipts/already"
    client = _S3()

    uploaded = await retry_pending_receipt_backups(
        _settings(tmp_path), db, client=client, limit=None
    )

    assert uploaded == 1
    assert client.uploads[0]["key"] == f"receipts/{pending['id']}"
    assert pending["s3_key"] == f"receipts/{pending['id']}"
    assert done["s3_key"] == "receipts/already"
    assert (tmp_path / str(tx_id) / pending["stored_name"]).is_file()


async def test_retry_without_a_bucket_does_nothing(tmp_path: Path) -> None:
    tx_id = ObjectId()
    db = _db([{"_id": tx_id, "attachments": []}])
    meta = await add_attachment_bytes(
        db, tmp_path, tx_id, filename="receipt.pdf", content=b"%PDF-local"
    )

    uploaded = await retry_pending_receipt_backups(
        _settings(tmp_path, backup_s3_bucket=""),
        db,
        client=_S3(),
        limit=None,
    )

    assert uploaded == 0
    assert meta["s3_key"] == ""
    assert (tmp_path / str(tx_id) / meta["stored_name"]).is_file()


async def test_retry_skips_a_missing_local_file(tmp_path: Path) -> None:
    tx_id = ObjectId()
    db = _db(
        [
            {
                "_id": tx_id,
                "attachments": [
                    {
                        "id": "gone",
                        "filename": "gone.pdf",
                        "stored_name": "gone_gone.pdf",
                        "s3_key": "",
                        "sha256": "abc",
                    }
                ],
            }
        ]
    )
    present = await add_attachment_bytes(
        db, tmp_path, tx_id, filename="present.pdf", content=b"%PDF-present"
    )

    uploaded = await retry_pending_receipt_backups(
        _settings(tmp_path), db, client=_S3(), limit=1
    )

    assert uploaded == 1
    assert db.transactions.documents[0]["attachments"][0]["s3_key"] == ""
    assert present["s3_key"] == f"receipts/{present['id']}"


async def test_scheduler_batch_stops_before_the_backlog_is_drained(tmp_path: Path) -> None:
    tx_id = ObjectId()
    db = _db([{"_id": tx_id, "attachments": []}])
    first = await add_attachment_bytes(
        db, tmp_path, tx_id, filename="one.pdf", content=b"%PDF-one"
    )
    second = await add_attachment_bytes(
        db, tmp_path, tx_id, filename="two.pdf", content=b"%PDF-two"
    )

    uploaded = await retry_pending_receipt_backups(
        _settings(tmp_path), db, client=_S3(), limit=1
    )

    assert uploaded == 1
    backed_up = [item for item in (first, second) if item["s3_key"]]
    waiting = [item for item in (first, second) if not item["s3_key"]]
    assert len(backed_up) == 1
    assert len(waiting) == 1
    assert (tmp_path / str(tx_id) / waiting[0]["stored_name"]).is_file()
