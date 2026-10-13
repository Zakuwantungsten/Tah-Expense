"""Cut/copy/paste must keep Description/Item/etc. on the correct columns."""
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
    from tahmeed.ui.cashier.register_delegates import (
        COL_CASHIER,
        COL_DESC,
        COL_ITEM,
        COL_TZS,
        HEADERS,
    )
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidgetItem

    user = MagicMock()
    user._id = "cashier-1"
    user.role = "cashier"
    reg = DailyRegister(user, [])
    reg._merged_mode = False
    reg._saved_count = 0
    reg._table.setRowCount(3)
    reg._init_editable_rows(0, 3)

    def put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        reg._table.setItem(row, col, it)

    put(0, COL_ITEM, "FUEL")
    put(0, COL_DESC, "DIESEL")
    put(0, COL_TZS, "1,000.00")
    put(0, COL_CASHIER, "Aisha")
    reg._HEADERS = HEADERS
    reg._COL_ITEM = COL_ITEM
    reg._COL_DESC = COL_DESC
    reg._COL_TZS = COL_TZS
    return reg


def test_tsv_full_width_maps_to_correct_columns(register):
    cells = register._serialize_row(0)
    values = register._tsv_cells_to_column_map(cells)
    assert values[register._COL_ITEM] == "FUEL"
    assert values[register._COL_DESC] == "DIESEL"
    assert values[register._COL_TZS] == "1,000.00"


def test_tsv_legacy_data_only_still_aligns(register):
    """Older copy omitted READONLY cols — must not shift Date into Item."""
    from tahmeed.ui.cashier.register_delegates import READONLY_COLS

    ncols = register._table.columnCount()
    data_cols = [c for c in range(ncols) if c not in READONLY_COLS]
    legacy = []
    for col in data_cols:
        it = register._table.item(0, col)
        legacy.append(it.text() if it else "")
    values = register._tsv_cells_to_column_map(legacy)
    assert values[register._COL_ITEM] == "FUEL"
    assert values[register._COL_DESC] == "DIESEL"
    assert values[register._COL_TZS] == "1,000.00"


def test_full_row_copy_uses_serialize_width(register):
    from PySide6.QtWidgets import QApplication

    register._table.selectRow(0)
    # Force full-row detection
    assert register._selection_is_full_rows([0])
    register._copy()
    text = QApplication.clipboard().text()
    cells = text.split("\t")
    assert len(cells) == register._table.columnCount()
    values = register._tsv_cells_to_column_map(cells)
    assert values[register._COL_DESC] == "DIESEL"
    assert values[register._COL_ITEM] == "FUEL"
