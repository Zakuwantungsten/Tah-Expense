"""Analytics Data table — source monetary summary."""

from __future__ import annotations

from datetime import date, datetime

from bson import ObjectId
from fastapi.testclient import TestClient

from app.accountant_analytics_api import _data_table_window
from app.accountant_analytics_ops import SEPARATE_EXPENSE_FEEDS
from app.accountant_trends_ops import DIESEL_STATION_TRENDS, entity_cache_clear
from app.dependencies import current_user
from tests.test_accountant_overview_api import (
    FakeImportedFeeds,
    FakeSeparateExpenses,
    FakeTransactions,
    _app,
    _manager,
)


def test_data_table_window_defaults_and_swap() -> None:
    today = date.today()
    start, end, start_d, end_d = _data_table_window(None, None)
    assert start_d == date(today.year, 1, 1)
    assert end_d == today
    assert start.hour == 0 and end.hour == 23
    s, e, sd, ed = _data_table_window(date(2026, 6, 1), date(2026, 1, 15))
    assert sd == date(2026, 1, 15)
    assert ed == date(2026, 6, 1)
    assert s < e


def test_analytics_data_table_endpoint_shape() -> None:
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
            "category_name": "Fuel",
            "item": "Fuel",
            "description": "x",
            "receipt_status": "received",
        },
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2026, 3, 11),
            "amount": 80.0,
            "currency": "TZS",
            "category_name": "Diesel Cash",
            "item": "Diesel Cash",
            "description": "Cash fill 400 LTRS",
            "receipt_status": "received",
        },
    ]
    feeds = FakeImportedFeeds(
        [
            {
                "feed_type": "diesel_infinity",
                "transaction_date": datetime(2026, 3, 10),
                "ltrs": 50,
                "price_per_ltr": 2.0,
                "currency": "TZS",
            },
            {
                "feed_type": "parking_congo",
                "transaction_date": datetime(2026, 2, 5),
                "amount": 40.0,
                "currency": "USD",
                "is_deposit": True,
            },
            {
                "feed_type": "parking_congo",
                "transaction_date": datetime(2026, 2, 6),
                "amount": 15.0,
                "currency": "USD",
                "is_deposit": False,
            },
        ]
    )
    app = _app(
        FakeTransactions(docs),
        imported_feeds=feeds,
        separate_expenses=FakeSeparateExpenses([]),
    )
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        res = client.get(
            "/v1/accountant/analytics/data-table",
            params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ready"] is True
    assert body["date_from"] == "2026-01-01"
    assert body["date_to"] == "2026-12-31"
    keys = [row["key"] for row in body["sources"]]
    assert "diesel_cash" not in keys
    assert "master" in keys
    assert "infinity" in keys
    expected = 1 + len(SEPARATE_EXPENSE_FEEDS) + len(DIESEL_STATION_TRENDS)
    assert body["source_count"] == expected
    assert len(body["sources"]) == expected
    master = next(r for r in body["sources"] if r["key"] == "master")
    assert master["tzs"] == 180.0
    assert body["chart_year"] == 2026
    assert "rates_to_tzs" in body
    assert body["rates_to_tzs"]["TZS"] == 1.0
    assert float(body["rates_to_tzs"]["USD"]) > 0
    assert float(body["rates_to_tzs"]["ZMW"]) > 0
    assert body["fx_source"] in {"live", "fallback", "cache"}
    month_trend = body["month_trend"]
    assert len(month_trend) == 12
    assert [row["label"] for row in month_trend] == [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    march = next(row for row in month_trend if row["month"] == 3)
    # Master 100 + Diesel Cash 80 + Infinity 50×2
    assert march["tzs"] == 280.0
    feb = next(row for row in month_trend if row["month"] == 2)
    # Parking Congo spend only (deposit excluded)
    assert feb["usd"] == 15.0
    entity_cache_clear()


def test_analytics_data_table_spend_trend_uses_to_year() -> None:
    """Spend Trend is always Jan–Dec of the To date's year."""
    entity_cache_clear()
    docs = [
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2026, 1, 20),
            "amount": 40.0,
            "currency": "TZS",
            "category_name": "Fuel",
            "item": "Fuel",
            "description": "x",
            "receipt_status": "received",
        },
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2025, 12, 10),
            "amount": 25.0,
            "currency": "TZS",
            "category_name": "Fuel",
            "item": "Fuel",
            "description": "y",
            "receipt_status": "received",
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
            "/v1/accountant/analytics/data-table",
            params={"date_from": "2025-12-01", "date_to": "2026-01-31"},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["chart_year"] == 2026
    assert len(body["month_trend"]) == 12
    assert body["month_trend"][0]["label"] == "Jan"
    assert body["month_trend"][11]["label"] == "Dec"
    # 2025 spend is outside the chart year
    assert body["month_trend"][0]["tzs"] == 40.0
    assert sum(row["tzs"] for row in body["month_trend"]) == 40.0
    entity_cache_clear()


