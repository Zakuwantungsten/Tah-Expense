"""Tests for Excel serial / spreadsheet date parsing."""

from datetime import date, datetime

from tahmeed.services.excel_dates import (
    excel_serial_to_datetime,
    format_excel_date,
    normalize_date_fields,
    parse_excel_date,
)
from tahmeed.ui.accountant.separate_expenses import _parse_zambia_parking_sheet


def test_excel_serial_week19_2026() -> None:
    # From TAHMEED PREPAID ACCOUNT STATEMENT WEEK 19 2026
    assert parse_excel_date(46146).date() == date(2026, 5, 4)
    assert parse_excel_date(46147).date() == date(2026, 5, 5)
    assert parse_excel_date("46146").date() == date(2026, 5, 4)
    assert parse_excel_date(46146.0).date() == date(2026, 5, 4)


def test_parse_excel_date_common_forms() -> None:
    assert parse_excel_date(datetime(2026, 5, 4, 15, 30)).hour == 15
    assert parse_excel_date(date(2026, 5, 4)) == datetime(2026, 5, 4)
    assert parse_excel_date("04 May 2026").date() == date(2026, 5, 4)
    assert parse_excel_date("04/05/2026").date() == date(2026, 5, 4)
    assert parse_excel_date("2026-05-04").date() == date(2026, 5, 4)
    assert parse_excel_date(None) is None
    assert parse_excel_date("") is None
    assert parse_excel_date("n/a") is None


def test_serial_range_rejects_small_ints() -> None:
    assert excel_serial_to_datetime(1) is None
    assert excel_serial_to_datetime(100) is None
    assert parse_excel_date(42) is None


def test_format_excel_date_serial() -> None:
    assert format_excel_date(46146) == "04 May 2026"
    assert format_excel_date("46146") == "04 May 2026"


def test_normalize_date_fields_rewrites_display() -> None:
    doc = {"date": "46146"}
    parsed = normalize_date_fields(doc, "date", store_as="transaction_date")
    assert parsed is not None
    assert parsed.date() == date(2026, 5, 4)
    assert doc["date"] == "04 May 2026"
    assert doc["transaction_date"] == parsed


def test_zambia_parking_sheet_converts_serial_dates() -> None:
    class _FakeWs:
        def iter_rows(self, values_only=True):
            return [
                ("Tahmeed Coach Tz", None, None, None, None, None, None, None),
                ("Date", "Type", "Plate Num.", "Ticket No.", "Debit", "Credit", "Balance", "Heading To"),
                (46146, "Truck", "T638EAF", 49319, 100, None, 52200, "DRC"),
                (46147, "Closing Balance ", None, None, None, None, 52000, None),
            ]

    records = _parse_zambia_parking_sheet(_FakeWs(), "Week19")
    assert len(records) == 2
    assert records[0]["date"] == "04 May 2026"
    assert records[0]["transaction_date"] == datetime(2026, 5, 4)
    assert records[0]["plate_num"] == "T638EAF"
    assert records[0]["ticket_no"] == "49319"  # ticket stays numeric string
    assert records[1]["date"] == "05 May 2026"
    assert records[1]["is_balance_row"] is True


def test_zambia_parking_real_week19_file() -> None:
    from pathlib import Path

    import openpyxl

    path = Path(__file__).resolve().parents[1] / (
        "TAHMEED PREPAID ACCOUNT STATEMENT WEEK 19 2026 (2).xlsx"
    )
    if not path.exists():
        return

    from tahmeed.ui.accountant.separate_expenses import _parse_zambia_last_sheet

    label, records = _parse_zambia_last_sheet(str(path))
    assert label == "Week19"
    assert records
    assert records[0]["date"] == "04 May 2026"
    assert isinstance(records[0]["transaction_date"], datetime)
    assert records[0]["transaction_date"].date() == date(2026, 5, 4)
    # No leftover raw serial display strings
    assert not any(
        str(r.get("date", "")).isdigit() for r in records
    )
