"""Tests for truck-overview multi-source filtering."""

from datetime import datetime

from tahmeed.services.accountant_service import (
    _filter_truck_overview_rows,
    _normalize_truck_overview_sources,
    _with_date_range,
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


def test_filter_rows_by_year_month_window() -> None:
    rows = [
        {"source_group": "master", "date": datetime(2024, 6, 15), "description": "old"},
        {"source_group": "master", "date": datetime(2026, 3, 10), "description": "in"},
        {"source_group": "toll_plaza", "date": datetime(2026, 8, 1), "description": "late"},
    ]
    filtered = _filter_truck_overview_rows(
        rows,
        date_from=datetime(2026, 1, 1),
        date_to=datetime(2026, 3, 31, 23, 59, 59),
    )
    assert [r["description"] for r in filtered] == ["in"]


def test_with_date_range_adds_inclusive_window() -> None:
    query = _with_date_range(
        {"verified": True, "truck_number": "T123"},
        "date",
        datetime(2026, 1, 1),
        datetime(2026, 12, 31),
    )
    assert query["verified"] is True
    assert query["truck_number"] == "T123"
    assert query["date"]["$gte"] == datetime(2026, 1, 1, 0, 0, 0)
    assert query["date"]["$lte"] == datetime(2026, 12, 31, 23, 59, 59)


def test_with_date_range_noop_when_unset() -> None:
    base = {"feed_type": "toll_plaza"}
    assert _with_date_range(base, "toll_date") == base


def test_truck_and_trailer_overview_match() -> None:
    from tahmeed.services.accountant_service import (
        _truck_and_trailer_match,
        _truck_row_matches_search,
    )

    clause = _truck_and_trailer_match("T469 EKZ")
    import re as _re

    compiled = _re.compile(clause["$regex"], _re.IGNORECASE)
    assert compiled.search("T469EKZ/T689ELK")
    assert compiled.search("T469 EKZ/T689ELK")
    assert not compiled.search("T689ELK/T469EKZ")

    assert _truck_row_matches_search(
        {"truck_value": "T469EKZ/T689ELK", "source": "SM Burhani RPA"},
        "T469 EKZ",
    )
    assert not _truck_row_matches_search(
        {"truck_value": "T688 EAF/T123 TRA", "source": "SM Burhani RPA"},
        "T469 EKZ",
    )


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


def test_truck_overview_defaults_to_fiscal_year() -> None:
    import asyncio

    from PySide6.QtWidgets import QApplication

    from tahmeed.app_state import app_state
    from tahmeed.ui.accountant.truck_overview import TruckOverviewWidget

    _app = QApplication.instance() or QApplication([])
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        w = TruckOverviewWidget()
        assert w._year == app_state.fiscal_year
        assert w._month == 0
        assert w._year_cb.currentData() == app_state.fiscal_year
        assert w._month_cb.isEnabled()
        date_from, date_to = w._date_filters()
        assert date_from is not None and date_from.year == app_state.fiscal_year
        assert date_to is not None and date_to.year == app_state.fiscal_year
        assert date_from.month == 1 and date_from.day == 1
        assert date_to.month == 12 and date_to.day == 31
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        asyncio.set_event_loop(None)
