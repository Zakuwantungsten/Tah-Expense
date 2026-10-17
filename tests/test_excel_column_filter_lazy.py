"""Excel ▾ filter popup supports lazy async value fill."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from tahmeed.ui.widgets.excel_column_filter import ExcelColumnFilterPopup


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_replace_values_updates_checklist_and_clears_loading() -> None:
    _app()
    popup = ExcelColumnFilterPopup(
        {"Seed"},
        set(),
        column_label="Item",
        loading=True,
    )
    assert popup._hint is not None
    assert "Loading" in popup._hint.text()
    # Apply must stay usable on seed values while the full list loads.
    assert popup._apply_btn is not None
    assert popup._apply_btn.isEnabled() is True
    assert popup._list is not None
    assert popup._list.isEnabled() is True

    popup.replace_values({"Fuel", "Parking"})
    assert popup._hint.text() == "2 value(s) in this table"
    assert popup._apply_btn.isEnabled() is True
    assert popup._all_values == ["Fuel", "Parking"]
    assert popup._list.count() == 2
    popup.close()


def test_replace_values_noop_sort_only() -> None:
    _app()
    popup = ExcelColumnFilterPopup(
        set(),
        set(),
        column_label="Date",
        sort_only=True,
        loading=True,
    )
    popup.replace_values({"18 Jul"})
    assert popup._list is None
    popup.close()
