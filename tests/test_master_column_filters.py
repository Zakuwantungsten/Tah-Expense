"""Master Expenses Excel ▾ filters apply across the full selected range."""

from __future__ import annotations

from datetime import datetime

from tahmeed.services.accountant_service import (
    _build_master_query,
    _parse_master_filter_amount,
    _parse_master_filter_date,
    _receipt_statuses_for_filter,
)


def test_parse_master_filter_date_day_month_label():
    assert _parse_master_filter_date("18 Jul", 2026) == datetime(2026, 7, 18)


def test_parse_master_filter_amount_strips_commas():
    assert _parse_master_filter_amount("-12,000") == -12000.0
    assert _parse_master_filter_amount("45.50") == 45.5


def test_receipt_filter_maps_display_labels():
    assert set(_receipt_statuses_for_filter(["No Receipt"])) == {"missing", "no_receipt"}
    assert _receipt_statuses_for_filter(["Received"]) == ["received"]


def test_build_master_query_column_item_filter():
    q = _build_master_query(
        2026, 7, "", "", "", "",
        column_filters={"item": ["Fuel", "Parking"]},
    )
    assert q["verified"] is True
    assert q["date"]["$gte"] == datetime(2026, 7, 1)
    assert "$and" in q or "$or" in q
    blob = str(q)
    assert "Fuel" in blob
    assert "Parking" in blob
    assert "category_name" in blob


def test_build_master_query_column_truck_and_date():
    q = _build_master_query(
        2026, 0, "", "", "", "",
        column_filters={
            "truck_number": ["T 123 ABC"],
            "date": ["18 Jul"],
        },
    )
    # Full year window narrowed by the day filter.
    assert q["date"]["$gte"] == datetime(2026, 1, 1)
    and_parts = q.get("$and") or []
    assert and_parts
    joined = str(and_parts)
    assert "T" in joined and "123" in joined and "ABC" in joined
    assert datetime(2026, 7, 18) in [
        clause.get("date", {}).get("$gte")
        for part in and_parts
        for clause in (part.get("$or") or [])
        if isinstance(clause, dict)
    ]


def test_build_master_query_tzs_amount_filter():
    q = _build_master_query(
        2026, 3, "", "", "", "",
        column_filters={"tzs": ["12,000", "-500"]},
    )
    blob = str(q)
    assert "12000" in blob or "12,000" in blob or "12000.0" in blob
    assert "TZS" in blob


def test_build_master_query_receipt_no_receipt_label():
    q = _build_master_query(
        2026, 1, "", "", "", "",
        column_filters={"receipt_status": ["No Receipt"]},
    )
    blob = str(q)
    assert "missing" in blob
    assert "no_receipt" in blob
