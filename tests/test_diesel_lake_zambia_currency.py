"""Lake Zambia diesel uploads — per-batch USD/ZMW currency."""

from tahmeed.services.diesel_currency import (
    LAKE_ZAMBIA_DIESEL_CURRENCIES,
    diesel_amount_by_currency_groups,
    diesel_record_currency,
)


def test_lake_zambia_currencies_are_usd_and_zmw_only() -> None:
    assert LAKE_ZAMBIA_DIESEL_CURRENCIES == frozenset({"USD", "ZMW"})


def test_diesel_record_currency_lake_zambia_stored() -> None:
    assert diesel_record_currency({"currency": "ZMW"}, "diesel_lake_zambia") == "ZMW"
    assert diesel_record_currency({"currency": "USD"}, "diesel_lake_zambia") == "USD"


def test_diesel_record_currency_lake_zambia_legacy_defaults_usd() -> None:
    assert diesel_record_currency({}, "diesel_lake_zambia") == "USD"
    assert diesel_record_currency({"currency": ""}, "diesel_lake_zambia") == "USD"
    assert diesel_record_currency({"currency": "TZS"}, "diesel_lake_zambia") == "USD"


def test_diesel_record_currency_other_stations_unchanged() -> None:
    assert diesel_record_currency({}, "diesel_infinity") == "TZS"
    assert diesel_record_currency({}, "diesel_gbp") is None
    assert diesel_record_currency({}, "diesel_lake_tunduma") is None


def test_diesel_amount_by_currency_groups() -> None:
    groups = [
        {"_id": "USD", "amount": 100.0},
        {"_id": "ZMW", "amount": 2500.0},
        {"_id": None, "amount": 50.0},
    ]
    assert diesel_amount_by_currency_groups(groups) == {
        "USD": 150.0,
        "ZMW": 2500.0,
    }


def test_fuel_overview_zmw_lake_zambia_row() -> None:
    from tahmeed.services.accountant_service import overview_row_display

    row = {
        "date": None,
        "source": "Lake Zambia Diesel",
        "description": "NDOLA",
        "reference": "LPO-1",
        "truck_value": "T102",
        "currency": "ZMW",
        "amount": 1800.0,
        "liters": 400,
        "rate": 4.5,
        "station": "LAKE NDOLA",
        "upload_description": "March ZMW batch",
        "receipt_status": "—",
    }
    assert overview_row_display(row, "zmw") == "1,800"
    assert overview_row_display(row, "usd") == "—"
