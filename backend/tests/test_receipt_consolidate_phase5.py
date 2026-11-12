"""Phase 5 — filtered consolidate (files-only ZIP)."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.receipt_evidence import build_consolidate_pack
from app.receipt_ops import (
    INBOX_APPROVED,
    approve_receipt_files,
    store_receipt_file_bytes,
)


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$gte" in expected or "$lte" in expected:
                if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                    return False
                if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                    return False
                continue
            if "$in" in expected and actual not in expected["$in"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def sort(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return self

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class FakeColl:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc: dict):
        self.documents.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def update_one(self, query: dict, update: dict, **kwargs):  # noqa: ANN003
        for doc in self.documents:
            if not _matches(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            if "$addToSet" in update:
                for key, val in update["$addToSet"].items():
                    if isinstance(val, dict) and "$each" in val:
                        cur = list(doc.get(key) or [])
                        for item in val["$each"]:
                            if item not in cur:
                                cur.append(item)
                        doc[key] = cur
                    else:
                        cur = list(doc.get(key) or [])
                        if val not in cur:
                            cur.append(val)
                        doc[key] = cur
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def find(self, query: dict, projection: dict | None = None):
        return _Cursor([dict(d) for d in self.documents if _matches(d, query)])


def _db(txs: list[dict] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        receipt_files=FakeColl(),
        transactions=FakeColl(txs or []),
        receipt_requests=FakeColl(),
        receipt_exports=FakeColl(),
    )


@pytest.mark.asyncio
async def test_consolidate_unique_approved_files(tmp_path: Path) -> None:
    tx_id = ObjectId()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = _db(
        [
            {
                "_id": tx_id,
                "date": now,
                "amount": 5000,
                "currency": "TZS",
                "item": "Diesel",
                "payee": "SHELL DAR",
                "ownership": "Ali",
                "attachments": [],
                "trashed": False,
            }
        ]
    )
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="fuel.jpg",
        content=b"\xff\xd8\xff\xe0jpeg-bytes",
        source="desktop",
        transaction_id=tx_id,
    )
    # Link attachment meta onto the tx like production
    for tx in db.transactions.documents:
        if tx["_id"] == tx_id:
            tx["attachments"] = [
                {
                    "id": meta["id"],
                    "filename": "fuel.jpg",
                    "stored_path": meta["stored_path"],
                    "sha256": meta["sha256"],
                    "size": meta["size"],
                }
            ]
    await approve_receipt_files(db, tmp_path, file_ids=[meta["id"]], actor_id=None)
    for d in db.receipt_files.documents:
        if d["_id"] == meta["id"]:
            assert d["inbox_status"] == INBOX_APPROVED

    payload, summary = await build_consolidate_pack(
        db,
        tmp_path,
        date_from=now.date(),
        date_to=now.date(),
        payee="SHELL DAR",
        approved_only=True,
    )
    assert summary["files_in_zip"] == 1
    assert summary["missing_on_disk"] == 0
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = zf.namelist()
        assert "SUMMARY.txt" in names
        assert any(n.endswith(".jpg") or "fuel" in n.lower() for n in names if n != "SUMMARY.txt")
