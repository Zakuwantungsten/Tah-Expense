"""Daily Register toolbar AutoFilter — activate / clear column ▾ filters."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QHeaderView, QTableWidget, QTableWidgetItem


@pytest.fixture
def qapp():
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
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid._load_table_font_pt",
        lambda: 11,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid._save_table_font_pt",
        lambda _pt: None,
    )
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    user = MagicMock()
    user._id = "cashier-1"
    return DailyRegister(user, [])


def test_qb_toolbar_exposes_filter_button(qapp):
    from tahmeed.ui.widgets.qb_txn_toolbar import QbTxnToolbar

    bar = QbTxnToolbar(register_actions=True)
    assert bar._btn_filter is not None
    assert bar._btn_filter.isCheckable()
    assert bar._btn_filter.isChecked()
    bar.set_filter_active(False)
    assert not bar._btn_filter.isChecked()
    assert "off" in bar._btn_filter.toolTip().lower()
    bar.set_filter_active(True)
    assert bar._btn_filter.isChecked()
    assert "on" in bar._btn_filter.toolTip().lower()

    form_bar = QbTxnToolbar(register_actions=False)
    assert form_bar._btn_filter is None


def test_toolbar_toggle_clears_filters_and_hides_chevrons(register):
    from tahmeed.ui.cashier.excel_grid import _FilterHeaderView
    from tahmeed.ui.cashier.register_delegates import COL_ITEM

    assert register.column_filters_enabled()
    hdr = register._table.horizontalHeader()
    assert isinstance(hdr, _FilterHeaderView)
    assert hdr.filters_enabled()

    register._col_filters[COL_ITEM] = {"Fuel"}
    register._sync_filter_header()
    assert hdr._active.get(COL_ITEM) == {"Fuel"}

    register.toolbar_toggle_column_filters()
    assert not register.column_filters_enabled()
    assert register._col_filters == {}
    assert not hdr.filters_enabled()
    assert hdr._active == {}

    register.toolbar_toggle_column_filters()
    assert register.column_filters_enabled()
    assert hdr.filters_enabled()


def test_disabled_header_does_not_open_filter_menu(qapp, monkeypatch):
    from tahmeed.ui.cashier.excel_grid import (
        _COLUMN_RESIZE_HIT_PX,
        _FILTER_COLS,
        _FilterHeaderView,
    )
    from tahmeed.ui.widgets.excel_column_filter import chevron_hit_contains

    table = QTableWidget(2, 4)
    hdr = _FilterHeaderView(table)
    table.setHorizontalHeader(hdr)
    for i in range(4):
        hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        table.setColumnWidth(i, 100)

    # Pick a filterable column (not S/N).
    col = next(c for c in range(4) if c in _FILTER_COLS)
    opened = {"n": 0}
    monkeypatch.setattr(
        hdr, "_open_menu", lambda *_a, **_k: opened.__setitem__("n", opened["n"] + 1)
    )

    col_x = hdr.sectionViewportPosition(col)
    col_w = hdr.sectionSize(col)
    # Click inside the chevron zone (not the resize grip).
    click_x = col_x + col_w - _COLUMN_RESIZE_HIT_PX - 8
    assert chevron_hit_contains(col_x, col_w, click_x)

    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPoint(click_x, 8),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    hdr.mousePressEvent(press)
    assert opened["n"] == 1

    hdr.set_filters_enabled(False)
    hdr.mousePressEvent(press)
    assert opened["n"] == 1  # no second open


def test_toggle_off_unhides_rows_filtered_by_column(register):
    from tahmeed.ui.cashier.register_delegates import COL_ITEM

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        register._table.setItem(row, col, it)

    register._saved_count = 2
    register._table.setRowCount(4)
    _put(0, COL_ITEM, "Fuel")
    _put(1, COL_ITEM, "Parts")

    register._col_filters[COL_ITEM] = {"Fuel"}
    register._apply_filters()
    assert not register._table.isRowHidden(0)
    assert register._table.isRowHidden(1)

    register.toolbar_toggle_column_filters()
    assert not register._table.isRowHidden(0)
    assert not register._table.isRowHidden(1)
