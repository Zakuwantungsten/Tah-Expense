"""Paperwork → Records — folder upload + pack (Phase 5 regression)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.receipt_ops import INBOX_APPROVED, list_inbox_files, list_storage_tree
from app.receipt_records_ops import (
    build_records_pack,
    create_custom_folder,
    ensure_custom_folder_path,
    ensure_records_workspace,
    list_custom_folders,
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
    if "$and" in query:
        return all(_match(doc, clause) for clause in query["$and"])
    if "$or" in query:
        if not any(_match(doc, clause) for clause in query["$or"]):
            return False
        rest = {k: v for k, v in query.items() if k != "$or"}
        return _match(doc, rest) if rest else True
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
        self.transactions = FakeColl()
        self.receipt_requests = FakeColl()


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


@pytest.mark.asyncio
async def test_nested_custom_folder_path_and_disk(tmp_path: Path) -> None:
    db = FakeDB()
    base = tmp_path / "attachments"
    ensure_records_workspace(base)

    leaf = await ensure_custom_folder_path(db, path="Contracts/2026")
    assert leaf["label"] == "2026"
    assert leaf["depth"] == 1
    assert leaf["path_labels"] == ["Contracts", "2026"]
    assert leaf["display_label"] == "Contracts / 2026"
    assert leaf["parent_id"]

    folders = await list_custom_folders(db)
    assert len(folders) == 2
    assert {f["label"] for f in folders} == {"Contracts", "2026"}

    dest = await resolve_record_destination(db, kind="custom", folder_id=leaf["id"])
    assert dest["path_slugs"] == ["contracts", "2026"]
    meta = await store_record_file_bytes(
        db,
        base,
        filename="deal.pdf",
        content=b"%PDF-1.4 nested",
        destination=dest,
    )
    path = str((meta.get("record") or {}).get("path") or "")
    assert path.startswith("records/custom/contracts/2026/")
    assert (base / Path(*path.split("/"))).is_file()

    # Same path reuses folders
    again = await ensure_custom_folder_path(db, path="Contracts/2026")
    assert again["id"] == leaf["id"]
    assert len(await list_custom_folders(db)) == 2


@pytest.mark.asyncio
async def test_records_file_date_controls_browse_month(tmp_path: Path) -> None:
    """Backlog filing: January file_date → …/2026/01/ even when uploaded later."""
    from datetime import date as date_cls

    db = FakeDB()
    base = tmp_path / "attachments"
    ensure_records_workspace(base)
    dest = await resolve_record_destination(db, kind="item", ref_key="Diesel")
    meta = await store_record_file_bytes(
        db,
        base,
        filename="jan-fuel.pdf",
        content=b"%PDF-1.4 january-backlog",
        destination=dest,
        file_date=date_cls(2026, 1, 15),
    )
    path = str((meta.get("record") or {}).get("path") or "")
    assert "/2026/01/" in path
    assert (meta.get("record") or {}).get("file_date") == "2026-01-15"
    assert (base / Path(*path.split("/"))).is_file()

    listed = await list_record_files(
        db,
        kind="item",
        ref_key="Diesel",
        date_from=date_cls(2026, 1, 1),
        date_to=date_cls(2026, 1, 31),
    )
    assert any(r["id"] == meta["id"] for r in listed)


@pytest.mark.asyncio
async def test_records_item_lists_approved_chase_receipt(tmp_path: Path) -> None:
    db = FakeDB()
    base = tmp_path / "attachments"
    ensure_records_workspace(base)
    tx_id = ObjectId()
    file_id = "approved_diesel_1"

    db.transactions.documents.append(
        {
            "_id": tx_id,
            "date": __import__("datetime").datetime(2026, 5, 1, 10, 0, 0),
            "amount": 100,
            "currency": "TZS",
            "item": "Diesel",
            "payee": "Shell",
            "ownership": "ALI",
            "attachments": [{"id": file_id, "file_id": file_id, "filename": "fuel.pdf"}],
            "receipt_status": "received",
            "trashed": False,
        }
    )
    db.receipt_files.documents.append(
        {
            "_id": file_id,
            "original_filename": "fuel.pdf",
            "inbox_status": INBOX_APPROVED,
            "uploaded_at": __import__("datetime").datetime(
                2026, 5, 1, 12, 0, 0, tzinfo=__import__("datetime").timezone.utc
            ),
            "transaction_ids": [tx_id],
            "security_rejected": False,
            "size": 12,
            "stored_path": "files/fuel.pdf",
            "materialized_paths": ["approved/2026/05/Diesel/fuel.pdf"],
        }
    )

    # Also file a Records-native document under Diesel
    dest = await resolve_record_destination(db, kind="item", ref_key="Diesel")
    await store_record_file_bytes(
        db,
        base,
        filename="policy.pdf",
        content=b"%PDF-1.4 policy",
        destination=dest,
    )

    listed = await list_record_files(db, kind="item", ref_key="Diesel", limit=50)
    ids = {row["id"] for row in listed}
    assert file_id in ids
    approved_row = next(r for r in listed if r["id"] == file_id)
    assert approved_row["record"]["from_approved"] is True
    assert any(r["original_filename"] == "policy.pdf" for r in listed)
