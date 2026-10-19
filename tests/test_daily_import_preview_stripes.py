"""Import preview uses Master Expenses zebra (white / slate-100)."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtWidgets import QApplication, QTableWidget

from tahmeed.services.daily_import_service import DailyImportPreview, DailyImportRow
from tahmeed.ui.dialogs.daily_import_preview_dialog import (
    DailyImportPreviewDialog,
    _ROW_EVEN,
    _ROW_ODD,
    _stripe_bg,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_stripe_bg_matches_master_expenses():
    assert _stripe_bg(0) == _ROW_EVEN == "#FFFFFF"
    assert _stripe_bg(1) == _ROW_ODD == "#F1F5F9"
    assert _stripe_bg(2) == "#FFFFFF"


def test_preview_dialog_applies_zebra_to_cells():
    _app()
    rows = [
        DailyImportRow(
            serial=i,
            date=datetime(2026, 8, 20),
            description=f"ROW {i}",
            truck_number="YARD",
            lpo_do="",
            do_number="",
            memo="",
            notes="",
            amount=1000.0 + i,
            currency="TZS",
            receipt_status="pending",
            ownership="",
            approver="",
        )
        for i in range(3)
    ]
    preview = DailyImportPreview(
        source_filename="day.xlsx",
        source_path="day.xlsx",
        rows=rows,
        primary_date=date(2026, 8, 20),
    )
    dlg = DailyImportPreviewDialog(preview)
    table = dlg.findChild(QTableWidget)
    assert table is not None
    assert table.rowCount() == 3
    assert table.item(0, 1).background().color().name().lower() == "#ffffff"
    assert table.item(1, 1).background().color().name().lower() == "#f1f5f9"
    assert table.item(2, 1).background().color().name().lower() == "#ffffff"
