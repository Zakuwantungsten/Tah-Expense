"""Phase 1 view/storage — open meta + safe path resolve for desk viewer."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.receipt_ops import (
    get_receipt_file_open_meta,
    open_receipt_file_bytes,
    resolve_receipt_file_path,
    store_receipt_file_bytes,
)


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if doc.get(key) != expected:
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

    async def insert_one(self, doc: dict) -> None:
        self.documents.append(dict(doc))


def _db() -> SimpleNamespace:
    return SimpleNamespace(receipt_files=FakeColl())


@pytest.mark.asyncio
async def test_open_meta_and_bytes(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="slip.png",
        content=b"\x89PNG\r\n\x1a\n" + b"x" * 32,
        source="desktop",
    )
    doc = await db.receipt_files.find_one({"_id": meta["id"]})
    path = resolve_receipt_file_path(tmp_path, doc)
    assert path is not None and path.is_file()

    open_meta = await get_receipt_file_open_meta(db, tmp_path, meta["id"])
    assert open_meta["id"] == meta["id"]
    assert open_meta["original_filename"] == "slip.png"
    assert open_meta["file_present"] is True
    assert "image" in str(open_meta["content_type"])

    path2, filename, content_type = await open_receipt_file_bytes(
        db, tmp_path, meta["id"]
    )
    assert path2 == path
    assert filename == "slip.png"
    assert "image" in content_type
    assert path2.read_bytes().startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_open_missing_id(tmp_path: Path) -> None:
    db = _db()
    with pytest.raises(ValueError, match="not found"):
        await get_receipt_file_open_meta(db, tmp_path, "nope")
    with pytest.raises(ValueError, match="not found"):
        await open_receipt_file_bytes(db, tmp_path, "nope")


@pytest.mark.asyncio
async def test_path_jail_rejects_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.bin"
    outside.write_bytes(b"secret")
    doc = {
        "_id": "x1",
        "stored_path": "../outside-secret.bin",
        "original_filename": "evil.bin",
        "content_type": "application/octet-stream",
        "size": 6,
    }
    assert resolve_receipt_file_path(tmp_path, doc) is None
