"""Phase 9.2 — fuel overview API."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


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
            if "$gte" in expected or "$lte" in expected:
                if actual is None:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
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
        return list(self.documents)


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


def _app(*, transactions=None, feeds=None):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=FakeColl(transactions),
            imported_feeds=FakeColl(feeds),
            system_settings=FakeColl(),
        )
    )
    return create_app(settings=settings(), database=database)


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct", "role": "accountant"}


def test_fuel_overview_records_summary_filters() -> None:
    app = _app(
        transactions=[
            {
                "_id": ObjectId(),
                "verified": True,
                "truck_number": "T103 DVL",
                "category_name": "Diesel Cash",
                "item": "Diesel Cash",
                "description": "100 ltrs station",
                "currency": "TZS",
                "amount": 500.0,
                "date": datetime(2026, 4, 1),
            }
        ],
        feeds=[
            {
                "_id": ObjectId(),
                "feed_type": "diesel_infinity",
                "truck_no": "T103 DVL",
                "ltrs": 20,
                "price_per_ltr": 3,
                "destinations": "DAR",
                "transaction_date": datetime(2026, 4, 2),
                "upload_label": "Apr batch",
            }
        ],
    )
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        records = client.get(
            "/v1/accountant/fuel-overview/records",
            params={"truck": "T103 DVL"},
        )
        summary = client.get(
            "/v1/accountant/fuel-overview/summary",
            params={"truck": "T103 DVL"},
        )
        cash_only = client.get(
            "/v1/accountant/fuel-overview/records/count",
            params={"truck": "T103 DVL", "source": "diesel_cash"},
        )
        filtered = client.get(
            "/v1/accountant/fuel-overview/records",
            params={
                "truck": "T103 DVL",
                "column_filters": json.dumps({"source": ["Infinity Diesel"]}),
            },
        )
        cache = client.get(
            "/v1/accountant/fuel-overview/column-filters",
            params={"truck": "T103 DVL"},
        )

    assert records.status_code == 200
    assert len(records.json()) == 2
    body = summary.json()
    assert body["record_count"] == 2
    assert body["tzs_total"] == 500.0 + 60.0
    assert body["liters_total"] == 100.0 + 20.0
    assert cash_only.json()["count"] == 1
    assert len(filtered.json()) == 1
    assert filtered.json()[0]["source"] == "Infinity Diesel"
    assert cache.status_code == 200
    assert "Infinity Diesel" in cache.json()["filters"]["source"]
    assert "Diesel Cash" in cache.json()["filters"]["source"]
