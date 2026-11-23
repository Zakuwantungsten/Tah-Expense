"""Overview Sources families + TZS conversion helpers."""

from __future__ import annotations

from app.accountant_analytics_ops import SEPARATE_EXPENSE_FEEDS
from app.accountant_overview_ops import (
    FUEL_LITERS_SERIES,
    SOURCE_FAMILIES,
    SOURCE_FAMILY_FEED_KEYS,
    accumulate_currency_amount,
    add_currency_buckets,
    build_fuel_spend_rows,
    build_source_pie_slices,
    build_source_rows,
    empty_currency_bucket,
    feed_amounts_to_bucket,
    subtract_currency_buckets,
)
from app.fx_rates import clear_fx_cache, convert_to_tzs, get_rates_to_tzs


def test_build_source_rows_converts_all_to_tzs_and_sorts() -> None:
    rates = {"TZS": 1.0, "USD": 2500.0, "ZMW": 100.0}
    rows = build_source_rows(
        {
            "master": {"tzs": 1_000_000.0, "usd": 0.0, "zmw": 0.0},
            "fuel": {"tzs": 100_000.0, "usd": 10.0, "zmw": 0.0},
            "toll_zambia": {"tzs": 0.0, "usd": 0.0, "zmw": 500.0},
            "congo": {"tzs": 0.0, "usd": 40.0, "zmw": 0.0},
            "afritrack": {"tzs": 0.0, "usd": 12.0, "zmw": 0.0},
            "insurance": {"tzs": 50_000.0, "usd": 0.0, "zmw": 0.0},
            "sm_burhani": {"tzs": 0.0, "usd": 5.0, "zmw": 0.0},
            "rahntech": {"tzs": 0.0, "usd": 8.0, "zmw": 0.0},
        },
        rates,
    )
    assert {row["key"] for row in rows} == {spec.key for spec in SOURCE_FAMILIES}
    by_key = {row["key"]: row for row in rows}
    assert by_key["master"]["amount_tzs"] == 1_000_000.0
    assert by_key["fuel"]["amount_tzs"] == 100_000.0 + 10.0 * 2500.0
    assert by_key["toll_zambia"]["amount_tzs"] == 500.0 * 100.0
    assert by_key["congo"]["amount_tzs"] == 40.0 * 2500.0
    assert by_key["congo"]["currency"] == "TZS"
    assert rows[0]["amount_tzs"] >= rows[-1]["amount_tzs"]
    assert all(row["currency"] == "TZS" for row in rows)


def test_source_families_cover_all_separate_expense_tabs() -> None:
    covered = {key for keys in SOURCE_FAMILY_FEED_KEYS.values() for key in keys}
    expected = {spec.key for spec in SEPARATE_EXPENSE_FEEDS}
    assert covered == expected


def test_master_minus_diesel_cash_and_feed_buckets() -> None:
    master = {"tzs": 500.0, "usd": 20.0, "zmw": 0.0}
    diesel = {"tzs": 80.0, "usd": 0.0, "zmw": 0.0}
    assert subtract_currency_buckets(master, diesel) == {
        "tzs": 420.0,
        "usd": 20.0,
        "zmw": 0.0,
    }
    feeds = [
        {"key": "toll_plaza", "currency": "ZMW", "amount": 30.0},
        {"key": "zambia_parking", "currency": "ZMW", "amount": 10.0},
        {"key": "congo_exp", "currency": "USD", "amount": 5.0},
    ]
    assert feed_amounts_to_bucket(feeds, ("toll_plaza", "zambia_parking")) == {
        "tzs": 0.0,
        "usd": 0.0,
        "zmw": 40.0,
    }
    assert add_currency_buckets(
        {"tzs": 1.0, "usd": 0.0, "zmw": 0.0},
        {"tzs": 2.0, "usd": 3.0, "zmw": 0.0},
    ) == {"tzs": 3.0, "usd": 3.0, "zmw": 0.0}


