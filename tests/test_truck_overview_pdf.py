"""Smoke tests for Truck Overview landscape PDF export."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import fitz
import pytest

from tahmeed.services.truck_overview_pdf import (
    _sort_detail_rows_by_source,
    export_truck_overview_pdf,
)


def test_export_truck_overview_pdf_writes_landscape_with_all_columns(tmp_path: Path):
    path = tmp_path / "truck_overview_test.pdf"
    rows = [
        {
            "date": datetime(2026, 1, 15),
            "source": "Master Expenses",
            "description": "Parking Congo gate fee",
            "reference": "MEMO-01",
            "truck_value": "T123 ABC",
            "currency": "TZS",
            "amount": 1250000,
            "liters": None,
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
            "amount": 450000,
            "liters": 200,
            "rate": 2250.5,
            "station": "Dar depot",
            "receipt_status": "pending",
        },
        {
            "date": datetime(2026, 6, 1),
            "source": "Toll Plaza",
            "description": "Kapiri toll",
            "reference": "RCPT-9",
            "truck_value": "T123 ABC",
            "currency": "ZMW",
            "amount": 340,
            "liters": None,
            "rate": None,
            "station": "Kapiri",
            "receipt_status": "missing",
        },
        {
            "date": datetime(2026, 8, 4),
            "source": "Afritrack",
            "description": "USD ledger trip",
            "reference": "AT-12",
            "truck_value": "T123 ABC",
            "currency": "USD",
            "amount": 85.5,
            "liters": None,
            "rate": None,
            "station": "Owner",
            "receipt_status": "",
        },
    ]
    summary = {
        "record_count": 4,
        "source_count": 4,
        "tzs_total": 1700000.0,
        "usd_total": 85.5,
        "zmw_total": 340.0,
        "liters_total": 200.0,
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
    )

    assert path.is_file()
    assert path.stat().st_size > 1000

    doc = fitz.open(str(path))
    try:
        assert doc.page_count >= 1
        page = doc[0]
        assert page.rect.width == pytest.approx(841.89, abs=0.5)
        assert page.rect.height == pytest.approx(595.28, abs=0.5)

        text = "\n".join(p.get_text("text") for p in doc).replace("\xa0", " ")
        upper = text.upper()
        assert "TAHMEED TRANSPORTERS" in text
        assert "FLEET EXPENSE REPORT" in upper
        assert "TRUCK OVERVIEW" in upper
        assert "CONFIDENTIAL" in text
        assert "TRANSACTION DETAIL" in upper
        for header in (
            "DATE",
            "SOURCE",
            "DESCRIPTION",
            "REFERENCE",
            "TRUCK",
            "TZS",
            "USD",
            "ZMW",
            "LTRS",
            "RATE",
            "STATION",
            "RECEIPT",
        ):
            assert header in upper
        assert "Parking Congo" in text or "PARKING CONGO" in upper
        assert "1,250,000" in text or "1250000" in text
        assert "85.50" in text or "85.5" in text
        assert "T123" in text
    finally:
        doc.close()


def test_pdf_detail_rows_group_by_source_then_date():
    rows = [
        {"source": "Toll Plaza", "date": datetime(2026, 6, 1), "description": "toll-late"},
        {"source": "Congo Expenses", "date": datetime(2026, 3, 10), "description": "congo-late"},
        {"source": "Toll Plaza", "date": datetime(2026, 1, 2), "description": "toll-early"},
        {"source": "Congo Expenses", "date": datetime(2026, 2, 1), "description": "congo-early"},
    ]
    grouped = _sort_detail_rows_by_source(rows)
    assert [r["description"] for r in grouped] == [
        "toll-early",
        "toll-late",
        "congo-early",
        "congo-late",
    ]
    # Original list stays date-mixed so the dashboard sort is unchanged.
    assert [r["description"] for r in rows] == [
        "toll-late",
        "congo-late",
        "toll-early",
        "congo-early",
    ]
