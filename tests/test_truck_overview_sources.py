"""Tests for truck-overview multi-source filtering."""

from tahmeed.services.accountant_service import (
    _filter_truck_overview_rows,
    _normalize_truck_overview_sources,
)


def test_normalize_sources_all_variants() -> None:
    assert _normalize_truck_overview_sources("all") is None
    assert _normalize_truck_overview_sources([]) is None
    assert _normalize_truck_overview_sources(["all", "master"]) is None
    assert _normalize_truck_overview_sources(None) is None


def test_normalize_sources_single_and_multi() -> None:
    assert _normalize_truck_overview_sources("master") == {"master"}
    assert _normalize_truck_overview_sources(["master", "diesel_cash"]) == {
        "master",
        "diesel_cash",
    }
    assert _normalize_truck_overview_sources(["nope"]) is None


def test_filter_rows_accepts_multiple_sources() -> None:
    rows = [
        {"source_group": "master", "date": None, "description": "a"},
        {"source_group": "diesel_cash", "date": None, "description": "b"},
        {"source_group": "toll_plaza", "date": None, "description": "c"},
    ]
    filtered = _filter_truck_overview_rows(
        rows, source=["master", "toll_plaza"]
    )
    assert [r["source_group"] for r in filtered] == ["master", "toll_plaza"]


def test_source_multi_combo_selects_multiple() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from tahmeed.ui.accountant.truck_overview import _SOURCE_OPTIONS, _SourceMultiCombo

    _app = QApplication.instance() or QApplication([])
    combo = _SourceMultiCombo(_SOURCE_OPTIONS)
    assert combo.selected_keys() == []
    assert combo.summary_text() == "All Sources"

    # Check Master + Diesel Cash
    combo.model().item(1).setCheckState(Qt.Checked)
    combo.model().item(2).setCheckState(Qt.Checked)
    keys = combo.selected_keys()
    assert "master" in keys
    assert "diesel_cash" in keys
    assert combo.summary_text() == "Master Expenses, Diesel Cash"

    combo.reset_to_all()
    assert combo.selected_keys() == []
    assert combo.summary_text() == "All Sources"