def test_build_source_pie_slices_master_separate_stations_no_diesel_cash() -> None:
    slices = build_source_pie_slices(
        master={"tzs": 500.0, "usd": 20.0, "zmw": 0.0},
        separate_feeds=[
            {"key": "toll_plaza", "label": "Toll Plaza", "currency": "ZMW", "amount": 30.0},
            {"key": "congo_exp", "label": "Congo Expenses", "currency": "USD", "amount": 5.0},
            {"key": "parking_congo", "label": "Parking Congo", "currency": "USD", "amount": 0.0},
        ],
        station_buckets={
            "infinity": {"tzs": 200.0, "usd": 0.0, "zmw": 0.0},
            "lake_zambia": {"tzs": 0.0, "usd": 10.0, "zmw": 0.0},
            "lake_tunduma": empty_currency_bucket(),
            "gbp_diesel": empty_currency_bucket(),
        },
        top_n=10,
    )
    names = [row["name"] for row in slices]
    assert "Master Expenses" in names
    assert "Diesel Cash" not in names
    assert "Toll Plaza" in names
    assert "Congo Expenses" in names
    assert "Infinity" in names
    assert "Lake Zambia" in names
    assert "Parking Congo" not in names  # zero amount dropped
    by_name = {row["name"]: row for row in slices}
    assert by_name["Master Expenses"]["tzs"] == 500.0
    assert by_name["Master Expenses"]["usd"] == 20.0
    assert by_name["Toll Plaza"]["zmw"] == 30.0
    assert by_name["Congo Expenses"]["usd"] == 5.0
    assert by_name["Infinity"]["tzs"] == 200.0


def test_build_source_pie_slices_folds_other() -> None:
    slices = build_source_pie_slices(
        master={"tzs": 100.0, "usd": 0.0, "zmw": 0.0},
        separate_feeds=[
            {"key": "a", "label": "A", "currency": "TZS", "amount": 50.0},
            {"key": "b", "label": "B", "currency": "TZS", "amount": 40.0},
            {"key": "c", "label": "C", "currency": "TZS", "amount": 30.0},
        ],
        station_buckets={
            "infinity": {"tzs": 20.0, "usd": 0.0, "zmw": 0.0},
            "lake_zambia": empty_currency_bucket(),
            "lake_tunduma": empty_currency_bucket(),
            "gbp_diesel": empty_currency_bucket(),
        },
        top_n=2,
    )
    names = [row["name"] for row in slices]
    assert names[:2] == ["Master Expenses", "A"]
    assert names[-1] == "Other"
    other = next(r for r in slices if r["name"] == "Other")
    assert other["tzs"] == 40.0 + 30.0 + 20.0


def test_build_fuel_spend_rows_per_station_to_tzs() -> None:
    rates = {"TZS": 1.0, "USD": 2500.0, "ZMW": 100.0}
    rows = build_fuel_spend_rows(
        {
            "diesel_cash": {"tzs": 500_000.0, "usd": 0.0, "zmw": 0.0},
            "infinity": {"tzs": 200_000.0, "usd": 0.0, "zmw": 0.0},
            "lake_zambia": {"tzs": 0.0, "usd": 40.0, "zmw": 0.0},
            "lake_tunduma": {"tzs": 0.0, "usd": 0.0, "zmw": 0.0},
            "gbp_diesel": {"tzs": 10_000.0, "usd": 0.0, "zmw": 0.0},
        },
        rates,
    )
    assert [row["key"] for row in rows]  # sorted by amount
    by_key = {row["key"]: row for row in rows}
    assert set(by_key) == {key for key, _ in FUEL_LITERS_SERIES}
    assert by_key["lake_zambia"]["amount_tzs"] == 40.0 * 2500.0
    assert by_key["diesel_cash"]["amount_tzs"] == 500_000.0
    assert all(row["currency"] == "TZS" for row in rows)
    assert rows[0]["amount_tzs"] >= rows[-1]["amount_tzs"]


def test_accumulate_currency_amount_aliases() -> None:
    bucket = empty_currency_bucket()
    accumulate_currency_amount(bucket, "TSH", 10)
    accumulate_currency_amount(bucket, "USD", 2)
    accumulate_currency_amount(bucket, "ZK", 5)
    accumulate_currency_amount(bucket, "", 3)  # blank → TZS
    assert bucket == {"tzs": 13.0, "usd": 2.0, "zmw": 5.0}


def test_convert_to_tzs_aliases() -> None:
    rates = {"TZS": 1.0, "USD": 2000.0, "ZMW": 80.0}
    assert convert_to_tzs(10, "USD", rates) == 20_000.0
    assert convert_to_tzs(10, "ZK", rates) == 800.0
    assert convert_to_tzs(10, "TSH", rates) == 10.0


def test_get_rates_to_tzs_fallback_when_fetch_fails(monkeypatch) -> None:
    clear_fx_cache()

    def _boom() -> dict[str, float]:
        raise RuntimeError("offline")

    monkeypatch.setattr("app.fx_rates._fetch_usd_rates", _boom)
    payload = get_rates_to_tzs(force_refresh=True)
    assert payload["source"] == "fallback"
    assert payload["rates"]["TZS"] == 1.0
    assert payload["rates"]["USD"] > 0
    assert payload["rates"]["ZMW"] > 0
    clear_fx_cache()
