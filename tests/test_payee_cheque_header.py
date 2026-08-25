"""Payee/Cheque header fields stamp all data rows without Verify dirtying."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _put_desc(register, row: int, text: str = "FUEL") -> None:
    from tahmeed.ui.cashier.register_delegates import COL_DESC, NEW_BG
    from PySide6.QtWidgets import QTableWidgetItem
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QBrush

    it = QTableWidgetItem(text)
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
    it.setBackground(QBrush(NEW_BG))
    register._table.setItem(row, COL_DESC, it)
    register._activate_row(row)


@pytest.fixture
def register(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    from tahmeed.ui.cashier.excel_grid import DailyRegister
    from tahmeed.ui.cashier.register_delegates import (
        COL_CHEQUE, COL_PAYEE, DEFAULT_EDITABLE_ROWS, NEW_BG,
    )
    from PySide6.QtWidgets import QTableWidgetItem
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QBrush

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


def test_header_stamps_all_filled_rows_not_blanks(register):
    from tahmeed.ui.cashier.register_delegates import COL_CHEQUE, COL_PAYEE

    _put_desc(register, 0, "DIESEL")
    _put_desc(register, 1, "OIL")
    # Row 2 stays blank — must not receive the stamp.

    register.set_active_payee("alnic logistics")
    register.set_active_cheque("chq-441")

    assert register._table.item(0, COL_PAYEE).text() == "ALNIC LOGISTICS"
    assert register._table.item(0, COL_CHEQUE).text() == "CHQ-441"
    assert register._table.item(1, COL_PAYEE).text() == "ALNIC LOGISTICS"
    assert register._table.item(1, COL_CHEQUE).text() == "CHQ-441"
    assert register._table.item(2, COL_PAYEE).text() == ""
    assert register._table.item(2, COL_CHEQUE).text() == ""


def test_header_stamps_saved_rows_without_edit_mode(register):
    from tahmeed.ui.cashier.register_delegates import COL_CHEQUE, COL_DESC, COL_PAYEE
    from PySide6.QtWidgets import QTableWidgetItem
    from PySide6.QtCore import Qt

    register._saved_count = 2
    register._edit_mode = False
    for row in range(2):
        for col, val in ((COL_DESC, f"ROW{row}"), (COL_PAYEE, ""), (COL_CHEQUE, "")):
            it = QTableWidgetItem(val)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            register._table.setItem(row, col, it)
        register._saved_ids[row] = f"id-{row}"
        register._saved_txs[row] = SimpleNamespace(
            payee="", cheque="", register_status="submitted", verified=False,
        )

    register.set_active_payee("shared payee")
    register.set_active_cheque("99")

    assert register._table.item(0, COL_PAYEE).text() == "SHARED PAYEE"
    assert register._table.item(1, COL_PAYEE).text() == "SHARED PAYEE"
    assert register._table.item(0, COL_CHEQUE).text() == "99"
    assert register._table.item(1, COL_CHEQUE).text() == "99"
    # Must not enter the Verify → Edited dirty path.
    assert register._dirty_rows == set()


def test_header_stamp_does_not_mark_saved_rows_dirty_in_edit_mode(register):
    from tahmeed.ui.cashier.register_delegates import COL_CHEQUE, COL_DESC, COL_PAYEE
    from PySide6.QtWidgets import QTableWidgetItem
    from PySide6.QtCore import Qt

    register._saved_count = 1
    register._edit_mode = True
    register._table.blockSignals(True)
    for col, val in ((COL_DESC, "PARKING"), (COL_PAYEE, "OLD"), (COL_CHEQUE, "1")):
        it = QTableWidgetItem(val)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        register._table.setItem(0, col, it)
    register._table.blockSignals(False)
    register._dirty_rows.clear()
    register._saved_ids[0] = "id-0"
    register._saved_txs[0] = SimpleNamespace(
        payee="OLD", cheque="1", register_status="submitted", verified=False,
    )

    register.set_active_payee("new payee")
    register.set_active_cheque("2")

    assert register._table.item(0, COL_PAYEE).text() == "NEW PAYEE"
    assert register._table.item(0, COL_CHEQUE).text() == "2"
    assert register._dirty_rows == set()


def test_header_emit_uses_day_level_values(register):
    seen = []
    register.active_payee_cheque_changed.connect(
        lambda p, c, e: seen.append((p, c, e))
    )
    _put_desc(register, 0, "TOLLS")
    register.set_active_payee("supplier a")
    register.set_active_cheque("99")
    register._emit_active_payee_cheque()

    assert seen[-1][0] == "SUPPLIER A"
    assert seen[-1][1] == "99"
    assert seen[-1][2] is True


def test_new_row_inherits_header_stamp_on_activate(register):
    from tahmeed.ui.cashier.register_delegates import COL_CHEQUE, COL_PAYEE

    register._header_payee = "ACME"
    register._header_cheque = "77"
    _put_desc(register, 0, "WATER")

    assert register._table.item(0, COL_PAYEE).text() == "ACME"
    assert register._table.item(0, COL_CHEQUE).text() == "77"


@pytest.mark.asyncio
async def test_persist_header_updates_only_payee_cheque(register, monkeypatch):
    from tahmeed.ui.cashier.register_delegates import COL_CHEQUE, COL_DESC, COL_PAYEE
    from PySide6.QtWidgets import QTableWidgetItem
    from PySide6.QtCore import Qt

    calls = []

    async def fake_update(tx_id, updates):
        calls.append((tx_id, dict(updates)))
        return True

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.update_transaction", fake_update
    )

    register._saved_count = 1
    it = QTableWidgetItem("FUEL")
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    register._table.setItem(0, COL_DESC, it)
    register._table.setItem(0, COL_PAYEE, QTableWidgetItem(""))
    register._saved_ids[0] = "oid-1"
    tx = SimpleNamespace(payee="", cheque="", verified=True, register_status="submitted")
    register._saved_txs[0] = tx
    register._header_payee = "VENDOR"
    register._header_cheque = "55"
    register._write_payee_cheque_cell(0, COL_PAYEE, "VENDOR")
    register._write_payee_cheque_cell(0, COL_CHEQUE, "55")

    await register._persist_header_payee_cheque()

    assert len(calls) == 1
    assert calls[0][0] == "oid-1"
    assert calls[0][1] == {"payee": "VENDOR", "cheque": "55"}
    assert "edited_after_verification" not in calls[0][1]
    assert tx.payee == "VENDOR"
    assert tx.cheque == "55"
    assert register._dirty_rows == set()


def test_action_bar_reconciled_date_emits_and_syncs(qapp):
    from datetime import date

    from PySide6.QtCore import QDate

    from tahmeed.ui.cashier.dashboard import _ActionBar

    bar = _ActionBar()
    seen: list = []
    bar.reconciled_date_changed.connect(seen.append)

    bar.set_reconciled_date(date(2026, 8, 19))
    assert bar._reconciled.date() == QDate(2026, 8, 19)
    assert seen == []

    bar._reconciled.setDate(QDate(2026, 8, 20))
    assert seen == [date(2026, 8, 20)]
