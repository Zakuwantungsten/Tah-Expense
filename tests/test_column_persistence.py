"""Column widths auto-fit once, then stick across sessions."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

from tahmeed.ui.widgets.column_persistence import (
    apply_pending_column_autofit,
    bind_column_width_persistence,
    has_saved_column_widths,
)


_KEY = "test_sep_autofit_once"


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _clear() -> None:
    QSettings("Tahmeed", "tahmeed-expense").remove(f"col_widths/{_KEY}")


def test_autofit_once_then_restore_saved_width() -> None:
    _app()
    _clear()
    try:
        table = QTableWidget(0, 2)
        bind_column_width_persistence(
            table, _KEY, [80, 80], auto_fit_if_unset=True,
        )
        assert getattr(table, "_col_auto_fit_pending") is True

        table.insertRow(0)
        table.setItem(0, 0, QTableWidgetItem("WWWWWWWWWWWW"))
        table.setItem(0, 1, QTableWidgetItem("X"))
        apply_pending_column_autofit(table)

        assert getattr(table, "_col_auto_fit_pending") is False
        assert has_saved_column_widths(_KEY)

        table.setColumnWidth(0, 240)
        table._col_width_save()  # type: ignore[attr-defined]

        later = QTableWidget(0, 2)
        later.insertRow(0)
        later.setItem(0, 0, QTableWidgetItem("a much longer value than before"))
        bind_column_width_persistence(
            later, _KEY, [80, 80], auto_fit_if_unset=True,
        )
        assert getattr(later, "_col_auto_fit_pending") is False
        assert later.columnWidth(0) == 240
    finally:
        _clear()
