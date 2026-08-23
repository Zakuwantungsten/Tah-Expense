"""Excel-style grid cursors and fill-handle behaviour."""
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


def test_fat_hover_on_cell_thin_at_fill_corner(register):
    table = register._table
    register._put(0, register._COL_DESC, "Diesel")
    table.setCurrentCell(0, register._COL_DESC)

    rect = table.visualRect(table.model().index(0, register._COL_DESC))
    table._update_hover_cursor(rect.center())
    assert table._cursor_zone == "hover"
    assert table.cursor().pixmap() is not None

    handle = table._fill_handle_hit_rect()
    assert handle is not None
    table._update_hover_cursor(handle.center())
    assert table._cursor_zone == "fill"
    assert table.cursor().pixmap() is not None


def test_fill_handle_shows_over_active_selection(register):
    table = register._table
    register._put(0, register._COL_DESC, "Diesel")
    table.setCurrentCell(0, register._COL_DESC)

    assert table._fill_handle_enabled()
    handle = table._fill_handle_rect()
    assert handle is not None
    assert handle.width() == 7
    assert table._pos_in_fill_handle(handle.center())


def test_fill_handle_mouse_drag_copies_down(register, qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    register._put(0, register._COL_DESC, "Diesel")
    table = register._table
    table.show()
    qapp.processEvents()
    table.setCurrentCell(0, register._COL_DESC)

    vp = table.viewport()
    hit = table._fill_handle_hit_rect()
    assert hit is not None
    table._update_hover_cursor(hit.center())
    assert table._cursor_zone == "fill"

    center = hit.center()
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        center.toPointF(),
        vp.mapToGlobal(center),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    QApplication.sendEvent(vp, press)
    assert table._fill_dragging

    row2 = table.visualRect(table.model().index(2, register._COL_DESC))
    move_pos = row2.center()
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        move_pos.toPointF(),
        vp.mapToGlobal(move_pos),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    QApplication.sendEvent(vp, move)

    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        move_pos.toPointF(),
        vp.mapToGlobal(move_pos),
        Qt.LeftButton,
        Qt.NoButton,
        Qt.NoModifier,
    )
    QApplication.sendEvent(vp, release)

    assert not table._fill_dragging
    assert table.item(2, register._COL_DESC).text() == "DIESEL"


def test_fill_drag_shows_copy_preview_not_full_selection(register, qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    register._put(0, register._COL_DESC, "Diesel")
    table = register._table
    table.show()
    qapp.processEvents()
    table.setCurrentCell(0, register._COL_DESC)

    vp = table.viewport()
    hit = table._fill_handle_hit_rect()
    center = hit.center()
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        center.toPointF(),
        vp.mapToGlobal(center),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    QApplication.sendEvent(vp, press)

    row2 = table.visualRect(table.model().index(2, register._COL_DESC))
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        row2.center().toPointF(),
        vp.mapToGlobal(row2.center()),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    QApplication.sendEvent(vp, move)

    rows = {i.row() for i in table.selectedIndexes()}
    assert rows == {0}
    assert table._fill_preview_display_text(2, register._COL_DESC) == "DIESEL"


def test_fill_drag_copies_value_down(register):
    register._put(0, register._COL_DESC, "Diesel")
    register._put(0, register._COL_TZS, "1,000.00")
    register._table.setCurrentCell(0, register._COL_DESC)
    register._table.selectRow(0)

    register._apply_fill_drag((0, register._COL_DESC, 0, register._COL_TZS), (2, register._COL_TZS))

    assert register._table.item(1, register._COL_DESC).text() == "DIESEL"
    assert register._table.item(2, register._COL_DESC).text() == "DIESEL"
    assert register._table.item(2, register._COL_TZS).text() == "1,000.00"


def test_fill_drag_records_undo(register):
    register._put(0, register._COL_DESC, "Diesel")
    register._table.setCurrentCell(0, register._COL_DESC)

    register._apply_fill_drag((0, register._COL_DESC, 0, register._COL_DESC), (2, register._COL_DESC))
    assert register._table.item(2, register._COL_DESC).text() == "DIESEL"
    assert register._undo_stack

    register._undo()
    assert register._table.item(2, register._COL_DESC) is None or (
        register._table.item(2, register._COL_DESC).text() == ""
    )
