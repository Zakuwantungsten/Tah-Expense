"""Cashier-facing daily Excel import orchestration (dialogs → staged preview)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from tahmeed.services.category_service import get_all_categories
from tahmeed.services.daily_import_service import (
    DailyImportCancelled,
    DailyImportPreview,
    apply_mapping_to_preview,
    preview_daily_import,
    skip_all_unmapped,
    skip_description_in_preview,
)
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.ui.dialogs.daily_import_preview_dialog import DailyImportPreviewDialog
from tahmeed.ui.dialogs.date_outlier_dialog import resolve_import_date_policy
from tahmeed.ui.dialogs.description_mapping_dialog import (
    ACTION_ASSIGN,
    ACTION_SKIP,
    ACTION_SKIP_ALL,
    DescriptionMappingDialog,
)
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

    categories = None
    items_error = None
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
            )
            if preview.rows and preview.unmapped:
                busy.update("Loading items…")
                try:
                    categories = await busy.await_or_cancel(get_all_categories())
                except UploadCancelled:
                    raise
                except Exception as exc:
                    items_error = exc
    except (UploadCancelled, DailyImportCancelled):
        return None
    except Exception as exc:
        QMessageBox.critical(
            parent,
            "Import Rejected",
            f"Could not import this file:\n\n{exc}",
        )
        return None

    if items_error is not None:
        QMessageBox.critical(
            parent, "Import Error", f"Could not load items:\n{items_error}"
        )
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

    # ── Description → item mapping (remembered) / skip ────────────────────
    if preview.unmapped:
        total_unmapped = len(preview.unmapped)
        cats = categories or []
        while preview.unmapped:
            key = next(iter(preview.unmapped))
            count = preview.unmapped[key]
            display = key
            for row in preview.rows:
                if normalize_description(row.description) == key:
                    display = row.description
                    break
            remaining = len(preview.unmapped)
            dlg = DescriptionMappingDialog(
                display,
                count,
                cats,
                remaining,
                parent=parent,
                allow_skip=True,
                total=total_unmapped,
            )
            if dlg.exec() != DescriptionMappingDialog.Accepted:
                return None
            action = dlg.action()
            if action == ACTION_SKIP:
                skip_description_in_preview(preview, key)
            elif action == ACTION_SKIP_ALL:
                skip_all_unmapped(preview)
                break
            elif action == ACTION_ASSIGN:
                chosen = dlg.selected_category()
                if chosen is None:
                    return None
                await apply_mapping_to_preview(
                    preview, key, chosen._id, chosen.name
                )

    # ── One register date for the whole upload ────────────────────────────
    if not resolve_import_date_policy(preview, parent=parent):
        return None

    # ── Confirm preview before staging into the table ─────────────────────
    confirm = DailyImportPreviewDialog(preview, parent=parent)
    if confirm.exec() != DailyImportPreviewDialog.Accepted:
        return None

    return preview
