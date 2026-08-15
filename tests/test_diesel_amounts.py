"""Diesel station amounts — litres × rate, never the Excel total."""

from tahmeed.services.diesel_amounts import (
    apply_diesel_computed_fields,
    diesel_line_total,
    parse_diesel_number,
)


def test_parse_plain_and_comma_numbers() -> None:
    assert parse_diesel_number("400") == 400.0
    assert parse_diesel_number("1,164,000") == 1164000.0
    assert parse_diesel_number("1,5") == 1.5
    assert parse_diesel_number("1,164.50") == 1164.5
    assert parse_diesel_number("2,910") == 2910.0
    assert parse_diesel_number(2910) == 2910.0
    assert parse_diesel_number("") is None
    assert parse_diesel_number("—") is None


def test_line_total_is_litres_times_rate() -> None:
    assert diesel_line_total("400", "2910") == 1164000.0
    assert diesel_line_total(400, 2910) == 1164000.0
    assert diesel_line_total("400", "") == 0.0
    assert diesel_line_total("", "2910") == 0.0


def test_excel_sn_and_amount_are_replaced() -> None:
    rec = {
        "sn": "309",
        "ltrs": "400",
        "price_per_ltr": "2910",
        "total_amount": "999",
    }
    apply_diesel_computed_fields(rec)
    assert rec["sn"] == ""
    assert rec["ltrs"] == 400.0
    assert rec["price_per_ltr"] == 2910.0
    assert rec["total_amount"] == 1164000.0
    assert rec["total_amount"] != 999
