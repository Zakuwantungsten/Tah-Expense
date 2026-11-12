"""Phase E — upload security: magic bytes, quarantine, rate limit."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.receipt_ops import (
    INBOX_REJECTED,
    INBOX_UNMATCHED,
    list_inbox_files,
    list_quarantine_files,
    store_receipt_file_bytes,
)
from app.receipt_upload_security import (
    UploadSecurityError,
    check_mobile_upload_rate,
    reset_rate_limits_for_tests,
    strip_jpeg_exif,
    validate_receipt_upload,
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
                return dict(doc)
        return None

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=document.get("_id"))

    def find(self, query: dict, projection: dict | None = None):
        matched = [dict(doc) for doc in self.documents if _matches(doc, query)]

        class _Cursor:
            def sort(self, *_a, **_k):
                return self

            def limit(self, n: int):
                nonlocal matched
                matched = matched[:n]
                return self

            async def to_list(self, length: int | None = None):
                return list(matched) if length is None else list(matched)[:length]

        return _Cursor()


def _db():
    return SimpleNamespace(
        receipt_files=FakeColl(),
        transactions=FakeColl(),
        receipt_requests=FakeColl(),
        operation_events=FakeColl(),
        system_settings=FakeColl(),
        users=FakeColl(),
        people=FakeColl(),
        categories=FakeColl(),
    )


def test_exe_renamed_to_jpg_rejected() -> None:
    mz = b"MZ" + b"\x90" * 64
    with pytest.raises(UploadSecurityError, match="magic-byte"):
        validate_receipt_upload("innocent.jpg", mz, claimed_content_type="image/jpeg")


def test_double_extension_rejected() -> None:
    with pytest.raises(UploadSecurityError, match="Dangerous"):
        validate_receipt_upload("invoice.pdf.exe", b"%PDF-1.4", claimed_content_type="application/pdf")


def test_good_jpeg_and_pdf_pass() -> None:
    jpeg = validate_receipt_upload("ok.jpg", b"\xff\xd8\xff\xe0XXXX")
    assert jpeg["content_type"] == "image/jpeg"
    pdf = validate_receipt_upload("ok.pdf", b"%PDF-1.7\n%")
    assert pdf["content_type"] == "application/pdf"


def test_client_mime_ignored() -> None:
    # Claims image/jpeg but body is PDF with .pdf name — sniffed as pdf
    out = validate_receipt_upload(
        "x.pdf", b"%PDF-1.4", claimed_content_type="image/jpeg"
    )
    assert out["content_type"] == "application/pdf"


def test_oversized_rejected() -> None:
    from app.attachment_ops import MAX_ATTACHMENT_BYTES

    with pytest.raises(UploadSecurityError, match="maximum size"):
        validate_receipt_upload(
            "big.jpg",
            b"\xff\xd8\xff" + b"x" * (MAX_ATTACHMENT_BYTES),
        )


def test_strip_jpeg_exif_removes_app1() -> None:
    # SOI + APP1 (length 6 incl size) + SOS-ish rest
    app1 = b"\xff\xe1\x00\x06Exif"
    rest = b"\xff\xda" + b"payload"
    raw = b"\xff\xd8" + app1 + rest
    cleaned = strip_jpeg_exif(raw)
    assert cleaned.startswith(b"\xff\xd8")
    assert b"Exif" not in cleaned
    assert b"payload" in cleaned


async def test_store_quarantines_bad_upload(tmp_path: Path) -> None:
    db = _db()
    with pytest.raises(ValueError, match="magic-byte"):
        await store_receipt_file_bytes(
            db,
            tmp_path,
            filename="virus.jpg",
            content=b"MZ\x90\x00fake-exe",
            source="expo",
        )
    assert len(db.receipt_files.documents) == 1
    doc = db.receipt_files.documents[0]
    assert doc["inbox_status"] == INBOX_REJECTED
    assert doc["security_rejected"] is True
    assert doc["quarantine_path"].startswith("quarantine/")
    qpath = tmp_path / doc["quarantine_path"]
    assert qpath.is_file()
    # Must not land under files/
    assert not any((tmp_path / "files").glob("*")) or not list(
        (tmp_path / "files").glob("*virus*")
    )
    parked = await list_inbox_files(db, status="parked")
    assert parked == []
    quarantined = await list_quarantine_files(db, tmp_path)
    assert len(quarantined) == 1


async def test_good_upload_still_parks(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="ok.jpg",
        content=b"\xff\xd8\xff\xe0hello",
        source="desktop",
        claimed_content_type="application/octet-stream",
    )
    assert meta["content_type"] == "image/jpeg"
    doc = db.receipt_files.documents[0]
    assert doc["inbox_status"] == INBOX_UNMATCHED
    assert doc.get("security_rejected") is False
    assert (tmp_path / "files" / Path(meta["stored_path"]).name).is_file()


def test_rate_limit_blocks() -> None:
    reset_rate_limits_for_tests()
    check_mobile_upload_rate("u1", limit=2, file_count=1)
    check_mobile_upload_rate("u1", limit=2, file_count=1)
    with pytest.raises(UploadSecurityError, match="rate limit"):
        check_mobile_upload_rate("u1", limit=2, file_count=1)
    reset_rate_limits_for_tests()


def test_mobile_api_rejects_exe_jpg(tmp_path: Path) -> None:
    reset_rate_limits_for_tests()
    db = _db()
    settings = Settings(
        mongodb_uri="mongodb://127.0.0.1:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
        backup_s3_bucket="",
        receipt_mobile_uploads_per_hour=100,
    )
    app = create_app(settings=settings, database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "mobile",
        "role": "cashier",
        "full_name": "ALI",
    }
    with TestClient(app) as client:
        resp = client.post(
            "/v1/mobile/receipt-files",
            files=[("files", ("photo.jpg", b"MZ\x00EXE", "image/jpeg"))],
        )
    assert resp.status_code == 422
    assert db.receipt_files.documents[0]["security_rejected"] is True
