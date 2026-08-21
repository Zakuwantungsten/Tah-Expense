"""Overview fuel-consumption line chart — litres over time, period buckets."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

from tahmeed.services.accountant_service import (
    accumulate_diesel_cash_fuel_liters,
    add_fuel_liters_day,
    bucket_daily_fuel_liters,
    empty_fuel_liters_daily,
    fuel_liters_period_spec,
    get_overview_fuel_liters,
)


TODAY = date(2026, 8, 21)


def _values(chart: dict, key: str) -> list[float]:
    return next(item["values"] for item in chart["series"] if item["key"] == key)


def test_month_period_is_days_of_current_month() -> None:
    spec = fuel_liters_period_spec("month", 2026, TODAY)
    assert spec["grain"] == "day"
    assert spec["start"] == date(2026, 8, 1)
    assert spec["end"] == TODAY
    assert spec["labels"][0] == "1"
    assert spec["labels"][-1] == "21"
    assert spec["keys"][-1] == TODAY


def test_three_month_period_is_weeks_clipped_to_fy() -> None:
    spec = fuel_liters_period_spec("3m", 2026, TODAY)
    assert spec["grain"] == "week"
    assert spec["start"] == date(2026, 6, 1)
    assert spec["end"] == TODAY
    assert spec["keys"][0] == date(2026, 6, 1)  # 1 Jun 2026 is a Monday
    assert spec["keys"][-1] == date(2026, 8, 17)


def test_year_period_is_months_through_today() -> None:
    spec = fuel_liters_period_spec("year", 2026, TODAY)
    assert spec["grain"] == "month"
    assert spec["labels"] == ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"]
    assert spec["keys"][-1] == date(2026, 8, 1)


def test_past_fy_month_uses_december() -> None:
    spec = fuel_liters_period_spec("month", 2025, TODAY)
    assert spec["start"] == date(2025, 12, 1)
    assert spec["end"] == date(2025, 12, 31)
    assert spec["labels"][-1] == "31"


def test_diesel_cash_liters_parse_from_description() -> None:
    store = empty_fuel_liters_daily()
    accumulate_diesel_cash_fuel_liters(
        [
            {"date": datetime(2026, 8, 10, 9, 0), "description": "DIESEL NAKONDE 400 LTRS"},
            {"date": datetime(2026, 8, 10, 15, 0), "description": "200 ltrs"},
            {"date": datetime(2026, 8, 11), "description": "LATRA"},
        ],
        store,
    )
    assert store["diesel_cash"][date(2026, 8, 10)] == 600.0
    assert date(2026, 8, 11) not in store["diesel_cash"]


def test_bucket_month_keeps_stations_and_diesel_cash_separate() -> None:
    store = empty_fuel_liters_daily()
    add_fuel_liters_day(store, "diesel_cash", date(2026, 8, 10), 400)
    add_fuel_liters_day(store, "infinity", date(2026, 8, 10), 250)
    add_fuel_liters_day(store, "lake_zambia", date(2026, 7, 1), 900)

    chart = bucket_daily_fuel_liters(store, "month", 2026, TODAY)
    labels = chart["labels"]
    day_10 = labels.index("10")
    assert _values(chart, "diesel_cash")[day_10] == 400
    assert _values(chart, "infinity")[day_10] == 250
    assert _values(chart, "lake_zambia")[day_10] == 0
    assert chart["total"] == 650


def test_bucket_year_sums_days_into_months() -> None:
    store = empty_fuel_liters_daily()
    add_fuel_liters_day(store, "gbp_diesel", date(2026, 8, 2), 100)
    add_fuel_liters_day(store, "gbp_diesel", date(2026, 8, 20), 50)
    add_fuel_liters_day(store, "gbp_diesel", date(2026, 5, 4), 25)

    chart = bucket_daily_fuel_liters(store, "year", 2026, TODAY)
    months = chart["labels"]
    assert _values(chart, "gbp_diesel")[months.index("Aug")] == 150
    assert _values(chart, "gbp_diesel")[months.index("May")] == 25
    assert chart["total"] == 175


def test_bucket_three_months_groups_by_week() -> None:
    store = empty_fuel_liters_daily()
    add_fuel_liters_day(store, "infinity", date(2026, 8, 10), 100)
    add_fuel_liters_day(store, "infinity", date(2026, 8, 12), 40)
    add_fuel_liters_day(store, "infinity", date(2026, 5, 20), 999)

    chart = bucket_daily_fuel_liters(store, "3m", 2026, TODAY)
    week_of_aug_10 = chart["keys"].index(date(2026, 8, 10))
    assert _values(chart, "infinity")[week_of_aug_10] == 140
    assert chart["total"] == 140


def test_january_three_months_does_not_leave_the_fy() -> None:
    spec = fuel_liters_period_spec("3m", 2026, date(2026, 1, 15))
    assert spec["start"] == date(2026, 1, 1)
    assert spec["end"] == date(2026, 1, 15)


def test_bucket_ignores_days_outside_selected_period() -> None:
    store = empty_fuel_liters_daily()
    add_fuel_liters_day(store, "lake_tunduma", date(2026, 1, 5), 80)
    add_fuel_liters_day(store, "lake_tunduma", date(2026, 8, 5), 20)

    month_chart = bucket_daily_fuel_liters(store, "month", 2026, TODAY)
    assert month_chart["total"] == 20
    year_chart = bucket_daily_fuel_liters(store, "year", 2026, TODAY)
    assert year_chart["total"] == 100


def test_get_overview_fuel_liters_merges_cash_and_stations(monkeypatch) -> None:
    cash = [
        {"date": datetime(2026, 8, 10), "description": "DIESEL 400 LTRS"},
    ]
    stations = [
        {
            "_id": {"feed": "diesel_infinity", "y": 2026, "m": 8, "d": 10},
            "liters": 250,
        },
        {
            "_id": {"feed": "diesel_lake_zambia", "y": 2026, "m": 8, "d": 12},
            "liters": 90,
        },
    ]

    class _Cursor:
        def __init__(self, docs):
            self._docs = docs

        async def to_list(self, length=None):
            return list(self._docs)

    db = SimpleNamespace(
        transactions=SimpleNamespace(find=lambda *a, **k: _Cursor(cash)),
        imported_feeds=SimpleNamespace(aggregate=lambda *a, **k: _Cursor(stations)),
    )
    monkeypatch.setattr(
        "tahmeed.services.accountant_service.get_db",
        lambda: db,
    )

    async def _names():
        return ["Diesel Cash"]

    monkeypatch.setattr(
        "tahmeed.services.accountant_service.get_diesel_cash_item_names",
        _names,
    )

    payload = asyncio.run(get_overview_fuel_liters(2026))
    series = payload["series"]
    assert series["diesel_cash"][date(2026, 8, 10)] == 400
    assert series["infinity"][date(2026, 8, 10)] == 250
    assert series["lake_zambia"][date(2026, 8, 12)] == 90
    assert series["lake_tunduma"] == {}
    assert series["gbp_diesel"] == {}
