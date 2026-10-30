"""Multi-file daily / MASTER workbook parsing for bulk import sessions."""

from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from tahmeed.services.import_session_parser import (
    SKIP_BAD_SOURCE_DATE,
    SKIP_EMPTY_DESCRIPTION,
    SKIP_MISSING_AMOUNT,
    SKIP_TOTAL_ROW,
    SKIP_UNPARSEABLE_AMOUNT,
    SKIP_ZERO_AMOUNT,
    common_sheet_names,
    normalize_import_description,
    parse_source_file_date,
    parse_workbook,
)


def _book(path: Path, rows, *, sheet: str = "Sheet1", extra_sheets=()) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(list(row))
    for name in extra_sheets:
        wb.create_sheet(name)
    wb.save(path)
    wb.close()
    return path


def test_source_file_date_matumizi_two_and_four_digit_years() -> None:
    assert parse_source_file_date("MATUMIZI YA 19-10-26.xlsx") == date(2026, 10, 19)
    assert parse_source_file_date("matumizi ya 7.09.2026") == date(2026, 9, 7)
    assert parse_source_file_date(r"C:\x\MATUMIZI YA 01/09/26.xlsx") == date(2026, 9, 1)
    assert parse_source_file_date("daily 20-08-2026.xlsx") == date(2026, 8, 20)
    assert parse_source_file_date("MATUMIZI YA 31-02-26") is None
    assert parse_source_file_date("random.xlsx") is None


def test_normalize_description_strips_trailing_dash_and_spaces() -> None:
    assert normalize_import_description("  get  valve -  ") == "GET VALVE"
    assert normalize_import_description("tyre repair —") == "TYRE REPAIR"


def test_master_workbook_groups_by_source_file_not_row_date(tmp_path: Path) -> None:
    path = _book(
        tmp_path / "OCTOBER MASTER.xlsx",
        [
            ("S/NO", "DATE", "DESCRIPTION", "TRUCK NO", "NOTES", "TZS", "Usd", "SOURCE FILE"),
            (1, datetime(2026, 10, 18), "Diesel", "t853eks", None, 1000, None, "MATUMIZI YA 19-10-26.xlsx"),
            (2, datetime(2026, 10, 19), "Tolls", None, None, 500, None, "MATUMIZI YA 19-10-26.xlsx"),
            (3, datetime(2026, 10, 20), "Parking", None, None, 200, None, "MATUMIZI YA 20-10-26.xlsx"),
        ],
        sheet="MASTER",
    )
    parsed = parse_workbook(path)
    assert parsed.file.sheet == "MASTER"
    by_date = {}
    for row in parsed.rows:
        by_date.setdefault(row.batch_date, []).append(row.description)
    assert by_date == {
        date(2026, 10, 19): ["DIESEL", "TOLLS"],
        date(2026, 10, 20): ["PARKING"],
    }
    first = parsed.rows[0]
    assert first.excel_date == date(2026, 10, 18)
    assert first.truck_raw == "T853EKS"
    assert first.source_file == "MATUMIZI YA 19-10-26.xlsx"
    assert first.excel_row == 2
    assert first.excel_cells["SOURCE FILE"] == "MATUMIZI YA 19-10-26.xlsx"
    assert "SOURCE FILE" in parsed.file.headers


def test_daily_file_uses_filename_date(tmp_path: Path) -> None:
    path = _book(
        tmp_path / "MATUMIZI YA 05-10-26.xlsx",
        [
            ("Date", "Description", "Truck No", "TZS"),
            (datetime(2026, 10, 4), "Diesel", "T100 ABC", 1000),
        ],
    )
    parsed = parse_workbook(path)
    assert [r.batch_date for r in parsed.rows] == [date(2026, 10, 5)]
    assert parsed.rows[0].source_file == "MATUMIZI YA 05-10-26.xlsx"


def test_no_name_date_falls_back_to_majority_row_date(tmp_path: Path) -> None:
    path = _book(
        tmp_path / "cash book.xlsx",
        [
            ("Date", "Description", "TZS"),
            (datetime(2026, 10, 4), "A", 1),
            (datetime(2026, 10, 5), "B", 1),
            (datetime(2026, 10, 5), "C", 1),
        ],
    )
    parsed = parse_workbook(path)
    assert {r.batch_date for r in parsed.rows} == {date(2026, 10, 5)}
    assert any("row dates" in w for w in parsed.file.warnings)


