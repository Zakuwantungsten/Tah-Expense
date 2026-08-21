"""Master ledger Excel-like paste / fill helpers (UI smoke)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QTableWidgetItem
from PySide6.QtCore import Qt

from tahmeed.ui.accountant.master_ledger_table import (
    MasterLedgerTable,
    _COL_DESC,
    _COL_ITEM,
    _plain,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_plain_treats_emdash_as_empty() -> None:
    assert _plain("—") == ""
    assert _plain("  hello ") == "hello"


def test_single_value_paste_fills_selection() -> None:
    _app()
    grid = MasterLedgerTable()
    t = grid.table()
    t.setRowCount(3)
    grid._txs = [SimpleNamespace(_id="a"), SimpleNamespace(_id="b"), SimpleNamespace(_id="c")]
    for r in range(3):
        for c in range(t.columnCount()):
            it = QTableWidgetItem("")
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            t.setItem(r, c, it)
    grid.enter_edit_mode()
    t.setCurrentCell(0, _COL_DESC)
    t.clearSelection()
    for r in range(3):
        t.item(r, _COL_DESC).setSelected(True)

    from PySide6.QtWidgets import QApplication as QA
    QA.clipboard().setText("PARKING FEE")
    grid._paste()

    for r in range(3):
        assert t.item(r, _COL_DESC).text() == "PARKING FEE"
    assert grid.dirty_rows() == [0, 1, 2]


def test_fill_down_copies_top_cell() -> None:
    _app()
    grid = MasterLedgerTable()
    t = grid.table()
    t.setRowCount(3)
    grid._txs = [SimpleNamespace(_id="a"), SimpleNamespace(_id="b"), SimpleNamespace(_id="c")]
    for r in range(3):
        for c in range(t.columnCount()):
            it = QTableWidgetItem("X" if r == 0 and c == _COL_ITEM else "")
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            t.setItem(r, c, it)
    t.item(0, _COL_ITEM).setText("DIESEL")
    grid.enter_edit_mode()
    t.clearSelection()
    for r in range(3):
        t.item(r, _COL_ITEM).setSelected(True)
    grid._fill_down()
    assert t.item(1, _COL_ITEM).text() == "DIESEL"
    assert t.item(2, _COL_ITEM).text() == "DIESEL"
