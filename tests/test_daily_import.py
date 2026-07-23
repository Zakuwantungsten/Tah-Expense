"""Tests for daily Excel import helpers (receipt, amount, date detection)."""

from datetime import date, datetime

from tahmeed.services.daily_import_service import (
    detect_date_from_name,
    normalize_receipt,
    parse_amount,
    parse_date_value,
    pick_primary_date,
)


def test_normalize_receipt_receipt_word() -> None:
    assert normalize_receipt("RECEIPT") == "received"
    assert normalize_receipt("receipt") == "received"
    assert normalize_receipt("  Receipt  ") == "received"


def test_normalize_receipt_no_receipt() -> None:
    assert normalize_receipt("NO RECEIPT") == "no_receipt"
    assert normalize_receipt("no receipt") == "no_receipt"
    assert normalize_receipt("no_receipt") == "no_receipt"


def test_normalize_receipt_existing_aliases() -> None:
    assert normalize_receipt("received") == "received"
    assert normalize_receipt("pending") == "pending"
    assert normalize_receipt("missing") == "missing"
    assert normalize_receipt("") == "pending"


def test_parse_amount_plain_and_negative() -> None:
    assert parse_amount(10000) == 10000.0
    assert parse_amount(-10000) == -10000.0
    assert parse_amount("10,000") == 10000.0
    assert parse_amount("-10,000") == -10000.0


def test_parse_amount_parentheses() -> None:
    assert parse_amount("(10000)") == -10000.0
    assert parse_amount("(10,000)") == -10000.0
    assert parse_amount("( 1,234.50 )") == -1234.5


def test_parse_amount_blank() -> None:
    assert parse_amount(None) is None
    assert parse_amount("") is None
    assert parse_amount("—") is None


def test_detect_date_from_filename() -> None:
    assert detect_date_from_name("MATUMIZI YA 23-07-2026.xlsx") == date(2026, 7, 23)
    assert detect_date_from_name("23-07-2026") == date(2026, 7, 23)
    assert detect_date_from_name("no-date-here") is None


def test_pick_primary_date_majority() -> None:
    dates = [date(2026, 7, 21)] * 10 + [date(2026, 6, 28)] * 2
    assert pick_primary_date(dates, filename="MATUMIZI YA 23-07-2026.xlsx") == date(
        2026, 7, 21
    )


def test_pick_primary_date_filename_fallback() -> None:
    assert pick_primary_date(
        [], filename="MATUMIZI YA 23-07-2026.xlsx", sheet_name="Sheet1"
    ) == date(2026, 7, 23)


def test_parse_date_value() -> None:
    assert parse_date_value(datetime(2026, 7, 21)).date() == date(2026, 7, 21)
    assert parse_date_value("21/07/2026").date() == date(2026, 7, 21)
    assert parse_date_value("21-07-2026").date() == date(2026, 7, 21)
