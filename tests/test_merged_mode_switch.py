"""My ↔ Merged toggle stashes local draft and never prompts to save."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
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
def register(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "tahmeed.services.register_draft_service.drafts_root",
        lambda: Path(tmp_path),
    )
    from tahmeed.ui.cashier.excel_grid import DailyRegister
    from tahmeed.ui.cashier.register_delegates import COL_DESC
    from PySide6.QtWidgets import QTableWidgetItem
    from bson import ObjectId

    user = MagicMock()
    user._id = ObjectId()
    user.role = "cashier"
    user.username = "cash1"
    reg = DailyRegister(user, [])
    reg._merged_mode = False
    reg._current_date = date(2026, 8, 23)
    # Clear constructor blank rows so has_unsaved_work starts False.
    for row in range(reg._table.rowCount()):
        for col in range(reg._table.columnCount()):
            it = reg._table.item(row, col)
            if it is not None and col != 0:
                it.setText("")
    reg._saved_count = 0
    reg._table.setItem(0, COL_DESC, QTableWidgetItem("UNSAVED ROW"))
    assert reg.has_unsaved_work() is True
    return reg


def test_set_merged_mode_does_not_prompt(register, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    def boom(*_a, **_k):
        raise AssertionError("mode switch must not show save/discard dialog")

    monkeypatch.setattr(QMessageBox, "question", boom)
    monkeypatch.setattr(register, "_prompt_unsaved_work", boom)
    monkeypatch.setattr(register, "_load_date", lambda *_a, **_k: None)

    register.set_merged_mode(True)
    assert register._merged_mode is True


def test_set_merged_mode_stashes_my_local_draft(register, monkeypatch):
    from tahmeed.services.register_draft_service import load_register_draft

    monkeypatch.setattr(register, "_load_date", lambda *_a, **_k: None)
    uid = register._user._id
    day = register._current_date

    register.set_merged_mode(True)

    my_draft = load_register_draft(uid, day, merged=False)
    assert my_draft is not None
    new_rows = my_draft.get("new_rows") or []
    assert len(new_rows) >= 1
    # Merged file must not steal My unsaved rows on switch.
    merged_draft = load_register_draft(uid, day, merged=True)
    assert merged_draft is None
