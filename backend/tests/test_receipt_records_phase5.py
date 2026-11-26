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
    discard_record_upload_batch,
    ensure_custom_folder_path,
    ensure_records_workspace,
    list_custom_folders,
    list_record_destinations,
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

    async def delete_one(self, query: dict):
        for i, doc in enumerate(self.documents):
            if _match(doc, query):
                del self.documents[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

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

    def aggregate(self, pipeline: list[dict]):
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _match(d, stage["$match"])]
            elif "$group" in stage:
                grouped: dict[tuple, dict] = {}
                spec = stage["$group"]
                id_spec = spec.get("_id") or {}
                for doc in docs:
                    if isinstance(id_spec, dict):
                        key_parts = []
                        for alias, path in id_spec.items():
                            _p, val = _dig(doc, str(path).lstrip("$"))
                            key_parts.append((alias, val))
                        key = tuple(key_parts)
                        row = grouped.setdefault(
                            key,
                            {
                                "_id": {a: v for a, v in key_parts},
                                "count": 0,
                            },
                        )
                    else:
                        row = grouped.setdefault(
                            ("_all",),
                            {"_id": id_spec, "count": 0},
                        )
                    if "count" in spec and "$sum" in (spec.get("count") or {}):
                        row["count"] = int(row.get("count") or 0) + int(
                            spec["count"]["$sum"]
                        )
                    if "total_size" in spec:
                        sum_spec = spec.get("total_size") or {}
                        add = 0
                        if isinstance(sum_spec, dict) and "$sum" in sum_spec:
                            s = sum_spec["$sum"]
                            if isinstance(s, dict) and "$ifNull" in s:
                                path = str((s["$ifNull"] or [None])[0] or "").lstrip(
                                    "$"
                                )
                                _p, val = _dig(doc, path)
                                add = int(val or 0)
                            else:
                                add = int(s or 0)
                        row["total_size"] = int(row.get("total_size") or 0) + add
                docs = list(grouped.values())

        class _Agg:
            def __init__(self, rows: list[dict]) -> None:
                self._docs = rows
                self._i = 0

            def __aiter__(self):
                self._i = 0
                return self

            async def __anext__(self):
                if self._i >= len(self._docs):
                    raise StopAsyncIteration
                row = self._docs[self._i]
                self._i += 1
                return row

            async def to_list(self, length=None):  # noqa: ANN001
                if length is None:
                    return list(self._docs)
                return list(self._docs[:length])

        return _Agg(docs)


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


@pytest.mark.asyncio
async def test_destinations_include_file_counts(tmp_path: Path) -> None:
    db = FakeDB()
    base = tmp_path / "attachments"
    ensure_records_workspace(base)
    db.categories.documents.append(
        {"_id": ObjectId(), "name": "Diesel", "sidebar_name": "Diesel", "is_supplier": False}
    )
    db.categories.documents.append(
        {"_id": ObjectId(), "name": "Empty Item", "is_supplier": False}
    )
    dest = await resolve_record_destination(db, kind="item", ref_key="Diesel")
    await store_record_file_bytes(
        db,
        base,
        filename="a.pdf",
        content=b"%PDF-1.4 a",
        destination=dest,
    )
    await store_record_file_bytes(
        db,
        base,
        filename="b.pdf",
        content=b"%PDF-1.4 b",
        destination=dest,
    )
    payload = await list_record_destinations(db)
    by_key = {r["ref_key"]: r for r in payload["items"]}
    assert by_key["Diesel"]["file_count"] == 2
    assert by_key["Diesel"]["total_size"] == len(b"%PDF-1.4 a") + len(b"%PDF-1.4 b")
    assert by_key["Empty Item"]["file_count"] == 0
    assert by_key["Empty Item"]["total_size"] == 0


@pytest.mark.asyncio
async def test_discard_record_upload_batch_removes_files(tmp_path: Path) -> None:
    db = FakeDB()
    base = tmp_path / "attachments"
    ensure_records_workspace(base)
    dest = await resolve_record_destination(db, kind="item", ref_key="Oil")
    first = await store_record_file_bytes(
        db,
        base,
        filename="one.pdf",
        content=b"%PDF-1.4 one",
        destination=dest,
    )
    second = await store_record_file_bytes(
        db,
        base,
        filename="two.pdf",
        content=b"%PDF-1.4 two",
        destination=dest,
    )
    path1 = base / Path(*str(first["record"]["path"]).split("/"))
    path2 = base / Path(*str(second["record"]["path"]).split("/"))
    assert path1.is_file() and path2.is_file()

    discarded = await discard_record_upload_batch(
        db, base, [first["id"], second["id"]]
    )
    assert discarded == 2
    assert await db.receipt_files.find_one({"_id": first["id"]}) is None
    assert await db.receipt_files.find_one({"_id": second["id"]}) is None
    assert not path1.exists()
    assert not path2.exists()
