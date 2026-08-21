"""Payee/Cheque header fields sync with the DailyRegister active row."""
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
    from tahmeed.ui.cashier.register_delegates import COL_CHEQUE, COL_PAYEE, DEFAULT_EDITABLE_ROWS
    from PySide6.QtWidgets import QTableWidgetItem
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QBrush
    from tahmeed.ui.cashier.register_delegates import NEW_BG

    user = MagicMock()
    user._id = "cashier-1"
    reg = DailyRegister(user, [])
    reg._saved_count = 0
    reg._table.setRowCount(DEFAULT_EDITABLE_ROWS)
    for row in range(DEFAULT_EDITABLE_ROWS):
        for col in (COL_PAYEE, COL_CHEQUE):
            it = QTableWidgetItem("")
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            it.setBackground(QBrush(NEW_BG))
            reg._table.setItem(row, col, it)
    reg._table.setCurrentCell(0, COL_PAYEE)
    return reg


def test_header_writes_payee_and_cheque_into_active_row(register):
    from tahmeed.ui.cashier.register_delegates import COL_CHEQUE, COL_PAYEE

    register.set_active_payee("alnic logistics")
    register.set_active_cheque("chq-441")

    assert register._table.item(0, COL_PAYEE).text() == "ALNIC LOGISTICS"
    assert register._table.item(0, COL_CHEQUE).text() == "CHQ-441"


def test_active_row_change_emits_payee_cheque(register):
    from tahmeed.ui.cashier.register_delegates import COL_CHEQUE, COL_DESC, COL_PAYEE

    seen = []
    register.active_payee_cheque_changed.connect(
        lambda p, c, e: seen.append((p, c, e))
    )
    register._table.item(0, COL_PAYEE).setText("SUPPLIER A")
    register._table.item(0, COL_CHEQUE).setText("99")
    register._table.setCurrentCell(0, COL_DESC)
    register._emit_active_payee_cheque()

    assert seen[-1][0] == "SUPPLIER A"
    assert seen[-1][1] == "99"
    assert seen[-1][2] is True


def test_saved_row_readonly_until_edit_mode(register):
    from tahmeed.ui.cashier.register_delegates import COL_CHEQUE, COL_PAYEE
    from PySide6.QtWidgets import QTableWidgetItem
    from PySide6.QtCore import Qt

    register._saved_count = 1
    register._edit_mode = False
    for col in (COL_PAYEE, COL_CHEQUE):
        it = QTableWidgetItem("EXISTING" if col == COL_PAYEE else "1")
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        register._table.setItem(0, col, it)
    register._table.setCurrentCell(0, COL_PAYEE)

    register.set_active_payee("SHOULD NOT APPLY")
    assert register._table.item(0, COL_PAYEE).text() == "EXISTING"

    register._edit_mode = True
    register.set_active_payee("updated payee")
    assert register._table.item(0, COL_PAYEE).text() == "UPDATED PAYEE"