def test_no_date_anywhere_skips_rows(tmp_path: Path) -> None:
    path = _book(
        tmp_path / "cash book.xlsx",
        [("Description", "TZS"), ("A", 1)],
    )
    parsed = parse_workbook(path)
    assert parsed.rows == []
    assert [s.reason for s in parsed.skipped] == [SKIP_BAD_SOURCE_DATE]


def test_skip_reasons_and_usd_rows_kept(tmp_path: Path) -> None:
    path = _book(
        tmp_path / "MATUMIZI YA 05-10-26.xlsx",
        [
            ("Date", "Description", "TZS", "USD", "SOURCE FILE"),
            (None, None, 100, None, None),
            (None, "TOTAL", 100, None, None),
            (None, "No money", None, None, None),
            (None, "Bad money", "abc", None, None),
            (None, "Zero", 0, None, None),
            (None, "Dollars", None, 50, None),
            (None, "Odd source", 10, None, "MASTER COPY"),
            (None, "Bad source", 10, None, "MATUMIZI YA 40-40-26"),
        ],
    )
    parsed = parse_workbook(path)
    reasons = [s.reason for s in parsed.skipped]
    assert reasons == [
        SKIP_EMPTY_DESCRIPTION,
        SKIP_TOTAL_ROW,
        SKIP_MISSING_AMOUNT,
        SKIP_UNPARSEABLE_AMOUNT,
        SKIP_ZERO_AMOUNT,
    ]
    descs = {r.description: r for r in parsed.rows}
    assert descs["DOLLARS"].currency == "USD"
    assert descs["DOLLARS"].amount_usd == 50
    # Unreadable SOURCE FILE falls back to the workbook filename date.
    assert descs["ODD SOURCE"].batch_date == date(2026, 10, 5)
    assert descs["BAD SOURCE"].batch_date == date(2026, 10, 5)


def test_unreadable_source_without_filename_date_is_skipped(tmp_path: Path) -> None:
    path = _book(
        tmp_path / "master.xlsx",
        [
            ("Description", "TZS", "SOURCE FILE"),
            ("A", 10, "MASTER COPY"),
            ("B", 10, "MATUMIZI YA 02-10-26"),
        ],
    )
    parsed = parse_workbook(path)
    assert [r.description for r in parsed.rows] == ["B"]
    assert [s.reason for s in parsed.skipped] == [SKIP_BAD_SOURCE_DATE]


def test_header_found_below_title_rows(tmp_path: Path) -> None:
    path = _book(
        tmp_path / "MATUMIZI YA 05-10-26.xlsx",
        [
            ("TAHMEED COACH",),
            ("Daily expenses",),
            ("S/No", "Date", "Description", "TZS"),
            (1, datetime(2026, 10, 5), "Diesel", 1000),
        ],
    )
    parsed = parse_workbook(path)
    assert [r.excel_row for r in parsed.rows] == [4]


def test_missing_header_raises(tmp_path: Path) -> None:
    path = _book(tmp_path / "x.xlsx", [("A", "B"), (1, 2)])
    with pytest.raises(ValueError, match="no header row"):
        parse_workbook(path)


def test_sheet_fallback_prefers_master(tmp_path: Path) -> None:
    path = _book(
        tmp_path / "MATUMIZI YA 05-10-26.xlsx",
        [("Description", "TZS"), ("A", 1)],
        sheet="MASTER",
        extra_sheets=("Other",),
    )
    parsed = parse_workbook(path, sheet_name="Missing")
    assert parsed.file.sheet == "MASTER"
    assert any("not found" in w for w in parsed.file.warnings)


def test_common_sheet_names(tmp_path: Path) -> None:
    a = _book(tmp_path / "a.xlsx", [("x",)], sheet="MASTER", extra_sheets=("Jan",))
    b = _book(tmp_path / "b.xlsx", [("x",)], sheet="MASTER", extra_sheets=("Feb",))
    assert common_sheet_names([a, b]) == ["MASTER"]
