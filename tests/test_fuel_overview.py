"""Tests for Fuel Overview (fuel-only truck view)."""

from datetime import datetime

from tahmeed.services.accountant_service import (
    _FUEL_OVERVIEW_SOURCES,
    _filter_fuel_overview_rows,
    _normalize_fuel_overview_sources,
    _summarize_overview_rows,
)


def test_fuel_sources_are_fuel_only() -> None:
    assert "diesel_cash" in _FUEL_OVERVIEW_SOURCES
    assert "infinity" in _FUEL_OVERVIEW_SOURCES
    assert "lake_zambia" in _FUEL_OVERVIEW_SOURCES
    assert "lake_tunduma" in _FUEL_OVERVIEW_SOURCES
    assert "gbp_diesel" in _FUEL_OVERVIEW_SOURCES
    assert "master" not in _FUEL_OVERVIEW_SOURCES
    assert "toll_plaza" not in _FUEL_OVERVIEW_SOURCES


def test_normalize_fuel_sources() -> None:
    assert _normalize_fuel_overview_sources("all") is None
    assert _normalize_fuel_overview_sources([]) is None
    assert _normalize_fuel_overview_sources(["infinity", "diesel_cash"]) == {
        "infinity",
        "diesel_cash",
    }
    assert _normalize_fuel_overview_sources(["master"]) is None
    assert _normalize_fuel_overview_sources("nope") is None


def test_filter_fuel_rows_by_station() -> None:
    rows = [
        {"source_group": "diesel_cash", "date": datetime(2026, 1, 2), "description": "cash"},
        {"source_group": "infinity", "date": datetime(2026, 1, 1), "description": "inf", "source": "Infinity Diesel"},
        {"source_group": "lake_zambia", "date": datetime(2026, 1, 3), "description": "zam", "source": "Lake Zambia Diesel"},
        {"source_group": "master", "date": datetime(2026, 1, 4), "description": "exp"},
    ]
    filtered = _filter_fuel_overview_rows(rows, source=["infinity", "lake_zambia"])
    assert [r["description"] for r in filtered] == ["inf", "zam"]


def test_fuel_overview_column_filter_and_sort() -> None:
    from tahmeed.services.accountant_service import (
        _apply_overview_column_filters,
        _finalize_fuel_overview_rows,
        overview_row_display,
    )

    rows = [
        {
            "date": datetime(2026, 1, 10),
            "source": "Infinity Diesel",
            "description": "DAR",
            "reference": "001",
            "truck_value": "T102",
            "currency": "TZS",
            "amount": 1000,
            "liters": 50,
            "rate": 20,
            "station": "INFINITY",
            "upload_description": "Batch A",
            "receipt_status": "received",
        },
        {
            "date": datetime(2026, 2, 5),
            "source": "Lake Zambia Diesel",
            "description": "NDOLA",
            "reference": "002",
            "truck_value": "T102",
            "currency": "USD",
            "amount": 25.5,
            "liters": 10,
            "rate": 2.55,
            "station": "LAKE NDOLA",
            "upload_description": "Batch B",
            "receipt_status": "pending",
        },
    ]
    filtered = _apply_overview_column_filters(
        rows,
        {"source": ["Infinity Diesel"]},
    )
    assert len(filtered) == 1
    assert filtered[0]["description"] == "DAR"

    sorted_rows = _finalize_fuel_overview_rows(
        rows,
        sort_field="date",
        sort_asc=False,
    )
    assert overview_row_display(sorted_rows[0], "source") == "Lake Zambia Diesel"

    upload_vals = {overview_row_display(r, "upload_description") for r in rows}
    assert upload_vals == {"Batch A", "Batch B"}


def test_filter_fuel_rows_all_keeps_only_loaded_groups() -> None:
    rows = [
        {"source_group": "diesel_cash", "date": datetime(2026, 2, 1), "description": "cash"},
        {"source_group": "gbp_diesel", "date": datetime(2026, 1, 1), "description": "gbp"},
    ]
    filtered = _filter_fuel_overview_rows(rows, source="all")
    assert [r["description"] for r in filtered] == ["gbp", "cash"]


def test_summarize_fuel_rows_splits_currency_and_liters() -> None:
    rows = [
        {
            "source": "Diesel Cash",
            "currency": "TZS",
            "amount": 1000,
            "liters": 40,
        },
        {
            "source": "Lake Zambia Diesel",
            "currency": "USD",
            "amount": 25.5,
            "liters": 10,
        },
        {
            "source": "Infinity Diesel",
            "currency": "TZS",
            "amount": 500,
            "liters": None,
        },
    ]
    summary = _summarize_overview_rows(rows)
    assert summary["record_count"] == 3
    assert summary["source_count"] == 3
    assert summary["tzs_total"] == 1500
    assert summary["usd_total"] == 25.5
    assert summary["zmw_total"] == 0.0
    assert summary["liters_total"] == 50.0


def test_diesel_display_label_prefers_upload_description() -> None:
    from tahmeed.services.accountant_service import diesel_display_label

    assert diesel_display_label({"upload_label": "March batch", "source_filename": "fuel.xlsx"}) == "March batch"
    assert diesel_display_label({"source_filename": "fuel.xlsx"}) == "fuel.xlsx"


