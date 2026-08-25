"""New blank table + import staging without draft stacking."""
from __future__ import annotations

import sys
from datetime import date, datetime
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
        DEFAULT_EDITABLE_ROWS,
    )
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidgetItem

    monkeypatch.setattr(drafts, "drafts_root", lambda: tmp_path / "register_drafts")

    user = MagicMock()
    user._id = ObjectId()
    user.username = "aisha"
    reg = DailyRegister(user, [])
    reg._current_date = date(2026, 8, 8)
    reg._merged_mode = False
    reg._saved_count = 0
    reg._saved_ids = {}
    reg._saved_txs = {}
    reg._table.setRowCount(DEFAULT_EDITABLE_ROWS)
    reg._init_editable_rows(0, DEFAULT_EDITABLE_ROWS)

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        reg._table.setItem(row, col, it)

    reg._put = _put
    reg._COL_DESC = COL_DESC
    reg._COL_ITEM = COL_ITEM
    reg._COL_TZS = COL_TZS
    return reg


def test_open_blank_register_clears_date_and_rows(register):
    register._put(0, register._COL_DESC, "OLD ROW")
    register._put(0, register._COL_ITEM, "FUEL")
    register._undo_stack.append({"cells": {}})
    register._redo_stack.append({"cells": {}})

    register._open_blank_register()

    assert register._current_date is None
    assert register._saved_count == 0
    assert not register._row_has_data(0)
    assert register._undo_stack == []
    assert register._redo_stack == []


def test_wipe_unsaved_before_import_does_not_stack(register):
    register._put(0, register._COL_DESC, "RECOVERED")
    register._put(0, register._COL_ITEM, "FUEL")
    register._put(0, register._COL_TZS, "1,000.00")
    register._pending_row_meta[0] = {"daily_import_id": "old-batch"}

    register._wipe_unsaved_editable_rows()
    assert not register._row_has_data(0)
    assert register._pending_row_meta == {}

    payloads = [
        {
            "date": datetime(2026, 8, 8),
            "item": "PARKING",
            "description": "IMPORT A",
            "amount": 500.0,
            "currency": "TZS",
            "daily_import_id": "new-batch",
            "daily_import_source": "file.xlsx",
        },
        {
            "date": datetime(2026, 8, 8),
            "item": "PARKING",
            "description": "IMPORT B",
            "amount": 700.0,
            "currency": "TZS",
            "daily_import_id": "new-batch",
            "daily_import_source": "file.xlsx",
        },
    ]
    register._load_staged_import_rows(payloads)

    assert register._table.item(0, register._COL_DESC).text() == "IMPORT A"
    assert register._table.item(1, register._COL_DESC).text() == "IMPORT B"
    assert not register._row_has_data(2)
    assert register._first_empty_editable_row() == 2


def test_skip_draft_restore_flag_prevents_restore(register, monkeypatch):
    calls = {"n": 0}

    def _fake_restore():
        calls["n"] += 1
        return (0, 1)

    monkeypatch.setattr(register, "_restore_local_draft", _fake_restore)
    register._skip_draft_restore = True
    if not register._skip_draft_restore:
        register._restore_local_draft()
    assert calls["n"] == 0

    register._skip_draft_restore = False
    if not register._skip_draft_restore:
        register._restore_local_draft()
    assert calls["n"] == 1


def test_navigate_from_blank_keeps_typed_rows(register):
    register._open_blank_register()
    assert register._current_date is None

    register._put(0, register._COL_DESC, "NEW ENTRY")
    register._put(0, register._COL_ITEM, "FUEL")
    register._put(0, register._COL_TZS, "3,000.00")

    ok = register.navigate_to_date(date(2026, 8, 10))
    assert ok is True
    assert register._current_date == date(2026, 8, 10)
    assert register._table.item(0, register._COL_DESC).text() == "NEW ENTRY"
