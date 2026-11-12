"""Phase D — approved browse folders + multi-item / approved-only packs."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from bson import ObjectId

from app.receipt_evidence import build_evidence_pack
from app.receipt_ops import (
    INBOX_APPROVED,
    approve_receipt_files,
    ensure_receipt_workspace,
    manual_link_files,
    materialize_approved_file,
    store_receipt_file_bytes,
)


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$or":
            return any(_matches(doc, clause) for clause in expected)
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected or "$lte" in expected:
                if actual is None:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
                    return False
                continue
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

    async def update_one(self, query: dict, update: dict, **_k) -> SimpleNamespace:
        for doc in self.documents:
            if not _matches(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            if "$push" in update:
                for key, value in update["$push"].items():
                    doc.setdefault(key, [])
                    doc[key] = list(doc[key]) + [value]
            if "$addToSet" in update:
                for key, value in update["$addToSet"].items():
                    existing = list(doc.get(key) or [])
                    if isinstance(value, dict) and "$each" in value:
                        for item in value["$each"]:
                            if item not in existing:
                                existing.append(item)
                    elif value not in existing:
                        existing.append(value)
                    doc[key] = existing
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


def _db(*, files=None, transactions=None, requests=None):
    return SimpleNamespace(
        receipt_files=FakeColl(files),
        transactions=FakeColl(transactions),
        receipt_requests=FakeColl(requests),
        receipt_exports=FakeColl(),
        operation_events=FakeColl(),
        system_settings=FakeColl(),
        users=FakeColl(),
        people=FakeColl(),
        categories=FakeColl(),
    )


async def test_approve_materializes_item_folder(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db, tmp_path, filename="latra.jpg", content=b"\xff\xd8\xff\xe0img-bytes", source="desktop"
    )
    tx_id = ObjectId()
    db.transactions.documents.append(
        {
            "_id": tx_id,
            "date": datetime(2026, 3, 15),
            "item": "LATRA",
            "payee": "TRA",
            "ownership": "ALI",
            "attachments": [],
            "receipt_status": "pending",
            "amount": 100,
            "currency": "TZS",
        }
    )
    db.receipt_requests.documents.append(
        {
            "_id": ObjectId(),
            "transaction_id": tx_id,
            "status": "needed",
            "asked_of_name": "ALI",
        }
    )
    await manual_link_files(
        db,
        file_ids=[meta["id"]],
        transaction_ids=[tx_id],
        actor_id=None,
        attachments_dir=tmp_path,
    )
    result = await approve_receipt_files(
        db, tmp_path, file_ids=[meta["id"]], actor_id=None
    )
    assert result["updated"] == 1
    assert result["materialized"] == 1
    stored_name = Path(meta["stored_path"]).name
    browse = (
        ensure_receipt_workspace(tmp_path)["approved"]
        / "2026"
        / "03"
        / "LATRA"
        / stored_name
    )
    assert browse.is_file()
    # Canonical untouched
    assert (tmp_path / "files" / stored_name).is_file()
    doc = await db.receipt_files.find_one({"_id": meta["id"]})
    assert doc["inbox_status"] == INBOX_APPROVED
    assert any("approved/2026/03/LATRA/" in p for p in doc["materialized_paths"])
    # Idempotent
    again = await materialize_approved_file(db, tmp_path, doc)
    assert again
    assert browse.is_file()


async def test_materialize_skips_until_item_present(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db, tmp_path, filename="x.jpg", content=b"\xff\xd8\xff\xe0x", source="desktop"
    )
    tx_id = ObjectId()
    db.transactions.documents.append(
        {
            "_id": tx_id,
            "date": datetime(2026, 1, 1),
            "item": "",
            "category_name": "",
            "attachments": [],
            "receipt_status": "pending",
        }
    )
    db.receipt_files.documents[0]["inbox_status"] = INBOX_APPROVED
    db.receipt_files.documents[0]["transaction_ids"] = [tx_id]
    paths = await materialize_approved_file(db, tmp_path, db.receipt_files.documents[0])
    assert paths
    assert any(p.startswith("parked/unmapped/") for p in paths)
    assert (tmp_path / "parked" / "unmapped").is_dir()
    # Item lands → relocate out of temp into sorted approved folder
    db.transactions.documents[0]["item"] = "Diesel"
    db.receipt_files.documents[0]["materialized_paths"] = list(paths)
    paths = await materialize_approved_file(db, tmp_path, db.receipt_files.documents[0])
    assert paths
    assert any("approved/2026/01/Diesel/" in p for p in paths)
    assert (tmp_path / "approved" / "2026" / "01" / "Diesel").is_dir()
    assert not any(p.startswith("parked/unmapped/") for p in paths)


async def test_pack_multi_item_and_approved_only(tmp_path: Path) -> None:
    files_dir = ensure_receipt_workspace(tmp_path)["files"]
    good = files_dir / "good.jpg"
    bad = files_dir / "parked.jpg"
    good.write_bytes(b"approved-bytes")
    bad.write_bytes(b"parked-bytes")
    tx_diesel = ObjectId()
    tx_latra = ObjectId()
    tx_other = ObjectId()
    db = _db(
        files=[
            {"_id": "f_good", "inbox_status": "approved", "stored_path": "files/good.jpg"},
            {"_id": "f_park", "inbox_status": "matched", "stored_path": "files/parked.jpg"},
        ],
        transactions=[
            {
                "_id": tx_diesel,
                "date": datetime(2026, 3, 10),
                "item": "Diesel",
                "amount": 1,
                "currency": "TZS",
                "ownership": "ALI",
                "attachments": [
                    {
                        "id": "f_good",
                        "filename": "good.jpg",
                        "stored_path": "files/good.jpg",
                        "stored_name": "files/good.jpg",
                    }
                ],
            },
            {
                "_id": tx_latra,
                "date": datetime(2026, 3, 11),
                "item": "LATRA",
                "amount": 2,
                "currency": "TZS",
                "ownership": "ALI",
                "attachments": [
                    {
                        "id": "f_park",
                        "filename": "parked.jpg",
                        "stored_path": "files/parked.jpg",
                        "stored_name": "files/parked.jpg",
                    }
                ],
            },
            {
                "_id": tx_other,
                "date": datetime(2026, 3, 12),
                "item": "Toll",
                "amount": 3,
                "currency": "TZS",
                "ownership": "ALI",
                "attachments": [],
            },
        ],
        requests=[],
    )
    payload, summary = await build_evidence_pack(
        db,
        tmp_path,
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 31),
        items=["Diesel", "LATRA"],
        approved_only=True,
    )
    assert summary["items"] == ["Diesel", "LATRA"]
    assert summary["approved_only"] is True
    assert summary["files_in_zip"] == 1  # only approved diesel file
    assert summary["skipped_unapproved"] >= 1
    # Toll excluded by item filter
    assert summary["row_count"] == 2
    assert len(payload) > 40

    # Without approved_only, LATRA parked file is included
    _payload2, summary2 = await build_evidence_pack(
        db,
        tmp_path,
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 31),
        items=["Diesel", "LATRA"],
        approved_only=False,
    )
    assert summary2["files_in_zip"] == 2