def test_fuel_overview_sidebar_is_first_in_fuel_section() -> None:
    from tahmeed.ui.accountant.sidebar import _SECTIONS

    fuel_items = next(items for label, items in _SECTIONS if label == "FUEL CONSUMPTION")
    keys = [entry[0] for entry in fuel_items]
    assert keys[0] == "fuel_overview"
    assert keys[1:] == [
        "diesel_cash",
        "infinity",
        "lake_zambia",
        "lake_tunduma",
        "gbp_diesel",
    ]


def test_fuel_overview_widget_defaults_and_sources() -> None:
    import asyncio

    from PySide6.QtWidgets import QApplication, QFrame, QPushButton

    from tahmeed.app_state import app_state
    from tahmeed.ui.accountant.fuel_overview import FuelOverviewWidget, _FUEL_SOURCE_OPTIONS
    from tahmeed.ui.accountant.truck_overview import _FUEL_OVERVIEW_COLS

    _app = QApplication.instance() or QApplication([])
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        w = FuelOverviewWidget()
        assert w.PAGE_TITLE == "Fuel Overview"
        assert w.USE_EXCEL_COLUMN_FILTERS is True
        assert [c[0] for c in w.TABLE_COLS] == [c[0] for c in _FUEL_OVERVIEW_COLS]
        assert "UPLOAD DESCRIPTION" in [c[0] for c in w.TABLE_COLS]
        assert w._year == app_state.fiscal_year
        assert w._month == 0
        assert w._year_cb.currentData() == app_state.fiscal_year
        keys = [key for _label, key in _FUEL_SOURCE_OPTIONS]
        assert keys == [
            "all",
            "diesel_cash",
            "infinity",
            "lake_zambia",
            "lake_tunduma",
            "gbp_diesel",
        ]
        assert w._source_cb.selected_keys() == []
        title_bar = w.findChild(QFrame, "truckTitleBar")
        assert title_bar is not None
        labels = {b.text().strip() for b in title_bar.findChildren(QPushButton)}
        assert "Excel" in labels
        assert "PDF" in labels
        get_records, count_records, get_summary = w._overview_apis()
        assert get_records.__name__ == "get_fuel_overview_records"
        assert count_records.__name__ == "count_fuel_overview_records"
        assert get_summary.__name__ == "get_fuel_overview_summary"
        from tahmeed.ui.widgets.excel_column_filter import ExcelFilterHeaderView
        assert isinstance(w._table.horizontalHeader(), ExcelFilterHeaderView)
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        asyncio.set_event_loop(None)


def test_fuel_overview_pdf_uses_fuel_titles(tmp_path) -> None:
    from pathlib import Path

    import fitz

    from tahmeed.services.truck_overview_pdf import export_truck_overview_pdf

    path = Path(tmp_path) / "fuel_overview_test.pdf"
    rows = [
        {
            "date": datetime(2026, 1, 15),
            "source": "Diesel Cash",
            "description": "DIESEL NAKONDE 200 LTRS",
            "reference": "MEMO-01",
            "truck_value": "T123 ABC",
            "currency": "TZS",
            "amount": 450000,
            "liters": 200,
            "rate": None,
            "station": "Company",
            "receipt_status": "received",
        },
        {
            "date": datetime(2026, 3, 10),
            "source": "Infinity Diesel",
            "description": "Diesel top-up Dar",
            "reference": "LPO-88",
            "truck_value": "T123 ABC",
            "currency": "TZS",
            "amount": 225000,
            "liters": 100,
            "rate": 2250.5,
            "station": "Dar depot",
            "receipt_status": "pending",
        },
    ]
    summary = {
        "record_count": 2,
        "source_count": 2,
        "tzs_total": 675000.0,
        "usd_total": 0.0,
        "zmw_total": 0.0,
        "liters_total": 300.0,
    }
    export_truck_overview_pdf(
        str(path),
        truck="T123 ABC",
        rows=rows,
        summary=summary,
        date_from=datetime(2026, 1, 1),
        date_to=datetime(2026, 12, 31, 23, 59, 59),
        source_label="All Sources",
        generated_at=datetime(2026, 8, 17, 12, 0, 0),
        eyebrow="FUEL CONSUMPTION REPORT",
        report_title="Fuel Overview — T123 ABC",
        subtitle="Consolidated fuel record across diesel cash and all stations",
    )
    assert path.is_file()
    doc = fitz.open(str(path))
    try:
        text = "\n".join(p.get_text("text") for p in doc).replace("\xa0", " ")
        upper = text.upper()
        assert "FUEL CONSUMPTION REPORT" in upper
        assert "FUEL OVERVIEW" in upper
        assert "DIESEL CASH" in upper
        assert "INFINITY" in upper
    finally:
        doc.close()


def test_dashboard_registers_fuel_overview_page() -> None:
    from tahmeed.ui.accountant.dashboard import _LAZY_PAGE_KEYS

    assert "fuel_overview" in _LAZY_PAGE_KEYS
