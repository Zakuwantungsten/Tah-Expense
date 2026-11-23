"""Analytics Meetings — Master App by / Ownership aggregations."""

from __future__ import annotations

from datetime import date, datetime

from bson import ObjectId
from fastapi.testclient import TestClient

from app.accountant_trends_ops import (
    build_meetings_month_trend,
    build_meetings_people_rows,
    compliance_pct,
    entity_cache_clear,
    normalize_meetings_dimension,
    receipt_needed_and_received,
)
from app.dependencies import current_user
from tests.test_accountant_overview_api import (
    FakeImportedFeeds,
    FakeSeparateExpenses,
    FakeTransactions,
    _app,
    _manager,
)


def test_normalize_and_receipt_helpers() -> None:
    assert normalize_meetings_dimension("ownership") == "ownership"
    assert normalize_meetings_dimension("bogus") == "approver"
    assert receipt_needed_and_received("received") == (True, True)
    assert receipt_needed_and_received("no_receipt") == (False, False)
    assert receipt_needed_and_received("pending") == (True, False)
    assert compliance_pct(1, 2) == 0.5
    assert compliance_pct(0, 0) is None


def test_build_meetings_people_rows_sn_and_compliance() -> None:
    table = build_meetings_people_rows(
        [
            {
                "name": "Alice",
                "tzs": 100.0,
                "usd": 0.0,
                "zmw": 0.0,
                "count": 2,
                "receipts_needed": 2,
                "receipts_received": 1,
            },
            {
                "name": "",
                "tzs": 50.0,
                "usd": 0.0,
                "zmw": 0.0,
                "count": 1,
                "receipts_needed": 0,
                "receipts_received": 0,
            },
        ]
    )
    assert [r["name"] for r in table["people"]] == ["Alice", "Unassigned"]
    assert table["people"][0]["sn"] == 1
    assert table["people"][0]["compliance_pct"] == 0.5
    assert table["people"][1]["compliance_pct"] is None
    assert table["chart_people"] == ["Alice", "Unassigned"]


def test_build_meetings_month_trend_person_series() -> None:
    trend = build_meetings_month_trend(
        [
            {"year": 2026, "month": 3, "name": "Alice", "tzs": 40.0},
            {"year": 2026, "month": 3, "name": "Bob", "tzs": 10.0},
            {"year": 2026, "month": 4, "name": "Alice", "tzs": 5.0},
        ],
        chart_people=["Alice", "Bob"],
        chart_year=2026,
    )
    assert len(trend) == 12
    assert trend[0]["label"] == "Jan"
    assert trend[2]["series"]["Alice"] == 40.0
    assert trend[2]["series"]["Bob"] == 10.0
    assert trend[3]["series"]["Alice"] == 5.0
    assert trend[3]["series"]["Bob"] == 0.0


def test_analytics_meetings_endpoint_default_approver() -> None:
    entity_cache_clear()
    docs = [
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2026, 3, 10),
            "amount": 100.0,
            "currency": "TZS",
            "approver": "Alice",
            "ownership": "Team A",
            "receipt_status": "received",
            "category_name": "Fuel",
            "item": "Fuel",
        },
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2026, 3, 11),
            "amount": 50.0,
            "currency": "TZS",
            "approver": "Alice",
            "ownership": "Team A",
            "receipt_status": "pending",
            "category_name": "Fuel",
            "item": "Fuel",
        },
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2026, 4, 5),
            "amount": 80.0,
            "currency": "TZS",
            "approver": "Bob",
            "ownership": "Team B",
            "receipt_status": "no_receipt",
            "category_name": "Travel",
            "item": "Travel",
        },
    ]
    app = _app(
        FakeTransactions(docs),
        imported_feeds=FakeImportedFeeds([]),
        separate_expenses=FakeSeparateExpenses([]),
    )
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        res = client.get(
            "/v1/accountant/analytics/meetings",
            params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dimension"] == "approver"
    assert body["dimension_label"] == "App by"
    assert body["chart_year"] == 2026
    assert body["person_count"] == 2
    names = [r["name"] for r in body["people"]]
    assert names == ["Alice", "Bob"]
    alice = body["people"][0]
    assert alice["sn"] == 1
    assert alice["tzs"] == 150.0
    assert alice["count"] == 2
    assert alice["receipts_needed"] == 2
    assert alice["receipts_received"] == 1
    assert alice["compliance_pct"] == 0.5
    bob = body["people"][1]
    assert bob["tzs"] == 80.0
    assert bob["receipts_needed"] == 0
    assert bob["compliance_pct"] is None
    assert len(body["month_trend"]) == 12
    march = body["month_trend"][2]
    assert march["series"]["Alice"] == 150.0
    assert "rates_to_tzs" in body
    entity_cache_clear()


def test_analytics_meetings_ownership_and_person_filter() -> None:
    entity_cache_clear()
    docs = [
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2026, 2, 1),
            "amount": 30.0,
            "currency": "TZS",
            "approver": "Alice",
            "ownership": "Team A",
            "receipt_status": "received",
            "category_name": "Fuel",
            "item": "Fuel",
        },
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2026, 2, 2),
            "amount": 70.0,
            "currency": "TZS",
            "approver": "Bob",
            "ownership": "Team B",
            "receipt_status": "received",
            "category_name": "Fuel",
            "item": "Fuel",
        },
    ]
    app = _app(
        FakeTransactions(docs),
        imported_feeds=FakeImportedFeeds([]),
        separate_expenses=FakeSeparateExpenses([]),
    )
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        by_own = client.get(
            "/v1/accountant/analytics/meetings",
            params={
                "date_from": "2026-01-01",
                "date_to": "2026-06-30",
                "dimension": "ownership",
            },
        )
        filtered = client.get(
            "/v1/accountant/analytics/meetings",
            params={
                "date_from": "2026-01-01",
                "date_to": "2026-06-30",
                "dimension": "approver",
                "person": "Alice",
            },
        )

    assert by_own.status_code == 200, by_own.text
    own_body = by_own.json()
    assert own_body["dimension"] == "ownership"
    assert [r["name"] for r in own_body["people"]] == ["Team B", "Team A"]

    assert filtered.status_code == 200, filtered.text
    filt = filtered.json()
    assert filt["person_count"] == 1
    assert filt["people"][0]["name"] == "Alice"
    assert filt["people"][0]["tzs"] == 30.0
    entity_cache_clear()
