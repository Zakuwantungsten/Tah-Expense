"""Cashier-facing daily Excel import orchestration (dialogs → staged preview)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from tahmeed.services.daily_import_service import (
    DailyImportCancelled,
    DailyImportPreview,
    find_prior_daily_imports,
    preview_daily_import,
    skip_all_unmapped,
)
from tahmeed.ui.dialogs.daily_import_preview_dialog import DailyImportPreviewDialog
from tahmeed.ui.dialogs.daily_reimport_dialog import DailyReimportDialog
from tahmeed.ui.dialogs.date_outlier_dialog import resolve_import_date_policy
from tahmeed.ui.dialogs.import_problem_rows_dialog import prompt_import_problems
from tahmeed.ui.widgets.upload_busy import UploadBusy, UploadCancelled


async def run_daily_import_flow(parent: QWidget) -> Optional[DailyImportPreview]:
    """Pick a file, resolve mappings + dates, confirm preview (or None)."""
    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Import daily Excel transactions",
        "",
        "Excel files (*.xlsx *.xls);;All files (*)",
    )
    if not path:
        return None

    try:
        with UploadBusy(
            parent, "Reading Excel file…", title="Import", cancellable=True
        ) as busy:
            await busy.tick()
            busy.update("Reading Excel file…")
            preview = await busy.await_or_cancel(
                preview_daily_import(path, should_cancel=busy.should_cancel)
            )
            busy.update(
                f"Matched descriptions · {len(preview.rows):,} row(s) found…"
                + (
                    f" · {len(preview.problem_rows):,} need dates"
                    if preview.problem_rows
                    else ""
                )
            )
    except (UploadCancelled, DailyImportCancelled):
        return None
    except Exception as exc:
        QMessageBox.critical(
            parent,
            "Import Rejected",
            f"Could not import this file:\n\n{exc}",
        )
        return None

    # ── Missing / invalid dates (flag to fix or explicit skip) ────────────
    if not await prompt_import_problems(preview, parent):
        return None

    if not preview.rows:
        QMessageBox.information(
            parent,
            "Import",
            "No transaction rows were found in that file.\n\n"
            "Check that it is a Daily Register / MATUMIZI Excel with "
            "Date and Description columns.",
        )
        return None

    # ── Unmapped descriptions → Day Issues (no one-by-one dialog) ─────────
    # Import rows without forcing a per-description dialog. Day Issues tab
    # (+ sidebar badge) is where cashiers bulk-map Payee + Item afterward.
    unmapped_n = len(preview.unmapped) if preview.unmapped else 0
    if unmapped_n:
        skip_all_unmapped(preview)
        QMessageBox.information(
            parent,
            "Day Issues",
            f"{unmapped_n:,} description(s) still need Payee + Item mapping.\n\n"
            "They will be imported without a mapping. Open Day Issues to map "
            "them in bulk before Submit.",
        )

    # ── One reconciled date for the whole upload ──────────────────────────
    if not resolve_import_date_policy(preview, parent=parent):
        return None

    # ── Reimport gate — same filename / exact content already uploaded ───
    try:
        with UploadBusy(
            parent, "Checking prior imports…", title="Import", cancellable=True
        ) as busy:
            await busy.tick()
            prior = await busy.await_or_cancel(
                find_prior_daily_imports(preview)
            )
    except (UploadCancelled, DailyImportCancelled):
        return None
    except Exception:
        prior = []

    if prior:
        reimport = DailyReimportDialog(
            prior,
            source_filename=preview.source_filename,
            row_count=len(preview.rows),
            parent=parent,
        )
        if reimport.exec() != DailyReimportDialog.Accepted:
            return None

    # ── Confirm preview before staging into the table ─────────────────────
    confirm = DailyImportPreviewDialog(preview, parent=parent)
    if confirm.exec() != DailyImportPreviewDialog.Accepted:
        return None

    return preview
