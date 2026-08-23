"""Separate Expenses export helpers: row mapping, paging, xlsx write."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
openpyxl = pytest.importorskip("openpyxl")

from tahmeed.ui.accountant.separate_expenses import (
    _comesa_export_row,
    _fetch_export_pages,
    _toll_export_row,
    _write_feed_xlsx,
)
from tahmeed.ui.widgets.export_paging import fetch_all_pages


def test_toll_export_row_maps_numeric_tender() -> None:
    row = _toll_export_row({
        "toll_date": "2026-01-02",
        "toll_plaza": "Kafue",
        "tender_amount": "12.50",
        "vehicle_reg": "T123",
    })
    assert row[0] == "2026-01-02"
    assert row[1] == "Kafue"
    assert row[4] == "T123"
    assert row[6] == 12.5


def test_comesa_export_row_skips_serial() -> None:
    row = _comesa_export_row({
        "name": "Asha",
        "card_no": "C1",
        "premium": "1000",
        "month": "JANUARY",
    })
    assert row[0] == "Asha"
    assert row[1] == "C1"
    assert row[5] == 1000.0
    assert row[6] == "JANUARY"


def test_fetch_export_pages_delegates_to_shared_helper() -> None:
    import asyncio

    batches = [[{"id": 1}], []]

    async def fetch(*, limit, skip):
        return batches[skip] if skip < len(batches) else []

    recs = asyncio.run(_fetch_export_pages(fetch, page=1))
    assert recs == [{"id": 1}]
    # Shared helper is the same one master/category exports use.
    recs2 = asyncio.run(fetch_all_pages(fetch, page_size=1))
    assert recs2 == [{"id": 1}]


def test_write_feed_xlsx_includes_headers_and_rows(tmp_path) -> None:
    path = tmp_path / "out.xlsx"
    _write_feed_xlsx(
        str(path),
        title="Toll Plaza",
        headers=["DATE", "PLAZA"],
        rows=[["2026-01-02", "Kafue"]],
        subtitle="Filtered",
    )
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    assert ws["A1"].value == "TAHMEED COACH TZ LTD"
    assert any(cell.value == "DATE" for row in ws.iter_rows(max_row=6) for cell in row)
    assert any(cell.value == "Kafue" for row in ws.iter_rows() for cell in row)
