"""Problem-rows dialog uses Master Expenses zebra (white / slate-100)."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtWidgets import QApplication, QLineEdit, QTableWidget

from tahmeed.services.daily_import_service import (
    DailyImportPreview,
    DailyImportProblemRow,
    REASON_EMPTY_DESCRIPTION,
    REASON_MISSING_DATE,
)
from tahmeed.ui.dialogs.import_problem_rows_dialog import (
    ImportProblemRowsDialog,
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


def test_problem_rows_dialog_applies_zebra_to_cells():
    _app()
    preview = DailyImportPreview(
        source_filename="day.xlsx",
        source_path="day.xlsx",
        rows=[],
        primary_date=date(2026, 8, 20),
        problem_rows=[
            DailyImportProblemRow(
                excel_row=50 + i,
                reason=REASON_MISSING_DATE,
                description=f"ROW {i}",
                truck_number="T459 EAG",
                amount=14500.0 + i,
            )
            for i in range(3)
        ],
    )
    dlg = ImportProblemRowsDialog(preview)
    table = dlg.findChild(QTableWidget)
    assert table is not None
    assert table.alternatingRowColors() is False
    assert table.rowCount() == 3
    assert table.item(0, 1).background().color().name().lower() == "#ffffff"
    assert table.item(1, 1).background().color().name().lower() == "#f1f5f9"
    assert table.item(2, 1).background().color().name().lower() == "#ffffff"
    assert table.rowHeight(0) == 26


def test_problem_rows_dialog_edits_blank_description():
    _app()
    preview = DailyImportPreview(
        source_filename="day.xlsx",
        source_path="day.xlsx",
        rows=[],
        primary_date=date(2026, 8, 20),
        problem_rows=[
            DailyImportProblemRow(
                excel_row=3,
                reason=REASON_EMPTY_DESCRIPTION,
                description="",
                truck_number="T459 EAG",
                amount=14500.0,
                date=datetime(2026, 8, 20),
            ),
        ],
    )
    dlg = ImportProblemRowsDialog(preview)
    table = dlg.findChild(QTableWidget)
    assert table is not None
    desc = table.cellWidget(0, 2)
    assert isinstance(desc, QLineEdit)
    assert desc.text() == ""
    desc.setText("parking fees")
    dlg._on_apply()
    assert preview.problem_rows[0].description == "PARKING FEES"
    assert preview.problem_rows[0].date == datetime(2026, 8, 20)
    assert dlg.result() == ImportProblemRowsDialog.Accepted
