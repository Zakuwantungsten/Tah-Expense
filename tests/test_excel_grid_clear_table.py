"""Clear Table toolbar — wipe unsaved rows with undo support."""
from __future__ import annotations

import sys
from datetime import date, datetime
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


@pytest.fixture
def register(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.clear_register_draft",
        lambda *_a, **_k: None,
    )
    from tahmeed.ui.cashier.excel_grid import DailyRegister
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TZS
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidgetItem, QMessageBox

    user = MagicMock()
    user._id = "cashier-1"
    reg = DailyRegister(user, [])
    reg._merged_mode = True
    reg._current_date = date(2026, 8, 23)

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        reg._table.setItem(row, col, it)

    reg._put = _put
    reg._COL_DESC = COL_DESC
    reg._COL_ITEM = COL_ITEM
    reg._COL_TZS = COL_TZS

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.question",
        lambda *_a, **_k: QMessageBox.Yes,
    )
    return reg


def test_clear_table_wipes_unsaved_rows(register):
    register._table.setRowCount(12)
    register._saved_count = 1
    register._saved_ids = {0: "tx-0"}
    register._saved_txs = {
        0: SimpleNamespace(
            _id="tx-0",
            date=datetime(2026, 8, 23),
            description="SAVED",
            item="FUEL",
            truck_number="T100 ABC",
            amount=1000.0,
            currency="TZS",
            amount_usd=0.0,
            memo="",
            receipt_status="pending",
            notes_flag=False,
            ref_float="",
            ownership="",
            approver="",
            payee="",
            cheque="",
            cashier_id="c-a",
        ),
    }
    register._cashier_names = {"c-a": "Aisha"}

    register._put(0, register._COL_DESC, "SAVED")
    register._put(1, register._COL_ITEM, "FUEL")
    register._put(1, register._COL_DESC, "UNSAVED")
    register._put(1, register._COL_TZS, "2,000.00")

    register._clear_unsaved_with_undo()

    assert register._table.item(0, register._COL_DESC).text() == "SAVED"
    assert not register._row_has_data(1)
    assert register._undo_stack


def test_clear_table_does_not_fill_receipt_with_pending(register):
    from tahmeed.ui.cashier.register_delegates import COL_RECEIPT

    register._table.setRowCount(12)
    register._saved_count = 0
    for row in range(5):
        register._put(row, register._COL_DESC, f"ROW {row + 1}")
        register._activate_row(row)

    register._clear_unsaved_with_undo()

    for row in range(5):
        rcpt = register._table.item(row, COL_RECEIPT)
        assert rcpt is None or not (rcpt.text() or "").strip()


def test_clear_table_undo_restores_unsaved_row(register):
    register._table.setRowCount(12)
    register._saved_count = 0

    register._put(0, register._COL_ITEM, "FUEL")
    register._put(0, register._COL_DESC, "DIESEL")
    register._put(0, register._COL_TZS, "1,000.00")

    register._clear_unsaved_with_undo()
    assert not register._row_has_data(0)

    register._undo()
    assert register._table.item(0, register._COL_DESC).text() == "DIESEL"
    assert register._table.item(0, register._COL_TZS).text() == "1,000.00"
