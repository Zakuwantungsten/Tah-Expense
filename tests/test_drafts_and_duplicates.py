"""Tests for duplicate review helpers."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QApplication

from tahmeed.models.transaction import Transaction
from tahmeed.services.duplicate_review import (
    DuplicateReviewItem,
    format_amount_label,
    format_existing_date,
    format_new_date,
    rows_to_save_with_duplicate_flags,
    soft_dup_key,
    soft_dup_key_is_meaningful,
    within_table_duplicate_items,
)
from tahmeed.ui.dialogs.duplicate_review_dialog import DuplicateReviewDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_format_amount_label_tzs_only():
    tx = Transaction(
        date=datetime.utcnow(), description="X", truck_number="", amount=1500.0, currency="TZS",
    )
    assert format_amount_label(tx) == "TZS 1,500"


def test_format_amount_label_dual():
    tx = Transaction(
        date=datetime.utcnow(),
        description="X",
        truck_number="T688 EAF",
        amount=1000.0,
        currency="TZS",
        amount_usd=50.0,
    )
    label = format_amount_label(tx)
    assert "TZS 1,000" in label
    assert "USD 50.00" in label


def test_format_existing_date_none():
    assert format_existing_date(None) == "—"


def test_format_new_date():
    assert format_new_date(None) == "—"
    assert format_new_date(datetime(2026, 8, 23)) == "23 Aug 2026"


def _sample_item() -> DuplicateReviewItem:
    return DuplicateReviewItem(
        row=3,
        row_display=4,
        description="PARKING",
        truck_number="YARD",
        item="PARKING",
        amount=5000.0,
        amount_label="TZS 5,000",
        existing=Transaction(
            date=datetime(2026, 8, 20),
            description="PARKING",
            truck_number="YARD",
            amount=5000.0,
            item="PARKING",
        ),
        date_label="23 Aug 2026",
    )


def test_rows_to_save_with_duplicate_flags():
    item = _sample_item()
    assert rows_to_save_with_duplicate_flags([item], {3}) == {3: True}
    assert rows_to_save_with_duplicate_flags([item], set()) == {}


def test_duplicate_review_dialog_pairs_new_and_existing():
    _app()
    item = _sample_item()
    dlg = DuplicateReviewDialog([item], dup_days=5)
    assert dlg._table.rowCount() == 2
    assert dlg._table.item(0, 1).text() == "New"
    assert dlg._table.item(1, 1).text() == "Existing"
    assert dlg._table.item(0, 3).text() == "23 Aug 2026"
    assert dlg._table.item(1, 3).text() == "20 Aug 2026"
    assert dlg._table.item(0, 4).text() == "PARKING"
    assert dlg._table.item(1, 4).text() == "PARKING"
    # Existing row uses expense/saved cream tint
    assert dlg._table.item(1, 4).background().color().name().lower() == "#fff8f0"
    assert len(dlg._checks) == 1
    dlg._checks[0].setChecked(True)
    dlg._accept_checked()
    assert dlg.save_anyway_rows() == {3}


def test_duplicate_review_dialog_reimport_hint_when_most_rows_match():
    _app()
    items = []
    for i in range(4):
        items.append(
            DuplicateReviewItem(
                row=i,
                row_display=i + 1,
                description=f"ROW {i}",
                truck_number="YARD",
                item="PARKING",
                amount=1000.0,
                amount_label="TZS 1,000",
                existing=Transaction(
                    date=datetime(2026, 8, 20),
                    description=f"ROW {i}",
                    truck_number="YARD",
                    amount=1000.0,
                    item="PARKING",
                ),
                date_label="23 Aug 2026",
            )
        )
    dlg = DuplicateReviewDialog(items, dup_days=5, pending_total=4)
    assert dlg._looks_like_reimport() is True


def _tx(**kwargs) -> Transaction:
    base = dict(
        date=datetime(2026, 6, 11),
        description="T849 EKS",
        truck_number="",
        amount=-300000.0,
        item="MILEAGE MBALA",
    )
    base.update(kwargs)
    return Transaction(**base)


def test_soft_dup_key_normalizes_case_and_whitespace():
    a = soft_dup_key(
        truck_number=" t101 aaa ",
        amount=-300000,
        item="Mileage Mbala",
        description="T849 EKS",
    )
    b = soft_dup_key(
        truck_number="T101 AAA",
        amount=-300000.0,
        item="MILEAGE MBALA",
        description="t849 eks",
    )
    assert a == b
    assert soft_dup_key_is_meaningful(a) is True


def test_soft_dup_key_blank_twins_not_meaningful():
    key = soft_dup_key(truck_number="", amount=0.0, item="", description="")
    assert soft_dup_key_is_meaningful(key) is False


def test_within_table_duplicate_items_flags_later_identical_rows():
    first = _tx()
    second = _tx()
    third = _tx(description="OTHER")
    items = within_table_duplicate_items(
        [(0, first), (1, second), (2, third)]
    )
    assert len(items) == 1
    assert items[0].row == 1
    assert items[0].existing is first
    assert items[0].existing_label == "Same table"


def test_within_table_duplicate_items_skips_already_flagged_rows():
    first = _tx()
    second = _tx()
    items = within_table_duplicate_items(
        [(0, first), (1, second)],
        already_flagged_rows={1},
    )
    assert items == []


def test_within_table_no_truck_still_matches_on_desc_item_amount():
    a = _tx(truck_number="")
    b = _tx(truck_number="")
    items = within_table_duplicate_items([(3, a), (5, b)])
    assert len(items) == 1
    assert items[0].row == 5
    assert items[0].truck_number == ""


def test_duplicate_review_dialog_same_table_label():
    _app()
    item = _sample_item()
    item.existing_label = "Same table"
    dlg = DuplicateReviewDialog([item], dup_days=5)
    assert dlg._table.item(1, 1).text() == "Same table"
