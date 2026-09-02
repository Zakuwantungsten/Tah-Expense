"""Excel-style vertical row header (numbers + row select + resize)."""
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
    from tahmeed.ui.cashier.excel_grid import DailyRegister
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TZS
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidgetItem

    user = MagicMock()
    user._id = "cashier-1"
    reg = DailyRegister(user, [])
    reg._merged_mode = True
    reg._saved_count = 0

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        reg._table.setItem(row, col, it)

    reg._put = _put
    reg._COL_DESC = COL_DESC
    reg._COL_ITEM = COL_ITEM
    reg._COL_TZS = COL_TZS
    return reg


def test_vertical_header_visible_with_row_numbers(register):
    from PySide6.QtWidgets import QHeaderView

    from tahmeed.ui.cashier.excel_row_header import ExcelRowHeaderView, DEFAULT_ROW_HEIGHT

    table = register._table
    vh = table.verticalHeader()
    assert isinstance(vh, ExcelRowHeaderView)
    assert not vh.isHidden()
    assert vh.defaultSectionSize() == DEFAULT_ROW_HEIGHT
    assert vh.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive

    count = table.rowCount()
    assert count > 0
    for row in range(count):
        item = table.verticalHeaderItem(row)
        assert item is not None
        assert item.text() == str(row + 1)


def test_row_header_click_selects_full_row(register, qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    table = register._table
    table.show()
    qapp.processEvents()

    vh = table.verticalHeader()
    row = 2
    y = vh.sectionPosition(row) + vh.sectionSize(row) // 2
    center = vh.viewport().rect().center()
    center.setY(y - vh.offset())

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        center.toPointF(),
        vh.viewport().mapToGlobal(center),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    QApplication.sendEvent(vh.viewport(), press)

    selected_rows = {i.row() for i in table.selectedIndexes()}
    assert selected_rows == {row}
    assert register._selection_is_full_rows([row])


def test_row_header_keyboard_copy(register, qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication, QTableWidgetItem

    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM

    register._put(2, COL_ITEM, "Diesel")
    register._put(2, COL_DESC, "Test row")
    table = register._table
    table.show()
    qapp.processEvents()
    table.selectRow(2)
    assert register._selection_is_full_rows([2])

    vh = table.verticalHeader()
    vh.setFocus()
    event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key_C,
        Qt.ControlModifier,
    )
    QApplication.sendEvent(vh, event)
    text = QApplication.clipboard().text()
    assert "Diesel" in text or "DIESEL" in text
