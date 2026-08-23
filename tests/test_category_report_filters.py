"""Item QuickReport query helpers — descriptions and column filters."""

from __future__ import annotations

from datetime import datetime

from tahmeed.services.accountant_service import (
    _category_report_query,
    _parse_master_filter_date,
)


def test_parse_master_filter_date_with_year_label():
    assert _parse_master_filter_date("23 Aug 2026", 2026) == datetime(2026, 8, 23)


def test_category_report_query_descriptions_multi_select():
    q = _category_report_query(
        "LATRA",
        descriptions=["LATRA INSPECTION", "LATRA RENEWAL"],
    )
    blob = str(q)
    assert "LATRA\\\\ INSPECTION" in blob or "LATRA INSPECTION" in blob
    assert "LATRA\\\\ RENEWAL" in blob or "LATRA RENEWAL" in blob
    assert q["category_name"]["$regex"].endswith("LATRA$")


def test_category_report_query_column_filters():
    q = _category_report_query(
        "LATRA",
        date_from=datetime(2026, 1, 1),
        date_to=datetime(2026, 12, 31, 23, 59, 59),
        column_filters={"truck_number": ["T 123 ABC"], "receipt_status": ["Received"]},
    )
    blob = str(q)
    assert "2026, 1, 1" in blob
    assert "T" in blob and "123" in blob
    assert "received" in blob.lower()


def test_category_report_query_date_range():
    q = _category_report_query(
        "Council",
        date_from=datetime(2025, 6, 1),
        date_to=datetime(2025, 6, 30, 23, 59, 59),
    )
    blob = str(q)
    assert "2025, 6, 1" in blob
    assert "2025, 6, 30" in blob
