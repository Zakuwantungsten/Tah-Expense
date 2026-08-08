"""DailyRegister local draft capture / restore."""

from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bson import ObjectId


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def register(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    from tahmeed.services import register_draft_service as drafts
    from tahmeed.ui.cashier.excel_grid import DailyRegister
    from tahmeed.ui.cashier.register_delegates import (
        COL_DESC,
        COL_ITEM,
        COL_TZS,
        EDIT_BG,
    )
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QBrush
    from PySide6.QtWidgets import QTableWidgetItem

    monkeypatch.setattr(drafts, "drafts_root", lambda: tmp_path / "register_drafts")

    user = MagicMock()
    user._id = ObjectId()
    user.username = "aisha"
    reg = DailyRegister(user, [])
    reg._current_date = date(2026, 8, 8)
    reg._merged_mode = False
    reg._edit_mode = True

    tx0 = ObjectId()
    reg._table.setRowCount(6)
    reg._saved_count = 1
    reg._saved_ids = {0: tx0}
    reg._saved_txs = {
        0: SimpleNamespace(
            _id=tx0,
            cashier_id=user._id,
            register_status="draft",
            verified=False,
        ),
    }

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        it.setBackground(QBrush(EDIT_BG))
        reg._table.setItem(row, col, it)

    _put(0, COL_ITEM, "FUEL")
    _put(0, COL_DESC, "DIESEL")
    _put(0, COL_TZS, "1,000.00")
    reg._init_editable_rows(1, 6)
    reg._tx0 = tx0
    return reg


def test_flush_and_restore_new_and_dirty_rows(register):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TZS
    from PySide6.QtWidgets import QTableWidgetItem

    register._dirty_rows.add(0)
    register._table.item(0, COL_DESC).setText("DIESEL EDITED")
    register._table.setItem(1, COL_DESC, QTableWidgetItem("NEW PARKING"))
    register._table.setItem(1, COL_ITEM, QTableWidgetItem("PARKING"))
    register._table.setItem(1, COL_TZS, QTableWidgetItem("5,000.00"))

    register._flush_local_draft()

    # Simulate reload wipe
    register._dirty_rows.clear()
    register._table.item(0, COL_DESC).setText("DIESEL")
    for row in range(register._saved_count, register._table.rowCount()):
        for col in (COL_DESC, COL_ITEM, COL_TZS):
            it = register._table.item(row, col)
            if it is not None:
                it.setText("")

    restored = register._restore_local_draft()
    assert restored == (1, 1)
    assert 0 in register._dirty_rows
    assert register._table.item(0, COL_DESC).text() == "DIESEL EDITED"
    assert register._table.item(1, COL_DESC).text() == "NEW PARKING"
    assert register._table.item(1, COL_TZS).text() == "5,000.00"


def test_clear_local_draft_removes_file(register):
    from tahmeed.ui.cashier.register_delegates import COL_DESC
    from PySide6.QtWidgets import QTableWidgetItem
    from tahmeed.services.register_draft_service import load_register_draft

    register._table.setItem(1, COL_DESC, QTableWidgetItem("TEMP"))
    register._flush_local_draft()
    assert load_register_draft(
        register._user._id, register._current_date, merged=False
    ) is not None

    register._clear_local_draft()
    assert load_register_draft(
        register._user._id, register._current_date, merged=False
    ) is None
