"""Phase G — acceptance flows: E2E, early→link, 1→3, partial→complete, verify gate."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.receipt_evidence import build_evidence_pack
from app.receipt_ops import (
    INBOX_APPROVED,
    INBOX_MATCHED,
    INBOX_UNMATCHED,
    approve_receipt_files,
    ensure_receipt_request,
    ensure_receipt_workspace,
    list_inbox_files,
    manual_link_files,
    reject_if_needed_receipts,
    store_receipt_file_bytes,
)


JPEG = b"\xff\xd8\xff\xe0acceptance"
PDF = b"%PDF-1.4 acceptance"


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
        doc = dict(document)
        if "_id" not in doc:
            doc["_id"] = ObjectId()
        self.documents.append(doc)
        document["_id"] = doc["_id"]
        return SimpleNamespace(inserted_id=doc["_id"])

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
                return list(matched) if length is None else list(matched)[:length]

        return _Cursor()


def _db(*, categories=None, people=None, transactions=None, requests=None, files=None):
    return SimpleNamespace(
        categories=FakeColl(categories),
        people=FakeColl(people),
        transactions=FakeColl(transactions),
        receipt_requests=FakeColl(requests),
        receipt_files=FakeColl(files),
        receipt_exports=FakeColl(),
        operation_events=FakeColl(),
        system_settings=FakeColl(),
        users=FakeColl(),
    )


async def test_e2e_save_request_upload_approve_folder_pack(tmp_path: Path) -> None:
    """save row → request → upload → inbox → approve → folder → pack."""
    cat_id = ObjectId()
    tx_id = ObjectId()
    db = _db(
        categories=[{"_id": cat_id, "name": "LATRA", "requires_receipt": True}],
        people=[{"_id": ObjectId(), "name": "ALI", "active": True}],
        transactions=[
            {
                "_id": tx_id,
                "date": datetime(2026, 3, 15),
                "amount": 50000,
                "currency": "TZS",
                "ownership": "ALI",
                "category_id": cat_id,
                "item": "LATRA",
                "payee": "TRA",
                "attachments": [],
                "receipt_status": "pending",
            }
        ],
    )

    # 1) Save row → open request
    tx = await db.transactions.find_one({"_id": tx_id})
    req = await ensure_receipt_request(db, tx)
    assert req is not None
    assert req["status"] == "needed"

    # Verify still blocked while needed
    with pytest.raises(ValueError, match="still need a receipt"):
        await reject_if_needed_receipts(db, [tx_id])

    # 2) Expo-style upload linked to request/tx
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="latra.jpg",
        content=JPEG,
        source="expo",
        request_id=req["_id"],
        transaction_id=tx_id,
    )
    await manual_link_files(
        db,
        file_ids=[meta["id"]],
        transaction_ids=[tx_id],
        actor_id=None,
        attachments_dir=tmp_path,
    )
    assert db.receipt_requests.documents[0]["status"] == "received"
    inbox = await list_inbox_files(db, status="matched")
    assert meta["id"] in {row["id"] for row in inbox}

    # 3) Approve → browse folder
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
    assert db.receipt_files.documents[0]["inbox_status"] == INBOX_APPROVED

    # Verify gate allows approve path once received
    await reject_if_needed_receipts(db, [tx_id])

    # 4) Evidence pack (approved only)
    # Refresh attachment meta on tx for pack resolver
    tx_doc = db.transactions.documents[0]
    assert tx_doc["attachments"]
    payload, summary = await build_evidence_pack(
        db,
        tmp_path,
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 31),
        items=["LATRA"],
        approved_only=True,
    )
    assert summary["files_in_zip"] >= 1
    assert summary["approved_only"] is True
    assert len(payload) > 40


async def test_early_upload_later_link_approve(tmp_path: Path) -> None:
    db = _db(
        categories=[{"_id": ObjectId(), "name": "Diesel", "requires_receipt": True}],
        people=[{"_id": ObjectId(), "name": "BETA", "active": True}],
    )
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="early.jpg",
        content=JPEG,
        source="expo_early",
    )
    assert meta["id"]
    parked = await list_inbox_files(db, status="parked")
    assert parked[0]["inbox_status"] == INBOX_UNMATCHED

    tx_id = ObjectId()
    db.transactions.documents.append(
        {
            "_id": tx_id,
            "date": datetime(2026, 4, 2),
            "amount": 100,
            "currency": "TZS",
            "ownership": "BETA",
            "item": "Diesel",
            "attachments": [],
            "receipt_status": "pending",
        }
    )
    db.receipt_requests.documents.append(
        {
            "_id": ObjectId(),
            "transaction_id": tx_id,
            "status": "needed",
            "asked_of_name": "BETA",
            "opened_at": datetime(2026, 4, 2),
            "reminder_count": 0,
        }
    )
    linked = await manual_link_files(
        db,
        file_ids=[meta["id"]],
        transaction_ids=[tx_id],
        actor_id=None,
        attachments_dir=tmp_path,
    )
    assert linked["linked"] == 1
    assert db.receipt_files.documents[0]["inbox_status"] == INBOX_MATCHED
    assert db.receipt_requests.documents[0]["status"] == "received"

    approved = await approve_receipt_files(
        db, tmp_path, file_ids=[meta["id"]], actor_id=None
    )
    assert approved["materialized"] == 1
    assert (
        tmp_path / "approved" / "2026" / "04" / "Diesel" / Path(meta["stored_path"]).name
    ).is_file()


async def test_one_file_three_rows(tmp_path: Path) -> None:
    db = _db()
    meta = await store_receipt_file_bytes(
        db, tmp_path, filename="shared.pdf", content=PDF, source="desktop"
    )
    txs = [ObjectId(), ObjectId(), ObjectId()]
    for tid, name in zip(txs, ("ALI", "BETA", "GAMMA"), strict=True):
        db.transactions.documents.append(
            {
                "_id": tid,
                "date": datetime(2026, 3, 10),
                "amount": 10,
                "currency": "TZS",
                "ownership": name,
                "item": "Toll",
                "attachments": [],
                "receipt_status": "pending",
            }
        )
        db.receipt_requests.documents.append(
            {
                "_id": ObjectId(),
                "transaction_id": tid,
                "status": "needed",
                "asked_of_name": name,
                "opened_at": datetime(2026, 3, 10),
                "reminder_count": 0,
            }
        )
    result = await manual_link_files(
        db,
        file_ids=[meta["id"]],
        transaction_ids=txs,
        actor_id=None,
        attachments_dir=tmp_path,
    )
    assert result["linked"] == 3
    assert set(db.receipt_files.documents[0]["transaction_ids"]) == set(txs)
    assert all(r["status"] == "received" for r in db.receipt_requests.documents)
    assert all(len(tx["attachments"]) == 1 for tx in db.transactions.documents)


async def test_partial_then_complete(tmp_path: Path) -> None:
    db = _db()
    fuel = await store_receipt_file_bytes(
        db, tmp_path, filename="fuel.jpg", content=JPEG, source="desktop"
    )
    rest = await store_receipt_file_bytes(
        db, tmp_path, filename="rest.jpg", content=JPEG + b"2", source="desktop"
    )
    tx_id = ObjectId()
    db.transactions.documents.append(
        {
            "_id": tx_id,
            "date": datetime(2026, 5, 1),
            "amount": 200,
            "currency": "TZS",
            "ownership": "ALI",
            "item": "Trip",
            "attachments": [],
            "receipt_status": "pending",
        }
    )
    db.receipt_requests.documents.append(
        {
            "_id": ObjectId(),
            "transaction_id": tx_id,
            "status": "needed",
            "asked_of_name": "ALI",
            "opened_at": datetime(2026, 5, 1),
            "reminder_count": 0,
        }
    )

    partial = await manual_link_files(
        db,
        file_ids=[fuel["id"]],
        transaction_ids=[tx_id],
        actor_id=None,
        partial=True,
        partial_note="covers fuel only",
        attachments_dir=tmp_path,
    )
    assert partial["partial"] is True
    assert db.receipt_requests.documents[0]["status"] == "needed"
    assert db.transactions.documents[0]["receipt_status"] == "partial"

    cat_id = ObjectId()
    db.categories = FakeColl(
        [{"_id": cat_id, "name": "Trip", "requires_receipt": True}]
    )
    db.transactions.documents[0]["category_id"] = cat_id
    with pytest.raises(ValueError, match="still need a receipt"):
        await reject_if_needed_receipts(db, [tx_id])

    # Complete with second full link
    complete = await manual_link_files(
        db,
        file_ids=[rest["id"]],
        transaction_ids=[tx_id],
        actor_id=None,
        partial=False,
        attachments_dir=tmp_path,
    )
    assert complete["linked"] == 1
    assert db.receipt_requests.documents[0]["status"] == "received"
    assert db.transactions.documents[0]["receipt_status"] == "received"
    assert len(db.transactions.documents[0]["attachments"]) == 2
    await reject_if_needed_receipts(db, [tx_id])


async def test_verify_still_blocks_needed() -> None:
    cat_id = ObjectId()
    tx_id = ObjectId()
    db = _db(
        categories=[{"_id": cat_id, "name": "LATRA", "requires_receipt": True}],
        transactions=[
            {
                "_id": tx_id,
                "category_id": cat_id,
                "item": "LATRA",
                "attachments": [],
            }
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_id,
                "status": "needed",
            }
        ],
    )
    with pytest.raises(ValueError, match="still need a receipt"):
        await reject_if_needed_receipts(db, [tx_id])
