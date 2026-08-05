"""Cut/insert and unsaved-mode guards for DailyRegister."""
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
    from tahmeed.ui.cashier.excel_grid import DailyRegister
    from tahmeed.ui.cashier.register_delegates import (
        COL_CASHIER,
        COL_DESC,
        COL_ITEM,
        COL_TZS,
        EDIT_BG,
    )
    from PySide6.QtGui import QBrush
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidgetItem

    user = MagicMock()
    user._id = "cashier-1"
    reg = DailyRegister(user, [])
    reg._merged_mode = True
    reg._edit_mode = True
    reg._cashier_names = {"c-a": "Aisha", "c-b": "John"}

    # Two draft saved rows + blank editable space
    reg._table.setRowCount(5)
    reg._saved_count = 2
    reg._saved_ids = {0: "tx-0", 1: "tx-1"}
    reg._saved_txs = {
        0: SimpleNamespace(
            _id="tx-0",
            cashier_id="c-a",
            register_status="draft",
            verified=False,
        ),
        1: SimpleNamespace(
            _id="tx-1",
            cashier_id="c-b",
            register_status="draft",
            verified=False,
        ),
    }

    def _put(row, col, text, readonly=False):
        it = QTableWidgetItem(text)
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if not readonly:
            flags |= Qt.ItemIsEditable
        it.setFlags(flags)
        it.setBackground(QBrush(EDIT_BG))
        reg._table.setItem(row, col, it)

    _put(0, COL_ITEM, "FUEL")
    _put(0, COL_DESC, "DIESEL")
    _put(0, COL_TZS, "1,000.00")
    _put(0, COL_CASHIER, "Aisha", readonly=True)

    _put(1, COL_ITEM, "PARKING")
    _put(1, COL_DESC, "NIGHT")
    _put(1, COL_TZS, "500.00")
    _put(1, COL_CASHIER, "John", readonly=True)

    reg._init_editable_rows(2, 5)
    return reg


def test_has_unsaved_work_detects_typed_new_rows(register):
    from tahmeed.ui.cashier.register_delegates import COL_DESC
    from PySide6.QtWidgets import QTableWidgetItem

    register._dirty_rows.clear()
    register._pending_row_meta.clear()
    # Clear any leftover editable cells from widget construction.
    for row in range(register._saved_count, register._table.rowCount()):
        for col in range(register._table.columnCount()):
            it = register._table.item(row, col)
            if it is not None and col != 0:
                it.setText("")

    assert register.has_unsaved_work() is False
    register._table.setItem(2, COL_DESC, QTableWidgetItem("NEW ENTRY"))
    assert register.has_unsaved_work() is True


def test_cut_insert_preserves_cashier_and_tx_id(register):
    from tahmeed.ui.cashier.register_delegates import COL_CASHIER, COL_DESC, COL_ITEM

    # Cut row 0 (Aisha) and insert at row 1 (before John)
    register._table.setCurrentCell(1, COL_DESC)
    register._table.selectRow(0)
    register._cut()
    assert register._has_cut_buffer()
    assert register._cut_payload.get("row_metas")

    register._insert_cut_cells()

    assert register._saved_count == 2
    # Aisha should now be at row 0 still or row 1 depending on insert-before;
    # insert_at was current row 1, source 0 removed first → insert_at=0.
    # After move: Aisha at 0, John at 1 — or Aisha inserted at 0 pushing John.
    cashiers = [
        (register._table.item(r, COL_CASHIER).text() if register._table.item(r, COL_CASHIER) else "")
        for r in range(2)
    ]
    assert "Aisha" in cashiers
    assert "John" in cashiers
    assert all(cashiers), f"Cashier cells blank: {cashiers}"

    assert set(register._saved_ids.values()) == {"tx-0", "tx-1"}
    assert register._saved_ids[0] in {"tx-0", "tx-1"}
    assert register._saved_ids[1] in {"tx-0", "tx-1"}

    # Data cells still present (not wiped)
    assert register._table.item(0, COL_DESC).text()
    assert register._table.item(1, COL_DESC).text()
    assert register._table.item(0, COL_ITEM).text()
    assert register._table.item(1, COL_ITEM).text()
