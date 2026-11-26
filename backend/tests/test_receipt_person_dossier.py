"""Person receipt dossier — Phase 2 composition API."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.receipt_ops import (
    INBOX_APPROVED,
    INBOX_MATCHED,
    INBOX_REJECTED,
    INBOX_UNMATCHED,
    person_receipt_dossier,
)


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
                    # Treat missing key as not exists (False branch passes).
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
            if "$gte" in expected or "$lte" in expected:
                if actual is None:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
                    return False
                continue
            if "$in" in expected and actual not in expected["$in"]:
                return False
            continue
        if actual != expected:
            return False
    return True


def _resolve_expr(doc: dict, expr: object) -> object:
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr[1:])
    if isinstance(expr, dict) and "$ifNull" in expr:
        left, right = expr["$ifNull"]
        value = _resolve_expr(doc, left)
        return value if value is not None else _resolve_expr(doc, right)
    return expr


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
                if length is None:
                    return list(matched)
                return list(matched)[:length]

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


class FakeEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=ObjectId())


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
        backup_s3_bucket="",
        receipt_reminder_hours=24,
    )


def _db(
    *,
    requests: list[dict] | None = None,
    transactions: list[dict] | None = None,
    files: list[dict] | None = None,
    people: list[dict] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        receipt_requests=FakeColl(requests),
        transactions=FakeColl(transactions),
        receipt_files=FakeColl(files),
        people=FakeColl(people),
        operation_events=FakeEvents(),
        system_settings=FakeColl(),
        users=FakeColl(),
        auth_sessions=FakeColl(),
        categories=FakeColl(),
    )


@pytest.mark.asyncio
async def test_person_receipt_dossier_sections() -> None:
    now = datetime.now(UTC)
    tx_needed = ObjectId()
    tx_received = ObjectId()
    tx_bob = ObjectId()
    user_id = ObjectId()
    file_id = "66cc00000000000000000001"

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
                "_id": tx_needed,
                "date": day_dt,
                "amount": 100,
                "currency": "TZS",
                "item": "Diesel",
                "ownership": "Ali",
                "receipt_status": "pending",
                "attachments": [],
                "trashed": False,
            },
            {
                "_id": tx_received,
                "date": day_dt + timedelta(hours=1),
                "amount": 200,
                "currency": "TZS",
                "item": "LATRA",
                "ownership": "ALI",
                "receipt_status": "received",
                "attachments": [
                    {"id": file_id, "file_id": file_id, "filename": "ok.pdf"}
                ],
                "trashed": False,
            },
            {
                "_id": tx_bob,
                "date": day_dt + timedelta(hours=2),
                "amount": 50,
                "currency": "TZS",
                "item": "Other",
                "ownership": "BOB",
                "receipt_status": "pending",
                "attachments": [],
                "trashed": False,
            },
        ],
        requests=[
            {
                "_id": ObjectId(),
                "transaction_id": tx_needed,
                "asked_of_name": "ALI",
                "status": "needed",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=5),
                "reminder_count": 1,
            },
            {
                "_id": ObjectId(),
                "transaction_id": tx_received,
                "asked_of_name": "ALI",
                "status": "received",
                "opened_at": now.replace(tzinfo=None) - timedelta(days=2),
                "reminder_count": 0,
            },
            {
                "_id": ObjectId(),
                "transaction_id": tx_bob,
                "asked_of_name": "BOB",
                "status": "needed",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=1),
                "reminder_count": 0,
            },
        ],
        files=[
            {
                "_id": "park_ali",
                "original_filename": "early.jpg",
                "inbox_status": INBOX_UNMATCHED,
                "uploaded_at": now - timedelta(hours=2),
                "intended_ownership": "ALI",
                "transaction_ids": [],
                "security_rejected": False,
            },
            {
                "_id": file_id,
                "original_filename": "ok.pdf",
                "inbox_status": INBOX_APPROVED,
                "uploaded_at": now - timedelta(days=1),
                "transaction_ids": [tx_received],
                "security_rejected": False,
            },
            {
                "_id": "rej_ali",
                "original_filename": "bad.pdf",
                "inbox_status": INBOX_REJECTED,
                "uploaded_at": now - timedelta(hours=3),
                "uploaded_by": user_id,
                "transaction_ids": [],
                "rejection_reason": "blurry",
                "security_rejected": False,
            },
            {
                "_id": "park_bob",
                "original_filename": "bob.jpg",
                "inbox_status": INBOX_UNMATCHED,
                "uploaded_at": now - timedelta(hours=1),
                "intended_ownership": "BOB",
                "transaction_ids": [],
                "security_rejected": False,
            },
            {
                "_id": "matched_ali",
                "original_filename": "mid.pdf",
                "inbox_status": INBOX_MATCHED,
                "uploaded_at": now - timedelta(hours=4),
                "transaction_ids": [tx_needed],
                "security_rejected": False,
            },
        ],
    )

    dossier = await person_receipt_dossier(
        db,
        "ali",
        date_from=day - timedelta(days=1),
        date_to=day,
    )

    assert dossier["ownership"] == "ALI"
    assert dossier["person"] is not None
    assert dossier["person"]["name"] == "ALI"
    assert dossier["person"]["user_id"] == str(user_id)

    assert dossier["stats"]["still_needed"] == 1
    assert dossier["stats"]["received"] == 1
    assert dossier["stats"]["parked_uploads"] >= 1
    assert dossier["stats"]["rejected_files"] == 1
    assert dossier["stats"]["entries"] == 2
    assert dossier["stats"]["needed_requests"] == 1

    assert len(dossier["needed"]) == 1
    assert dossier["needed"][0]["ownership"] == "ALI"

    entry_ids = {row["id"] for row in dossier["entries"]}
    assert entry_ids == {str(tx_needed), str(tx_received)}

    submitted_ids = {row["id"] for row in dossier["submitted"]}
    assert file_id in submitted_ids
    assert "matched_ali" in submitted_ids
    assert "park_ali" not in submitted_ids

    parked_ids = {row["id"] for row in dossier["parked"]}
    assert "park_ali" in parked_ids
    assert "matched_ali" in parked_ids
    assert "park_bob" not in parked_ids

    rejected_ids = {row["id"] for row in dossier["rejected"]}
    assert rejected_ids == {"rej_ali"}


@pytest.mark.asyncio
async def test_person_receipt_dossier_requires_ownership() -> None:
    db = _db()
    with pytest.raises(ValueError, match="ownership"):
        await person_receipt_dossier(db, "  ")


def test_person_dossier_api(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    tx_id = ObjectId()
    db = _db(
        transactions=[
            {
                "_id": tx_id,
                "date": datetime(2026, 8, 10, 9, 0, 0),
                "amount": 1,
                "currency": "TZS",
                "item": "Fuel",
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
                "opened_at": now.replace(tzinfo=None),
                "reminder_count": 0,
            }
        ],
        files=[],
        people=[{"_id": ObjectId(), "name": "ALI", "active": True}],
    )
    app = create_app(settings=_settings(tmp_path), database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "desk",
        "role": "cashier",
    }
    with TestClient(app) as client:
        response = client.get(
            "/v1/receipts/people/ALI",
            params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ownership"] == "ALI"
    assert payload["stats"]["still_needed"] == 1
    assert len(payload["needed"]) == 1
    assert len(payload["entries"]) == 1
