"""Merged view has no data-entry surface — My entries only."""
from __future__ import annotations

import sys
from datetime import date
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
    from tahmeed.ui.cashier.register_delegates import DEFAULT_EDITABLE_ROWS

    user = MagicMock()
    user._id = "cashier-1"
    user.role = "cashier"
    user.username = "cash1"
    reg = DailyRegister(user, [])
    reg._current_date = date(2026, 8, 23)
    reg._DEFAULT_EDITABLE_ROWS = DEFAULT_EDITABLE_ROWS
    return reg


def _tx(desc: str, cashier_id, *, tx_id: str):
    from tahmeed.models.transaction import Transaction
    from datetime import datetime

    tx = Transaction(
        date=datetime(2026, 8, 23),
        description=desc,
        truck_number="",
        amount=1.0,
        cashier_id=cashier_id,
        register_status="draft",
    )
    tx._id = tx_id
    return tx


def test_populate_merged_has_no_blank_entry_rows(register):
    register._merged_mode = True
    txs = [
        _tx("A", register._user._id, tx_id="tx-0"),
        _tx("B", "other", tx_id="tx-1"),
    ]
    register._populate(txs)
    assert register._saved_count == 2
    assert register._table.rowCount() == 2
    assert register._cell_editable(0, 3) is False  # not edit mode
    assert register._cell_editable(2, 3) is False  # no entry rows


def test_populate_my_keeps_blank_entry_rows(register):
    from tahmeed.ui.cashier.register_delegates import DEFAULT_EDITABLE_ROWS

    register._merged_mode = False
    register._populate([_tx("MINE", register._user._id, tx_id="tx-0")])
    assert register._saved_count == 1
    assert register._table.rowCount() == 1 + DEFAULT_EDITABLE_ROWS
    assert register._cell_editable(1, 3) is True


def test_insert_row_noop_in_merged(register):
    register._merged_mode = True
    register._saved_count = 1
    register._table.setRowCount(1)
    before = register._table.rowCount()
    register._insert_above()
    register._insert_below()
    assert register._table.rowCount() == before


def test_append_editable_rows_noop_in_merged(register):
    register._merged_mode = True
    register._table.setRowCount(2)
    register._saved_count = 2
    register._append_editable_rows(10)
    assert register._table.rowCount() == 2


def test_capture_local_draft_skips_new_rows_in_merged(register):
    from tahmeed.ui.cashier.register_delegates import COL_DESC
    from PySide6.QtWidgets import QTableWidgetItem

    register._merged_mode = True
    register._saved_count = 0
    register._table.setRowCount(1)
    register._table.setItem(0, COL_DESC, QTableWidgetItem("SHOULD NOT CAPTURE"))
    payload = register._capture_local_draft()
    assert payload.get("new_rows") == []