def test_analytics_landing_matches_data_table_all_sources() -> None:
    """Landing KPIs / source mix / trend use the same all-sources rollup as summary."""
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
            "category_name": "Fuel",
            "item": "Fuel",
            "description": "x",
            "receipt_status": "received",
        },
        {
            "_id": ObjectId(),
            "verified": True,
            "deletion_requested": False,
            "trashed": False,
            "date": datetime(2026, 3, 11),
            "amount": 200.0,
            "currency": "TZS",
            "category_name": "Supplier X",
            "item": "Parts",
            "description": "supplier",
            "receipt_status": "received",
        },
    ]
    feeds = FakeImportedFeeds(
        [
            {
                "feed_type": "diesel_infinity",
                "transaction_date": datetime(2026, 3, 10),
                "ltrs": 50,
                "price_per_ltr": 2.0,
                "currency": "TZS",
            },
            {
                "feed_type": "toll_plaza",
                "transaction_date": datetime(2026, 2, 5),
                "tender_amount": 30.0,
            },
        ]
    )
    separate = FakeSeparateExpenses(
        [
            {
                "expense_type": "congo_expenses",
                "expense_date": datetime(2026, 1, 15),
                "amount_usd": 25.0,
            }
        ]
    )
    from tests.test_accountant_overview_api import FakeCategories

    app = _app(
        FakeTransactions(docs),
        imported_feeds=feeds,
        separate_expenses=separate,
        categories=FakeCategories(
            [{"name": "Supplier X", "is_supplier": True, "active": True}]
        ),
    )
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        table = client.get(
            "/v1/accountant/analytics/data-table",
            params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
        )
        landing = client.get(
            "/v1/accountant/analytics",
            params={"year": 2026, "period": "year"},
        )

    assert table.status_code == 200, table.text
    assert landing.status_code == 200, landing.text
    totals = table.json()["totals"]
    kpis = landing.json()["kpis"]["totals"]
    assert kpis["tzs"] == totals["tzs"]
    assert kpis["usd"] == totals["usd"]
    assert kpis["zmw"] == totals["zmw"]
    # Master 300 + Infinity 100
    assert kpis["tzs"] == 400.0
    assert kpis["usd"] == 25.0
    assert kpis["zmw"] == 30.0

    mix = landing.json()["source_mix"]
    assert mix["master"]["tzs"] == 300.0
    assert mix["fuel"]["tzs"] == 100.0
    assert mix["separate"]["usd"] == 25.0
    assert mix["separate"]["zmw"] == 30.0

    names = {row["name"] for row in landing.json()["categories"]}
    assert "Infinity" in names
    assert "Congo Expenses" in names or "Toll Plaza" in names

    supplier_names = {row["name"] for row in landing.json()["suppliers"]}
    assert "Supplier X" in supplier_names
    assert "Infinity" in supplier_names

    march = next(
        row for row in landing.json()["month_trend"] if int(row["month"]) == 3
    )
    assert march["tzs"] == 400.0  # Master 300 + Infinity 100
    entity_cache_clear()
