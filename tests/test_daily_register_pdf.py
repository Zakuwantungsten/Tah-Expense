"""Smoke tests for cashier Daily Register PDF export."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import fitz
import pytest

from tahmeed.services.daily_register_pdf import (
    EXPORT_HEADERS,
    export_daily_register_pdf,
)


def test_export_daily_register_pdf_writes_landscape_with_all_columns(tmp_path: Path):
    path = tmp_path / "register_test.pdf"
    rows = [
        [
            "23/07/2026",
            "FUEL",
            "Diesel top-up Dar",
            "T123 ABC",
            "Trip 9",
            "REFUND TO FLOAT",
            "1,250,000.00",
            "40.00",
            "Yes",
            "Company",
            "JM",
            "Cashier A",
            "CHQ-01",
        ],
        [
            "23/07/2026",
            "TOLLS",
            "Morogoro gate",
            "T456 DEF",
            "",
            "",
            "45,000",
            "",
            "No",
            "Owner",
            "AK",
            "",
            "",
        ],
    ]

    export_daily_register_pdf(
        str(path),
        rows=rows,
        register_date=date(2026, 7, 23),
        generated_at=datetime(2026, 7, 23, 12, 0, 0),
    )

    assert path.is_file()
    assert path.stat().st_size > 1000

    doc = fitz.open(str(path))
    try:
        assert doc.page_count >= 1
        page = doc[0]
        assert page.rect.width == pytest.approx(841.89, abs=0.5)
        assert page.rect.height == pytest.approx(595.28, abs=0.5)

        text = page.get_text("text").replace("\xa0", " ")
        upper = text.upper()
        assert "TAHMEED TRANSPORTERS" in text
        assert "DAILY REGISTER" in upper
        assert "CONFIDENTIAL" in text
        assert "S/N" in upper or "S/N" in text
        assert "SCOPE" not in upper
        assert "VISIBLE ROWS" not in upper
        assert "LINE ITEMS LISTED" not in upper
        assert "landscape layout" not in text.lower()
        assert "REFUND TOTAL" in upper
        assert "USD TOTAL" in upper
        assert "TRANSACTION DETAIL" in upper
        assert "23 JUL 2026" in upper
        for header in (
            "DATE",
            "ITEM",
            "DESCRIPTION",
            "TRUCK",
            "TZS",
            "USD",
            "RECEIPT",
            "PAYEE",
            "CHEQUE",
        ):
            assert header in upper
        assert "1,250,000" in text or "1250000" in text
        assert "Diesel" in text or "DIESEL" in upper or "top-up" in text.lower()
    finally:
        doc.close()


def test_export_headers_match_register_export_columns():
    assert len(EXPORT_HEADERS) == 13
    assert EXPORT_HEADERS[0] == "Date"
    assert EXPORT_HEADERS[6] == "TZS"
    assert EXPORT_HEADERS[7] == "USD"
    assert EXPORT_HEADERS[-1] == "Cheque"
