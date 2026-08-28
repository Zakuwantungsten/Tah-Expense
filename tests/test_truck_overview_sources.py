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
    from tahmeed.services.accountant_service import _truck_and_trailer_match

    clause = _truck_and_trailer_match("T469 EKZ")
    import re as _re

    compiled = _re.compile(clause["$regex"], _re.IGNORECASE)
    assert compiled.search("T469EKZ/T689ELK")
    assert compiled.search("T469 EKZ/T689ELK")
    assert not compiled.search("T689ELK/T469EKZ")


def test_filter_rows_sorts_date_ascending() -> None:
    rows = [
        {"source_group": "master", "date": datetime(2026, 12, 1), "description": "dec"},
        {"source_group": "master", "date": datetime(2026, 1, 15), "description": "jan"},
        {"source_group": "master", "date": datetime(2026, 6, 1), "description": "jun"},
    ]
    filtered = _filter_truck_overview_rows(rows)
    assert [r["description"] for r in filtered] == ["jan", "jun", "dec"]


def test_truck_exact_matches_compact_and_spaced_plates() -> None:
    import re as _re

    from tahmeed.services.accountant_service import _truck_exact

    clause = _truck_exact("T103 DVL")
    compiled = _re.compile(clause["$regex"], _re.IGNORECASE)
    assert compiled.search("T103 DVL")
    assert compiled.search("T103DVL")
    assert compiled.search("T103DVL/T200 XXX")
    assert not compiled.search("T102 DVL")
    assert not compiled.search("T1030 DVL")


def test_normalize_sources_diesel_imports_expands_stations() -> None:
    from tahmeed.services.accountant_service import (
        _DIESEL_STATION_SOURCE_GROUPS,
        _normalize_truck_overview_sources,
    )

    wanted = _normalize_truck_overview_sources("diesel_imports")
    assert wanted == set(_DIESEL_STATION_SOURCE_GROUPS)
    assert "diesel_imports" not in wanted


def test_filter_rows_diesel_imports_matches_station_groups() -> None:
    rows = [
        {"source_group": "infinity", "date": None, "description": "inf"},
        {"source_group": "master", "date": None, "description": "m"},
        {"source_group": "gbp_diesel", "date": None, "description": "gbp"},
    ]
    filtered = _filter_truck_overview_rows(rows, source="diesel_imports")
    assert [r["description"] for r in filtered] == ["inf", "gbp"]


def test_diesel_overview_query_uses_transaction_date_not_display_date() -> None:
    """Station imports store the real datetime on transaction_date."""
    from tahmeed.services.accountant_service import _diesel_overview_query

    query = _diesel_overview_query(
        "diesel_infinity",
        "T103 DVL",
        datetime(2026, 1, 1),
        datetime(2026, 12, 31),
    )
    assert query["feed_type"] == "diesel_infinity"
    assert "transaction_date" in query
    assert "date" not in query
    assert query["transaction_date"]["$gte"] == datetime(2026, 1, 1, 0, 0, 0)
    assert query["transaction_date"]["$lte"] == datetime(2026, 12, 31, 23, 59, 59)
    assert "$regex" in query["truck_no"]


def test_imported_feed_overview_queries_use_transaction_date() -> None:
    """Toll / Parking Congo / RahnTech tabs range on transaction_date, not display strings."""
    from tahmeed.services.accountant_service import _imported_feed_overview_query

    cases = (
        ("toll_plaza", "vehicle_reg", "toll_date"),
        ("parking_congo", "vehicle_no", "payment_date"),
        ("rahntech", "truck_number", "sales_date"),
    )
    for feed_type, truck_field, display_field in cases:
        query = _imported_feed_overview_query(
            feed_type,
            truck_field,
            "T103 DVL",
            datetime(2026, 1, 1),
            datetime(2026, 12, 31),
        )
        assert query["feed_type"] == feed_type
        assert "transaction_date" in query
        assert display_field not in query
        assert query["transaction_date"]["$gte"] == datetime(2026, 1, 1, 0, 0, 0)
        assert query["transaction_date"]["$lte"] == datetime(2026, 12, 31, 23, 59, 59)
        assert "$regex" in query[truck_field]

def test_truck_overview_covers_separate_expense_tabs() -> None:
    from tahmeed.services.accountant_service import _TRUCK_OVERVIEW_SOURCES

    assert {
        "toll_plaza",
        "parking_congo",
        "zambia_parking",
        "congo_expenses",
        "ahmed_kimvi",
        "afritrack",
        "third_party",
        "comesa",
        "sm_burhani",
        "rahntech",
    }.issubset(_TRUCK_OVERVIEW_SOURCES)


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

    from PySide6.QtWidgets import QApplication, QFrame, QPushButton

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
        assert not hasattr(w, "_search")
        title_bar = w.findChild(QFrame, "truckTitleBar")
        assert title_bar is not None
        labels = {b.text().strip() for b in title_bar.findChildren(QPushButton)}
        assert "Excel" in labels
        assert "PDF" in labels
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        asyncio.set_event_loop(None)
