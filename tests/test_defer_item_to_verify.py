"""Description-only entries when defer_item_to_verify is enabled."""
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
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TZS, COL_TRUCK
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidgetItem

    user = MagicMock()
    user._id = "cashier-1"
    fuel = Category(name="Fuel", requires_truck=False)
    reg = DailyRegister(user, [fuel])
    reg._merged_mode = True
    reg._current_date = __import__("datetime").date(2026, 8, 23)

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        reg._table.setItem(row, col, it)

    reg._put = _put
    reg._COL_ITEM = COL_ITEM
    reg._COL_DESC = COL_DESC
    reg._COL_TZS = COL_TZS
    reg._COL_TRUCK = COL_TRUCK
    return reg


def test_build_transaction_requires_item_when_defer_off(register):
    register._defer_item_to_verify = False
    register._put(0, register._COL_DESC, "Office supplies")
    register._put(0, register._COL_TZS, "1,000.00")

    with pytest.raises(ValueError, match="Item is required"):
        register._build_transaction_from_row(0)


def test_build_transaction_allows_blank_item_when_defer_on(register):
    register._defer_item_to_verify = True
    register._put(0, register._COL_DESC, "Office supplies")
    register._put(0, register._COL_TZS, "1,000.00")

    tx = register._build_transaction_from_row(0)
    assert tx is not None
    assert tx.description == "OFFICE SUPPLIES"
    assert tx.item == ""
    assert tx.category_name is None


def test_build_transaction_accepts_allow_anyway_truck(register):
    register._truck_allow_anyway[0] = "T128 EFP"
    register._put(0, register._COL_ITEM, "Fuel")
    register._put(0, register._COL_DESC, "Partner diesel")
    register._put(0, register._COL_TRUCK, "T128 EFP")
    register._put(0, register._COL_TZS, "1,000.00")

    tx = register._build_transaction_from_row(0)
    assert tx is not None
    assert tx.truck_number == "T128 EFP"


def test_build_transaction_still_accepts_item_when_defer_on(register):
    register._defer_item_to_verify = True
    register._put(0, register._COL_ITEM, "Fuel")
    register._put(0, register._COL_DESC, "Diesel")
    register._put(0, register._COL_TZS, "2,500.00")

    tx = register._build_transaction_from_row(0)
    assert tx is not None
    assert tx.item == "FUEL"
    assert tx.category_name == "FUEL"


def test_entry_form_resolve_item_for_submit(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.entry_form.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    from tahmeed.ui.cashier.entry_form import EntryForm

    user = MagicMock()
    user._id = "cashier-1"
    form = EntryForm(user)
    form._defer_item_to_verify = True
    form._restrict_items = False
    form._categories = []
    form._cat_by_name = {}

    item, cat_id = form._resolve_item_for_submit()
    assert item == ""
    assert cat_id is None

    form._defer_item_to_verify = False
    with pytest.raises(ValueError, match="Item is required"):
        form._resolve_item_for_submit()
