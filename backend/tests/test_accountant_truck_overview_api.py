"""Phase 9.1 — truck overview API."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


def _as_naive(value):
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(doc, clause) for clause in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, clause) for clause in expected):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$regex" in expected:
                import re

                flags = re.I if "i" in str(expected.get("$options", "")) else 0
                if actual is None or re.search(
                    expected["$regex"], str(actual), flags
                ) is None:
                    return False
                continue
            if "$gte" in expected or "$lte" in expected or "$lt" in expected:
                if actual is None:
                    return False
                left = _as_naive(actual)
                if "$gte" in expected and left < _as_naive(expected["$gte"]):
                    return False
                if "$lte" in expected and left > _as_naive(expected["$lte"]):
                    return False
                if "$lt" in expected and left >= _as_naive(expected["$lt"]):
                    return False
            continue
        if actual != expected:
            return False
    return True

class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = list(documents)

    def sort(self, *_a: object, **_k: object) -> "FakeCursor":
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        if length is None:
            return list(self.documents)
        return list(self.documents)[:length]


class FakeColl:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        return FakeCursor([d for d in self.documents if _matches(d, query)])

    async def find_one(self, query: dict) -> dict | None:
        for d in self.documents:
            if _matches(d, query):
                return dict(d)
        return None


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(
    *,
    transactions: list[dict] | None = None,
    feeds: list[dict] | None = None,
    separate: list[dict] | None = None,
    recon: list[dict] | None = None,
):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=FakeColl(transactions),
            imported_feeds=FakeColl(feeds),
            separate_expenses=FakeColl(separate),
            reconciliation_entries=FakeColl(recon),
            system_settings=FakeColl(),
        )
    )
    return create_app(settings=settings(), database=database)


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct", "role": "accountant"}


def test_truck_overview_records_count_summary() -> None:
    app = _app(
        transactions=[
            {
                "_id": ObjectId(),
                "verified": True,
                "truck_number": "T103 DVL",
                "category_name": "Parts",
                "item": "Bolt",
                "description": "repair",
                "currency": "TZS",
                "amount": 100.0,
                "date": datetime(2026, 3, 10),
            },
            {
                "_id": ObjectId(),
                "verified": True,
                "truck_number": "T103 DVL",
                "category_name": "Diesel Cash",
                "item": "Diesel Cash",
                "description": "50 ltrs",
                "currency": "TZS",
                "amount": 200.0,
                "date": datetime(2026, 3, 11),
            },
        ],
        feeds=[
            {
                "_id": ObjectId(),
                "feed_type": "toll_plaza",
                "vehicle_reg": "T103 DVL",
                "toll_plaza": "NAKONDE",
                "tender_amount": 40.0,
                "transaction_date": datetime(2026, 3, 12),
            },
            {
                "_id": ObjectId(),
                "feed_type": "diesel_infinity",
                "truck_no": "T103 DVL",
                "ltrs": 10,
                "price_per_ltr": 2,
                "transaction_date": datetime(2026, 3, 13),
            },
        ],
        separate=[
            {
                "_id": ObjectId(),
                "expense_type": "congo_expenses",
                "truck_no": "T103 DVL",
                "description": "border",
                "amount_usd": 15.0,
                "expense_date": datetime(2026, 3, 14),
            }
        ],
    )
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        records = client.get(
            "/v1/accountant/truck-overview/records",
            params={"truck": "T103 DVL"},
        )
        count = client.get(
            "/v1/accountant/truck-overview/records/count",
            params={"truck": "T103 DVL"},
        )
        summary = client.get(
            "/v1/accountant/truck-overview/summary",
            params={"truck": "T103 DVL"},
        )
        master_only = client.get(
            "/v1/accountant/truck-overview/records",
            params={"truck": "T103 DVL", "source": "master"},
        )
        diesel_imports = client.get(
            "/v1/accountant/truck-overview/records/count",
            params={"truck": "T103 DVL", "source": "diesel_imports"},
        )

    assert records.status_code == 200
    assert len(records.json()) == 5
    assert count.json()["count"] == 5
    body = summary.json()
    assert body["record_count"] == 5
    assert body["tzs_total"] == 100.0 + 200.0 + 20.0  # master + diesel cash + infinity
    assert body["zmw_total"] == 40.0
    assert body["usd_total"] == 15.0
    assert body["liters_total"] == 50.0 + 10.0
    assert len(master_only.json()) == 1
    assert master_only.json()[0]["source_group"] == "master"
    assert diesel_imports.json()["count"] == 1


def test_truck_overview_empty_truck() -> None:
    app = _app()
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        r = client.get(
            "/v1/accountant/truck-overview/records",
            params={"truck": "   "},
        )
    # FastAPI min_length=1 after strip? Query doesn't strip; spaces pass.
    # Our handler returns [] for blank strip.
    assert r.status_code in (200, 422)


def test_truck_overview_accepts_tz_aware_dates_with_range() -> None:
    app = _app(
        transactions=[
            {
                "_id": ObjectId(),
                "verified": True,
                "truck_number": "T103 DVL",
                "category_name": "Parts",
                "item": "Bolt",
                "description": "repair",
                "currency": "TZS",
                "amount": 100.0,
                "date": datetime(2026, 3, 10, tzinfo=timezone.utc),
            }
        ],
    )
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        response = client.get(
            "/v1/accountant/truck-overview/records",
            params={
                "truck": "T103 DVL",
                "date_from": "2026-01-01T00:00:00Z",
                "date_to": "2026-12-31T23:59:59Z",
            },
        )
        summary = client.get(
            "/v1/accountant/truck-overview/summary",
            params={
                "truck": "T103 DVL",
                "date_from": "2026-01-01T00:00:00Z",
                "date_to": "2026-12-31T23:59:59Z",
            },
        )
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert summary.status_code == 200
    assert summary.json()["record_count"] == 1
