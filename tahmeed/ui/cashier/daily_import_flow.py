"""Cashier-facing daily Excel import orchestration (dialogs → staged preview)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from tahmeed.services.category_service import get_all_categories
from tahmeed.services.daily_import_service import (
    DailyImportPreview,
    apply_date_policy,
    apply_mapping_to_preview,
    preview_daily_import,
    skip_all_unmapped,
    skip_description_in_preview,
)
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.ui.dialogs.daily_import_preview_dialog import DailyImportPreviewDialog
from tahmeed.ui.dialogs.date_outlier_dialog import (
    FORCE_PRIMARY,
    KEEP_AND_FLAG,
    KEEP_AS_IS,
    DateOutlierDialog,
)
from tahmeed.ui.dialogs.description_mapping_dialog import (
    ACTION_ASSIGN,
    ACTION_SKIP,
    ACTION_SKIP_ALL,
    DescriptionMappingDialog,
)
from tahmeed.ui.widgets.upload_busy import UploadBusy


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
        with UploadBusy(parent, "Reading Excel file…", title="Import") as busy:
            busy.update("Reading Excel file…")
            preview = await preview_daily_import(path)
            busy.update(
                f"Matched descriptions · {len(preview.rows):,} row(s) found…"
            )
    except Exception as exc:
        QMessageBox.critical(
            parent,
            "Import Rejected",
            f"Could not import this file:\n\n{exc}",
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
        try:
            categories = await get_all_categories()
        except Exception as exc:
            QMessageBox.critical(
                parent, "Import Error", f"Could not load items:\n{exc}"
            )
            return None

        total_unmapped = len(preview.unmapped)
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
                categories,
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

    # ── Mixed dates ───────────────────────────────────────────────────────
    if preview.outlier_count > 0 and preview.primary_date is not None:
        dlg = DateOutlierDialog(
            preview.primary_date,
            preview.outlier_count,
            preview.detected_dates,
            len(preview.rows),
            parent=parent,
        )
        if dlg.exec() != DateOutlierDialog.Accepted:
            return None
        choice = dlg.choice()
        if choice == FORCE_PRIMARY:
            apply_date_policy(preview, force_primary=True, flag_discrepancy=False)
        elif choice == KEEP_AND_FLAG:
            apply_date_policy(preview, force_primary=False, flag_discrepancy=True)
        else:  # KEEP_AS_IS
            apply_date_policy(preview, force_primary=False, flag_discrepancy=False)
    else:
        apply_date_policy(preview, force_primary=False, flag_discrepancy=False)

    # ── Confirm preview before staging into the table ─────────────────────
    confirm = DailyImportPreviewDialog(preview, parent=parent)
    if confirm.exec() != DailyImportPreviewDialog.Accepted:
        return None

    return preview
