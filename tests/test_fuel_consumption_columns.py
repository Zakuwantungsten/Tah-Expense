"""Fuel station tables bind column persistence without crashing."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from tahmeed.ui.accountant.fuel_consumption import _DieselAllEntries, _FUEL_SCHEMAS


def test_diesel_all_entries_table_binds_column_widths() -> None:
    _app = QApplication.instance() or QApplication([])
    feed_type = next(iter(_FUEL_SCHEMAS))
    widget = _DieselAllEntries(feed_type)
    assert widget._table.columnCount() == len(widget._columns)
    assert widget._table.horizontalHeaderItem(widget._table.columnCount() - 1).text() == "UPLOAD DESCRIPTION"
