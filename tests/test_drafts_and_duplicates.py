"""Tests for duplicate review helpers."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QApplication

from tahmeed.models.transaction import Transaction
from tahmeed.services.duplicate_review import (
    DuplicateReviewItem,
    convert_lookback_value,
    dup_source_kind,
    effective_lookback_days,
    filter_duplicate_items,
    format_amount_label,
    format_existing_date,
    format_lookback_label,
    format_new_date,
    group_counts,
    lookback_days_from_unit,
    normalize_lookback_overrides,
    rows_to_save_with_duplicate_flags,
    soft_dup_key,
    soft_dup_key_is_meaningful,
    within_table_duplicate_items,
)
from tahmeed.ui.dialogs.duplicate_review_dialog import DuplicateReviewDialog
from tahmeed.ui.dialogs.lookback_overrides_dialog import LookbackOverridesDialog


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


def test_duplicate_review_dialog_edit_mode_defer_label():
    _app()
    item = _sample_item()
    dlg = DuplicateReviewDialog([item], dup_days=5, review_mode="edit")
    assert "edited" in dlg.windowTitle().casefold()
    assert "edited row" in dlg._title.text().casefold()
    assert "skip for now" in dlg._subtitle.text().casefold()
    dlg._accept_keep_for_later()
    assert dlg.kept_for_later() is True
    assert dlg.deferred_duplicate_rows() == {3}


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


def test_dup_source_kind_and_filters():
    db_item = _sample_item()
    table_item = _sample_item()
    table_item.row = 4
    table_item.existing_label = "Same table"
    table_item.truck_number = "T200"
    table_item.item = "DIESEL"
    table_item.description = "FUEL STOP"

    assert dup_source_kind(db_item) == "db"
    assert dup_source_kind(table_item) == "table"

    counts = group_counts([db_item, table_item])
    assert counts["source"]["db"] == 1
    assert counts["source"]["table"] == 1
    assert counts["truck"]["YARD"] == 1
    assert counts["truck"]["T200"] == 1

    only_table = filter_duplicate_items([db_item, table_item], source="table")
    assert [i.row for i in only_table] == [4]
    only_truck = filter_duplicate_items(
        [db_item, table_item], truck="T200"
    )
    assert [i.row for i in only_truck] == [4]
    only_item = filter_duplicate_items(
        [db_item, table_item], item="DIESEL"
    )
    assert [i.row for i in only_item] == [4]


def test_lookback_days_from_unit_and_label():
    assert lookback_days_from_unit(5, "days") == 5
    assert lookback_days_from_unit(2, "months") == 60
    assert lookback_days_from_unit(20, "months") == 365
    assert format_lookback_label(5, unit="days") == "last 5 days"
    assert format_lookback_label(60, unit="months", value=2) == "last 2 months"
    assert convert_lookback_value(5, from_unit="days", to_unit="months") == 1
    assert convert_lookback_value(60, from_unit="days", to_unit="months") == 2
    assert convert_lookback_value(2, from_unit="months", to_unit="days") == 60
    assert convert_lookback_value(5, from_unit="months", to_unit="days") == 150


def test_duplicate_review_dialog_pills_filter_and_rescan(qapp=None):
    _app()
    db_item = _sample_item()
    table_item = DuplicateReviewItem(
        row=5,
        row_display=6,
        description="DIESEL FILL",
        truck_number="T200",
        item="DIESEL",
        amount=8000.0,
        amount_label="TZS 8,000",
        existing=Transaction(
            date=datetime(2026, 8, 18),
            description="DIESEL FILL",
            truck_number="T200",
            amount=8000.0,
            item="DIESEL",
        ),
        date_label="23 Aug 2026",
        existing_label="Same table",
    )
    dlg = DuplicateReviewDialog(
        [db_item, table_item],
        dup_days=5,
        lookback_value=2,
        lookback_unit="months",
    )
    assert dlg._table.rowCount() == 4
    assert "all" in dlg._source_pills
    assert "db" in dlg._source_pills
    assert "table" in dlg._source_pills
    assert dlg._source_pills["all"]._count == 2
    # Truck pills replaced by a compact count badge (2 distinct trucks).
    assert dlg._truck_count_badge is not None
    assert dlg._truck_count_badge.text() == "2"
    assert not dlg._truck_count_badge.isHidden()
    assert dlg._lookback_hint.text() == "= 60 days"

    dlg._on_source_pill("table")
    assert dlg._table.rowCount() == 2
    assert dlg._table.item(0, 5).text() == "T200"
    assert dlg._truck_count_badge is not None
    assert dlg._truck_count_badge.text() == "1"

    # Switching Months → Days converts 2 months → 60 days (not leave spin at 2).
    dlg._lookback_unit_combo.setCurrentIndex(0)  # Days
    assert dlg.lookback_unit() == "days"
    assert dlg.lookback_value() == 60
    assert dlg._lookback_hint.isHidden() or dlg._lookback_hint.text() == ""

    dlg._lookback_spin.setValue(3)
    dlg._checks[0].setChecked(True)
    dlg._request_rescan()
    assert dlg.result() == DuplicateReviewDialog.RESCAN
    assert dlg.is_rescan_result()
    assert dlg.requested_rescan_days() == 3
    assert dlg.checked_rows_snapshot() == {5}
    assert dlg.lookback_unit() == "days"
    assert dlg.lookback_value() == 3

    # Fresh scan replaces pairs in the same dialog (no stale list).
    fresh = _sample_item()
    fresh.row = 9
    fresh.row_display = 10
    fresh.description = "OTHER"
    dlg.apply_fresh_scan(
        [fresh],
        dup_days=3,
        lookback_value=3,
        lookback_unit="days",
    )
    assert dlg._table.rowCount() == 2
    assert dlg._table.item(0, 4).text() == "OTHER"
    assert "last 3 days" in dlg._title.text()
    assert dlg.requested_rescan_days() is None


def test_duplicate_review_dialog_months_rescan_uses_converted_days(qapp=None):
    _app()
    dlg = DuplicateReviewDialog(
        [_sample_item()],
        dup_days=5,
        lookback_value=5,
        lookback_unit="days",
    )
    # Days → Months converts 5 days → 1 month; bump to 5 months explicitly.
    dlg._lookback_unit_combo.setCurrentIndex(1)
    assert dlg.lookback_unit() == "months"
    assert dlg.lookback_value() == 1
    assert dlg._lookback_hint.text() == "= 30 days"
    dlg._lookback_spin.setValue(5)
    assert dlg._lookback_hint.text() == "= 150 days"
    dlg._request_rescan()
    assert dlg.result() == DuplicateReviewDialog.RESCAN
    assert dlg.requested_rescan_days() == 150
    assert dlg.lookback_unit() == "months"
    assert dlg.lookback_value() == 5


def test_effective_lookback_days_prefers_description_then_item():
    overrides = normalize_lookback_overrides(
        {
            "item": {"diesel": 10},
            "description": {"fill up": 2},
        }
    )
    assert (
        effective_lookback_days(
            item="DIESEL",
            description="FILL UP",
            default_days=5,
            overrides=overrides,
        )
        == 2
    )
    assert (
        effective_lookback_days(
            item="DIESEL",
            description="OTHER",
            default_days=5,
            overrides=overrides,
        )
        == 10
    )
    assert (
        effective_lookback_days(
            item="OTHER",
            description="OTHER",
            default_days=5,
            overrides=overrides,
        )
        == 5
    )


def test_lookback_overrides_dialog_saves_non_default_only(qapp=None):
    _app()
    item = _sample_item()
    dlg = LookbackOverridesDialog(
        [item],
        default_days=5,
        overrides={"item": {item.item.casefold(): 2}, "description": {}},
    )
    assert dlg._kind_combo.currentData() == "item"
    key = item.item.casefold()
    assert dlg._table.horizontalHeaderItem(0).text() == "SN"
    assert dlg._table.item(0, 0).text() == "1"
    assert dlg._table.item(0, 1).text() == item.item
    assert dlg._table.rowHeight(0) <= 24
    spin = dlg._spins[("item", key)]
    assert spin.value() == 2
    spin.setValue(12)
    dlg._accept()
    assert dlg.overrides()["item"][key] == 12
    assert dlg.overrides()["description"] == {}


def test_lookback_overrides_dialog_default_clears_override(qapp=None):
    _app()
    item = _sample_item()
    key = item.item.casefold()
    dlg = LookbackOverridesDialog(
        [item],
        default_days=5,
        overrides={"item": {key: 2}, "description": {}},
    )
    spin = dlg._spins[("item", key)]
    dlg._reset_row("item", key, spin)
    assert spin.value() == 5
    dlg._accept()
    assert key not in dlg.overrides()["item"]
