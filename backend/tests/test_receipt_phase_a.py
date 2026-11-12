"""Phase A — request list presets + compliance KPIs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.receipt_ops import list_receipt_requests, receipt_compliance_summary


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
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


class FakeColl:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

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


def _db(*, requests=None, transactions=None, files=None):
    return SimpleNamespace(
        receipt_requests=FakeColl(requests),
        transactions=FakeColl(transactions),
        receipt_files=FakeColl(files),
    )


async def test_list_presets_today_open_overdue() -> None:
    now = datetime.now(UTC)
    tx_id = ObjectId()
    db = _db(
        transactions=[
            {
                "_id": tx_id,
                "amount": 1000,
                "currency": "TZS",
                "item": "LATRA",
                "payee": "X",
                "ownership": "ALI",
            }
        ],
        requests=[
            {
                "_id": ObjectId(),
                "status": "needed",
                "asked_of_name": "ALI",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=2),
                "reminder_count": 0,
                "transaction_id": tx_id,
            },
            {
                "_id": ObjectId(),
                "status": "needed",
                "asked_of_name": "BETA",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=80),
                "reminder_count": 4,
                "transaction_id": ObjectId(),
            },
            {
                "_id": ObjectId(),
                "status": "received",
                "asked_of_name": "ALI",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=1),
                "reminder_count": 0,
            },
        ],
    )
    today = await list_receipt_requests(db, preset="today")
    assert len(today) >= 2
    open_rows = await list_receipt_requests(db, preset="open")
    assert {r["ownership"] for r in open_rows} == {"ALI", "BETA"}
    overdue = await list_receipt_requests(db, preset="overdue", overdue_hours=48)
    assert len(overdue) == 1
    assert overdue[0]["ownership"] == "BETA"
    heavy = await list_receipt_requests(db, preset="heavy_reminders", reminder_threshold=3)
    assert len(heavy) == 1
    assert heavy[0]["reminder_count"] == 4


async def test_compliance_summary_counts() -> None:
    now = datetime.now(UTC)
    db = _db(
        requests=[
            {
                "_id": ObjectId(),
                "status": "needed",
                "asked_of_name": "ALI",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=1),
                "reminder_count": 0,
            },
            {
                "_id": ObjectId(),
                "status": "needed",
                "asked_of_name": "BETA",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=60),
                "reminder_count": 5,
            },
            {
                "_id": ObjectId(),
                "status": "received",
                "asked_of_name": "ALI",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=3),
                "reminder_count": 1,
            },
            {
                "_id": ObjectId(),
                "status": "waived",
                "asked_of_name": "GAMMA",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=4),
                "reminder_count": 0,
            },
        ],
        files=[{"_id": "f1", "inbox_status": "unmatched"}],
    )
    summary = await receipt_compliance_summary(db, overdue_hours=48, reminder_threshold=3)
    assert summary["still_needed"] == 2
    assert summary["overdue"] == 1
    assert summary["heavy_reminders"] == 1
    assert summary["received"] == 1
    assert summary["waived"] == 1
    assert summary["unmatched_uploads"] == 1
    assert summary["today_requested"] >= 1


def test_compliance_api(tmp_path) -> None:
    now = datetime.now(UTC)
    db = _db(
        requests=[
            {
                "_id": ObjectId(),
                "status": "needed",
                "asked_of_name": "ALI",
                "opened_at": now.replace(tzinfo=None) - timedelta(hours=3),
                "reminder_count": 0,
            }
        ],
        files=[],
    )
    settings = Settings(
        mongodb_uri="mongodb://127.0.0.1:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        attachments_directory=tmp_path,
        backup_s3_bucket="",
    )
    app = create_app(settings=settings, database=SimpleNamespace(db=db))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "acct",
        "role": "accountant",
    }
    with TestClient(app) as client:
        comp = client.get("/v1/receipts/compliance")
        reqs = client.get("/v1/receipts/requests", params={"preset": "open"})
    assert comp.status_code == 200
    assert comp.json()["still_needed"] == 1
    assert reqs.status_code == 200
    assert reqs.json()["total"] == 1
