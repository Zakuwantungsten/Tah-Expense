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
from tahmeed.ui.async_utils import pause_background_polls
from tahmeed.ui.dialogs.daily_import_preview_dialog import DailyImportPreviewDialog
from tahmeed.ui.dialogs.daily_reimport_dialog import DailyReimportDialog
from tahmeed.ui.dialogs.date_outlier_dialog import resolve_import_date_policy
from tahmeed.ui.dialogs.import_problem_rows_dialog import prompt_import_problems
from tahmeed.ui.widgets.export_runner import await_ui
from tahmeed.ui.widgets.upload_busy import UploadBusy, UploadCancelled

_IMPORT_TITLE = "Importing Daily Register"


async def run_daily_import_flow(parent: QWidget) -> Optional[DailyImportPreview]:
    """Pick a file, resolve mappings + dates, confirm preview (or None)."""
    with pause_background_polls(parent):
        path, _ = await await_ui(
            lambda: QFileDialog.getOpenFileName(
                parent,
                "Import daily Excel transactions",
                "",
                "Excel files (*.xlsx *.xls);;All files (*)",
            )
        )
    if not path:
        return None

    try:
        with UploadBusy(
            parent,
            "Opening Excel file…",
            title=_IMPORT_TITLE,
            cancellable=True,
        ) as busy:
            await busy.tick()
            busy.update("Reading Excel file…", value=10, maximum=100)
            preview = await busy.await_or_cancel(
                preview_daily_import(path, should_cancel=busy.should_cancel)
            )
            busy.update(
                "Matching descriptions…",
                value=70,
                maximum=100,
            )
            await busy.tick()
            detail = f"{len(preview.rows):,} row(s) ready"
            if preview.problem_rows:
                detail += f" · {len(preview.problem_rows):,} need fixes"
            busy.update(detail, value=100, maximum=100)
            await busy.tick()
    except (UploadCancelled, DailyImportCancelled):
        return None
    except Exception as exc:
        with pause_background_polls(parent):
            await await_ui(
                lambda: QMessageBox.critical(
                    parent,
                    "Import Rejected",
                    f"Could not import this file:\n\n{exc}",
                )
            )
        return None

    # ── Missing date / blank description (flag to fix or explicit skip) ───
    if not await prompt_import_problems(preview, parent):
        return None

    if not preview.rows:
        with pause_background_polls(parent):
            await await_ui(
                lambda: QMessageBox.information(
                    parent,
                    "Import",
                    "No transaction rows were found in that file.\n\n"
                    "Check that it is a Daily Register / MATUMIZI Excel with "
                    "Date and Description columns.",
                )
            )
        return None

    # ── Unmapped descriptions → Day Issues (no one-by-one dialog) ─────────
    # Import rows without forcing a per-description dialog. Day Issues tab
    # (+ sidebar badge) is where cashiers bulk-map Payee + Item afterward.
    unmapped_n = len(preview.unmapped) if preview.unmapped else 0
    if unmapped_n:
        skip_all_unmapped(preview)
        with pause_background_polls(parent):
            await await_ui(
                lambda: QMessageBox.information(
                    parent,
                    "Day Issues",
                    f"{unmapped_n:,} description(s) still need Payee + Item mapping.\n\n"
                    "They will be imported without a mapping. Open Day Issues to map "
                    "them in bulk before Submit.",
                )
            )

    # ── One reconciled date for the whole upload ──────────────────────────
    with pause_background_polls(parent):
        if not await await_ui(
            lambda: resolve_import_date_policy(preview, parent=parent)
        ):
            return None

    # ── Reimport gate — same filename / exact content already uploaded ───
    try:
        with UploadBusy(
            parent,
            "Checking prior imports…",
            title=_IMPORT_TITLE,
            cancellable=True,
        ) as busy:
            await busy.tick()
            busy.update("Checking prior imports…", value=35, maximum=100)
            prior = await busy.await_or_cancel(
                find_prior_daily_imports(preview)
            )
            busy.update("Prior imports checked", value=100, maximum=100)
            await busy.tick()
    except (UploadCancelled, DailyImportCancelled):
        return None
    except Exception:
        prior = []

    if prior:
        def _reimport() -> bool:
            reimport = DailyReimportDialog(
                prior,
                source_filename=preview.source_filename,
                row_count=len(preview.rows),
                parent=parent,
            )
            return reimport.exec() == DailyReimportDialog.Accepted

        with pause_background_polls(parent):
            if not await await_ui(_reimport):
                return None

    # ── Confirm preview before staging into the table ─────────────────────
    def _confirm() -> bool:
        confirm = DailyImportPreviewDialog(preview, parent=parent)
        return confirm.exec() == DailyImportPreviewDialog.Accepted

    with pause_background_polls(parent):
        if not await await_ui(_confirm):
            return None

    return preview
