"""Tests for daily Excel import helpers (receipt, amount, date detection)."""

from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from tahmeed.models.transaction import Transaction
from tahmeed.services.daily_import_service import (
    DailyImportPreview,
    DailyImportRow,
    _looks_like_classic_matumizi,
    analyze_date_allocation,
    apply_date_policy,
    detect_date_from_name,
    normalize_receipt,
    parse_amount,
    parse_date_value,
    parse_daily_expenses_excel,
    pick_primary_date,
    staged_row_payload,
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


def test_analyze_date_allocation_clear_majority() -> None:
    dates = [date(2025, 12, 31)] * 300 + [date(2025, 12, 30)] * 120
    alloc = analyze_date_allocation(dates)
    assert alloc.clear_majority is True
    assert alloc.primary == date(2025, 12, 31)
    assert alloc.candidates == (date(2025, 12, 31),)


def test_analyze_date_allocation_tie_is_unclear() -> None:
    dates = [date(2026, 7, 21)] * 5 + [date(2026, 7, 22)] * 5
    alloc = analyze_date_allocation(dates)
    assert alloc.clear_majority is False
    assert alloc.primary is None
    assert alloc.candidates == (date(2026, 7, 21), date(2026, 7, 22))


def test_pick_primary_date_filename_fallback() -> None:
    assert pick_primary_date(
        [], filename="MATUMIZI YA 23-07-2026.xlsx", sheet_name="Sheet1"
    ) == date(2026, 7, 23)


def test_apply_date_policy_keeps_excel_row_dates() -> None:
    rows = [
        DailyImportRow(
            serial=1,
            date=datetime(2025, 12, 31),
            description="Fuel",
            truck_number="",
            lpo_do="",
            do_number="",
            memo="",
            notes="",
            amount=100.0,
            currency="TZS",
            receipt_status="pending",
            ownership="",
            approver="",
        ),
        DailyImportRow(
            serial=2,
            date=datetime(2025, 12, 30),
            description="Oil",
            truck_number="",
            lpo_do="",
            do_number="",
            memo="",
            notes="",
            amount=50.0,
            currency="TZS",
            receipt_status="pending",
            ownership="",
            approver="",
        ),
    ]
    preview = DailyImportPreview(
        source_filename="x.xlsx",
        source_path="x.xlsx",
        rows=rows,
        primary_date=date(2025, 12, 31),
        outlier_count=1,
        date_majority_clear=True,
    )
    apply_date_policy(preview, force_primary=True, flag_discrepancy=True)
    assert rows[0].date.date() == date(2025, 12, 31)
    assert rows[1].date.date() == date(2025, 12, 30)
    assert preview.force_primary_date is False
    assert preview.flag_date_discrepancy is False
    payload = staged_row_payload(rows[1], preview)
    assert payload["date"].date() == date(2025, 12, 30)
    assert payload["import_primary_date"].date() == date(2025, 12, 31)
    assert payload["date_discrepancy"] is False


def test_parse_date_value() -> None:
    assert parse_date_value(datetime(2026, 7, 21)).date() == date(2026, 7, 21)
    assert parse_date_value("21/07/2026").date() == date(2026, 7, 21)
    assert parse_date_value("21-07-2026").date() == date(2026, 7, 21)
    assert parse_date_value(46146).date() == date(2026, 5, 4)
    assert parse_date_value("46146").date() == date(2026, 5, 4)


def test_looks_like_classic_matumizi() -> None:
    assert _looks_like_classic_matumizi(
        ["S/NO", "DATE", "X", "DESCRIPTION", "TRUCK NO."]
    )
    assert not _looks_like_classic_matumizi(["Name", "Amount", "Notes"])
    assert not _looks_like_classic_matumizi([])


def test_reject_wrong_format_workbook(tmp_path: Path) -> None:
    path = tmp_path / "parking.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Ticket", "Plate", "Fee"])
    ws.append(["1", "T123 ABC", "5000"])
    wb.save(path)
    wb.close()

    with pytest.raises(ValueError, match="does not match the Daily Register format"):
        parse_daily_expenses_excel(path)


def test_accept_header_mapped_workbook(tmp_path: Path) -> None:
    path = tmp_path / "matumizi.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "TZS", "Truck No."])
    ws.append([datetime(2026, 7, 21), "DIESEL", 10000, "T688 EAF"])
    wb.save(path)
    wb.close()

    rows, skipped, sheet = parse_daily_expenses_excel(path)
    assert sheet
    assert skipped == 0
    assert len(rows) == 1
    assert rows[0].description == "DIESEL"
    assert rows[0].amount == 10000.0


def test_transaction_to_doc_omits_null_import_id() -> None:
    tx = Transaction(
        date=datetime(2026, 7, 21),
        description="TEST",
        truck_number="",
        amount=100.0,
    )
    doc = tx.to_doc()
    assert "daily_import_id" not in doc
    assert "daily_import_source" not in doc

    tx.daily_import_id = "batch-1"
    tx.daily_import_source = "file.xlsx"
    doc2 = tx.to_doc()
    assert doc2["daily_import_id"] == "batch-1"
    assert doc2["daily_import_source"] == "file.xlsx"


def test_browse_match_upload_id_skips_date_filter() -> None:
    from tahmeed.services.cashier_service import _browse_match

    match = _browse_match(
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 31),
        daily_import_id="batch-abc",
    )
    assert match["daily_import_id"] == "batch-abc"
    assert "date" not in match


def test_master_query_filters_by_excel_date() -> None:
    from tahmeed.services.accountant_service import _build_master_query

    q = _build_master_query(2025, 12, "", "", "", "", "")
    assert q["verified"] is True
    assert "date" in q
    assert q["date"]["$gte"].month == 12
    assert q["date"]["$lte"].month == 12
    assert "import_primary_date" not in q
    assert "$or" not in q

