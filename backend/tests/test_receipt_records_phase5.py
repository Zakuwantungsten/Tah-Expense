"""Paperwork → Records — folder upload + pack (Phase 5 regression)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.receipt_ops import list_inbox_files, list_storage_tree
from app.receipt_records_ops import (
    build_records_pack,
    create_custom_folder,
    ensure_records_workspace,
    list_record_files,
    resolve_record_destination,
    store_record_file_bytes,
)


def _dig(doc: dict, key: str):
    if "." not in key:
        return key in doc, doc.get(key)
    cur: object = doc
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _match(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        present, actual = _dig(doc, key)
        if isinstance(expected, dict):
            if "$exists" in expected:
                if bool(expected["$exists"]) != present:
                    return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected or "$lte" in expected:
                if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                    return False
                if "$lte" in expected and (actual is None or actual > expected["$lte"]):
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
            if _match(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc: dict):
        if "_id" not in doc:
            doc = {**doc, "_id": ObjectId()}
        self.documents.append(dict(doc))
        return SimpleNamespace(inserted_id=doc["_id"])

    async def update_one(self, query: dict, update: dict, **kwargs):  # noqa: ANN003
        for doc in self.documents:
            if not _match(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def update_many(self, query: dict, update: dict, **kwargs):  # noqa: ANN003
        n = 0
        for doc in self.documents:
            if not _match(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            n += 1
        return SimpleNamespace(modified_count=n)

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.documents if _match(doc, query))

    def find(self, query: dict, projection: dict | None = None):
        docs = [dict(d) for d in self.documents if _match(d, query)]

        class _C:
            def __init__(self, rows: list[dict]) -> None:
                self._docs = rows

            def sort(self, *a, **k):  # noqa: ANN002, ANN003
                return self

            def limit(self, n: int):
                self._docs = self._docs[:n]
                return self

            async def to_list(self, length=None):  # noqa: ANN001
                if length is None:
                    return list(self._docs)
                return list(self._docs[:length])

        return _C(docs)


class FakeDB:
    def __init__(self) -> None:
        self.receipt_files = FakeColl()
        self.receipt_record_folders = FakeColl()
        self.categories = FakeColl()


@pytest.mark.asyncio
async def test_records_upload_excluded_from_inbox_and_packable(tmp_path: Path) -> None:
    db = FakeDB()
    base = tmp_path / "attachments"
    ensure_records_workspace(base)

    folder = await create_custom_folder(db, label="Supplier Pays")
    dest = await resolve_record_destination(
        db, kind="custom", folder_id=folder["id"]
    )
    content = b"%PDF-1.4 records-test"
    meta = await store_record_file_bytes(
        db,
        base,
        filename="invoice.pdf",
        content=content,
        destination=dest,
    )
    assert meta["id"]
    assert (meta.get("record") or {}).get("kind") == "custom"

    inbox = await list_inbox_files(db, status="all", limit=50)
    assert all(str(r.get("id")) != meta["id"] for r in inbox)

    listed = await list_record_files(db, kind="custom", folder_id=folder["id"])
    assert len(listed) == 1
    assert listed[0]["original_filename"] == "invoice.pdf"

    tree = await list_storage_tree(db, base, prefix="")
    names = {f["name"] for f in tree["folders"]}
    assert "records" in names

    payload, summary = await build_records_pack(
        db, base, kind="custom", folder_id=folder["id"]
    )
    assert summary["file_count"] == 1
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = zf.namelist()
        assert "README.txt" in names
        assert any(n.endswith("invoice.pdf") or "invoice" in n for n in names)
        pdfs = [n for n in names if n.lower().endswith(".pdf")]
        assert pdfs
        assert zf.read(pdfs[0]).startswith(b"%PDF")
