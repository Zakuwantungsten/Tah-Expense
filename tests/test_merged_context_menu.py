"""Merged context menu: copy + own-delete; no entry actions while browsing."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QMenu


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def merged_register(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    user = MagicMock()
    user._id = "cashier-1"
    user.role = "cashier"
    reg = DailyRegister(user, [])
    reg._merged_mode = True
    reg._edit_mode = False
    reg._saved_count = 2
    reg._saved_ids = {0: "tx-mine", 1: "tx-other"}
    reg._saved_txs = {
        0: SimpleNamespace(_id="tx-mine", cashier_id="cashier-1", verified=False),
        1: SimpleNamespace(_id="tx-other", cashier_id="cashier-2", verified=False),
    }
    reg._table.setRowCount(2)
    reg._table.setCurrentCell(0, 3)
    return reg


def _action_texts(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if a.text()]


def test_merged_browse_menu_has_copy_and_own_delete(merged_register, monkeypatch):
    monkeypatch.setattr(
        merged_register, "_selection_is_full_rows", lambda _rows: True
    )
    merged_register._table.selectRow(0)
    menu = QMenu()
    merged_register._populate_context_menu(menu)
    texts = _action_texts(menu)
    assert "Copy" in texts
    assert any(t.startswith("Delete Saved") for t in texts)
    assert "Insert Row Above" not in texts
    assert "Paste" not in texts


def test_merged_browse_others_row_copy_only(merged_register):
    merged_register._table.setCurrentCell(1, 3)
    menu = QMenu()
    merged_register._populate_context_menu(menu)
    texts = _action_texts(menu)
    assert texts == ["Copy"]


def test_merged_edit_menu_allows_cut_insert_not_blank_rows(merged_register):
    merged_register._edit_mode = True
    menu = QMenu()
    merged_register._populate_context_menu(menu)
    texts = _action_texts(menu)
    assert "Copy" in texts
    assert "Cut" in texts
    assert "Paste" in texts
    assert "Insert Row Above" not in texts
    assert "Delete Selected Row(s)" not in texts
