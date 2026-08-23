"""Save blocking when an item requires a truck number."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def register(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    from tahmeed.models.category import Category
    from tahmeed.ui.cashier.excel_grid import DailyRegister
    from tahmeed.ui.cashier.register_delegates import (
        COL_DESC,
        COL_ITEM,
        COL_TRUCK,
        COL_TZS,
        TRUCK_REQUIRED_BG,
    )
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidgetItem

    user = MagicMock()
    user._id = "cashier-1"
    fuel = Category(name="Fuel", requires_truck=True)
    parking = Category(name="Parking", requires_truck=False)
    reg = DailyRegister(user, [fuel, parking])
    reg._merged_mode = True
    reg._current_date = __import__("datetime").date(2026, 8, 23)

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        reg._table.setItem(row, col, it)

    reg._put = _put
    reg._COL_ITEM = COL_ITEM
    reg._COL_DESC = COL_DESC
    reg._COL_TRUCK = COL_TRUCK
    reg._COL_TZS = COL_TZS
    reg._TRUCK_REQUIRED_BG = TRUCK_REQUIRED_BG
    return reg


def test_build_transaction_raises_when_truck_required_but_empty(register):
    register._put(0, register._COL_ITEM, "Fuel")
    register._put(0, register._COL_DESC, "Diesel")
    register._put(0, register._COL_TZS, "1,000.00")

    with pytest.raises(ValueError, match="Truck number is required"):
        register._build_transaction_from_row(0)


def test_build_transaction_allows_empty_truck_when_not_required(register):
    register._put(0, register._COL_ITEM, "Parking")
    register._put(0, register._COL_DESC, "City lot")
    register._put(0, register._COL_TZS, "500.00")

    tx = register._build_transaction_from_row(0)
    assert tx is not None
    assert tx.truck_number == ""


def test_rows_missing_required_truck_detects_new_row(register):
    register._table.setRowCount(2)
    register._saved_count = 0
    register._put(0, register._COL_ITEM, "Fuel")
    register._put(0, register._COL_DESC, "Diesel")
    register._put(0, register._COL_TZS, "1,000.00")

    assert register._rows_missing_required_truck() == [0]


def test_rows_missing_required_truck_ignores_item_without_requirement(register):
    register._table.setRowCount(2)
    register._saved_count = 0
    register._put(0, register._COL_ITEM, "Parking")
    register._put(0, register._COL_DESC, "City lot")
    register._put(0, register._COL_TZS, "500.00")

    assert register._rows_missing_required_truck() == []


def test_rows_missing_required_truck_detects_dirty_saved_row(register):
    register._table.setRowCount(2)
    register._saved_count = 1
    register._put(0, register._COL_ITEM, "Fuel")
    register._put(0, register._COL_DESC, "Diesel")
    register._put(0, register._COL_TZS, "1,000.00")
    register._dirty_rows.add(0)

    assert register._rows_missing_required_truck() == [0]


def test_rows_missing_required_truck_accepts_place_label(register):
    register._table.setRowCount(2)
    register._saved_count = 0
    register._put(0, register._COL_ITEM, "Fuel")
    register._put(0, register._COL_DESC, "Diesel")
    register._put(0, register._COL_TRUCK, "YARD")
    register._put(0, register._COL_TZS, "1,000.00")

    assert register._rows_missing_required_truck() == []


@pytest.mark.asyncio
async def test_save_blocked_when_truck_required_but_empty(register, monkeypatch):
    register._table.setRowCount(3)
    register._saved_count = 0
    register._put(0, register._COL_ITEM, "Fuel")
    register._put(0, register._COL_DESC, "Diesel")
    register._put(0, register._COL_TZS, "1,000.00")

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.warning",
        lambda *_a, **_k: 0,
    )

    ok = await register._do_save_body()
    assert ok is False


def test_truck_cell_highlighted_when_required_and_empty(register):
    register._table.setRowCount(2)
    register._put(0, register._COL_ITEM, "Fuel")
    register._put(0, register._COL_DESC, "Diesel")
    register._put(0, register._COL_TRUCK, "")

    register._update_truck_required_highlight(0)

    truck_it = register._table.item(0, register._COL_TRUCK)
    assert truck_it is not None
    assert truck_it.background().color() == register._TRUCK_REQUIRED_BG
    assert "Truck number is required" in truck_it.toolTip()


def test_truck_cell_highlight_cleared_when_truck_entered(register):
    register._table.setRowCount(2)
    register._put(0, register._COL_ITEM, "Fuel")
    register._put(0, register._COL_DESC, "Diesel")
    register._put(0, register._COL_TRUCK, "")

    register._update_truck_required_highlight(0)
    register._put(0, register._COL_TRUCK, "YARD")
    register._update_truck_required_highlight(0)

    truck_it = register._table.item(0, register._COL_TRUCK)
    assert truck_it.background().color() != register._TRUCK_REQUIRED_BG
    assert truck_it.toolTip() == ""
