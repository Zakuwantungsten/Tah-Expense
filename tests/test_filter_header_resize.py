"""Register filter header: section-edge press must reach Qt resize, not column-select."""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QHeaderView, QTableWidget

from tahmeed.ui.cashier.excel_grid import _COLUMN_RESIZE_HIT_PX, _FilterHeaderView


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _header_with_table(cols: int = 4) -> tuple[QTableWidget, _FilterHeaderView]:
    table = QTableWidget(2, cols)
    hdr = _FilterHeaderView(table)
    table.setHorizontalHeader(hdr)
    for i in range(cols):
        hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        table.setColumnWidth(i, 100)
    return table, hdr


def test_on_column_resize_edge_detects_boundary() -> None:
    _app()
    _table, hdr = _header_with_table()
    # First section ends at x=100 (viewport).
    assert hdr._on_column_resize_edge(QPoint(100, 8)) is True
    assert hdr._on_column_resize_edge(QPoint(100 - _COLUMN_RESIZE_HIT_PX, 8)) is True
    # Middle of first section — not a resize grip.
    assert hdr._on_column_resize_edge(QPoint(50, 8)) is False


def test_edge_press_does_not_select_column(monkeypatch) -> None:
    _app()
    table, hdr = _header_with_table()
    selected = {"n": 0}

    def boom_select(_col: int) -> None:
        selected["n"] += 1

    monkeypatch.setattr(table, "selectColumn", boom_select)

    edge = QPoint(100, 8)
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        edge,
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    hdr.mousePressEvent(press)
    assert selected["n"] == 0


def test_interior_press_still_selects_column(monkeypatch) -> None:
    _app()
    table, hdr = _header_with_table()
    selected = {"cols": []}

    def capture(col: int) -> None:
        selected["cols"].append(col)

    monkeypatch.setattr(table, "selectColumn", capture)

    mid = QPoint(50, 8)
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        mid,
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    hdr.mousePressEvent(press)
    assert selected["cols"] == [0]
