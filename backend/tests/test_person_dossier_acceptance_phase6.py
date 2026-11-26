"""Phase 6 — person dossier acceptance: early park → dossier → link → submitted/received."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.receipt_ops import (
    INBOX_MATCHED,
    INBOX_REJECTED,
    INBOX_UNMATCHED,
    ensure_receipt_workspace,
    manual_link_files,
    park_receipt_uploads,
    person_receipt_dossier,
)


def _resolve_expr(doc: dict, expr: object) -> object:
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr[1:])
    if isinstance(expr, dict) and "$ifNull" in expr:
        left, right = expr["$ifNull"]
        value = _resolve_expr(doc, left)
        return value if value is not None else _resolve_expr(doc, right)
    return expr


def _matches(doc: dict, query: dict) -> bool:
    if "$and" in query:
        return all(_matches(doc, clause) for clause in query["$and"])
    if "$or" in query:
        if not any(_matches(doc, clause) for clause in query["$or"]):
            return False
        rest = {k: v for k, v in query.items() if k != "$or"}
        return _matches(doc, rest) if rest else True
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$exists" in expected:
                exists = key in doc and actual is not None
                if bool(expected["$exists"]) != exists:
                    if expected["$exists"] is False and key not in doc:
                        continue
                    if expected["$exists"] is False and actual is None:
                        continue
                    if expected["$exists"] is True and exists:
                        continue
                    return False
                continue
            if "$ne" in expected:
                if actual == expected["$ne"]:
                    return False
                continue
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
            if "$unset" in update:
                for key in update["$unset"]:
                    doc.pop(key, None)
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

            def skip(self, n: int):
                nonlocal matched
                matched = matched[n:]
                return self

            async def to_list(self, length: int | None = None):
                return list(matched) if length is None else list(matched)[:length]

        return _Cursor()

    def aggregate(self, pipeline: list[dict]):
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [doc for doc in docs if _matches(doc, stage["$match"])]
            elif "$addFields" in stage:
                for doc in docs:
                    for key, expr in stage["$addFields"].items():
                        doc[key] = _resolve_expr(doc, expr)
            elif "$sort" in stage:
                keys = list(stage["$sort"].items())
                for field, direction in reversed(keys):
                    docs.sort(
                        key=lambda d, f=field: (d.get(f) is None, d.get(f)),
                        reverse=direction < 0,
                    )
            elif "$limit" in stage:
                docs = docs[: int(stage["$limit"])]
            elif "$project" in stage:
                projected: list[dict] = []
                for doc in docs:
                    row = {"_id": doc.get("_id")}
                    for key, flag in stage["$project"].items():
                        if flag in (1, True) and key in doc:
                            row[key] = doc[key]
                    projected.append(row)
                docs = projected

        class _Cursor:
            async def to_list(self, length: int | None = None):
                if length is None:
                    return list(docs)
                return list(docs)[:length]

        return _Cursor()


def _db(
    *,
    people: list[dict] | None = None,
    transactions: list[dict] | None = None,
    requests: list[dict] | None = None,
    files: list[dict] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        people=FakeColl(people),
        transactions=FakeColl(transactions),
        receipt_requests=FakeColl(requests),
        receipt_files=FakeColl(files),
        operation_events=FakeColl(),
        system_settings=FakeColl(),
        users=FakeColl(),
        auth_sessions=FakeColl(),
        categories=FakeColl(),
    )


@pytest.mark.asyncio
async def test_dossier_early_park_link_rejected_acceptance(tmp_path: Path) -> None:
    """Seeded person: early park under them → link clears unmatched → submitted/received;
    rejected attributed via linked login still shows.
    """
    ensure_receipt_workspace(tmp_path)
    now = datetime.now(UTC)
    user_id = ObjectId()
    tx_id = ObjectId()
    day = now.date()
    day_dt = datetime(day.year, day.month, day.day, 10, 0, 0)

    db = _db(
        people=[
            {
                "_id": ObjectId(),
                "name": "ALI",
                "active": True,
                "user_id": user_id,
                "receives_receipt_requests": True,
            }
        ],
        transactions=[
            {
                "_id": tx_id,
                "date": day_dt,
                "amount": 15000,
                "currency": "TZS",
                "item": "Diesel",
                "ownership": "ALI",
                "receipt_status": "pending",
                "attachments": [],
                "trashed": False,
            }
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_id,
                "asked_of_name": "ALI",
                "status": "needed",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=4),
                "reminder_count": 0,
            }
        ],
        files=[
            {
                "_id": "rej_ali",
                "original_filename": "bad.jpg",
                "inbox_status": INBOX_REJECTED,
                "uploaded_at": now - timedelta(hours=6),
                "uploaded_by": user_id,
                "transaction_ids": [],
                "rejection_reason": "blurry",
                "security_rejected": False,
            }
        ],
    )

    parked = await park_receipt_uploads(
        db,
        tmp_path,
        uploads=[("early.jpg", b"\xff\xd8\xff\xe0early-ali")],
        actor_id=user_id,
        intended_ownership="ali",
    )
    assert len(parked) == 1
    park_id = str(parked[0]["id"])
    assert parked[0]["intended_ownership"] == "ALI"
    assert db.receipt_files.documents[-1]["inbox_status"] == INBOX_UNMATCHED

    before = await person_receipt_dossier(
        db,
        "ALI",
        date_from=day - timedelta(days=1),
        date_to=day,
    )
    assert before["ownership"] == "ALI"
    assert before["person"] is not None
    assert before["person"]["user_id"] == str(user_id)
    assert before["person"]["receives_receipt_requests"] is True

    assert before["stats"]["still_needed"] == 1
    assert before["stats"]["unmatched_uploads"] == 1
    assert before["stats"]["rejected_files"] == 1
    assert before["stats"]["entries"] == 1
    assert before["stats"]["needed_requests"] == 1

    assert {row["id"] for row in before["parked"]} >= {park_id}
    assert {row["id"] for row in before["rejected"]} == {"rej_ali"}
    assert park_id not in {row["id"] for row in before["submitted"]}
    assert len(before["needed"]) == 1

    linked = await manual_link_files(
        db,
        file_ids=[park_id],
        transaction_ids=[tx_id],
        actor_id=user_id,
        attachments_dir=tmp_path,
    )
    assert linked["linked"] == 1
    file_doc = await db.receipt_files.find_one({"_id": park_id})
    assert file_doc["inbox_status"] == INBOX_MATCHED
    assert db.receipt_requests.documents[0]["status"] == "received"

    after = await person_receipt_dossier(
        db,
        "ALI",
        date_from=day - timedelta(days=1),
        date_to=day,
    )
    # Early unmatched park cleared; evidence now submitted; chase closed.
    assert after["stats"]["unmatched_uploads"] == 0
    assert after["stats"]["still_needed"] == 0
    assert after["stats"]["received"] >= 1
    assert after["stats"]["rejected_files"] == 1
    assert park_id in {row["id"] for row in after["submitted"]}
    assert {row["id"] for row in after["rejected"]} == {"rej_ali"}
    assert after["needed"] == []
    assert after["entries"][0].get("primary_file_id") == park_id or any(
        str(a.get("id") or a.get("file_id") or "") == park_id
        for a in (db.transactions.documents[0].get("attachments") or [])
    )
