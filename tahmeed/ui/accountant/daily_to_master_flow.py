"""Accountant daily Excel → Master Expenses (bypass Verify)."""

from __future__ import annotations

from typing import Optional

from bson import ObjectId
from PySide6.QtWidgets import QFileDialog, QWidget

from tahmeed.services.category_service import get_all_categories
from tahmeed.services.daily_import_service import (
    apply_mapping_to_preview,
    commit_daily_to_master,
    preview_daily_import,
    preview_rows_as_truck_dicts,
)
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.ui.accountant.import_truck_gate import run_import_truck_gate
from tahmeed.ui.dialog_theme import show_critical, show_info, show_warning
from tahmeed.ui.dialogs.daily_import_preview_dialog import DailyImportPreviewDialog
from tahmeed.ui.dialogs.date_outlier_dialog import resolve_import_date_policy
from tahmeed.ui.dialogs.description_mapping_dialog import (
    ACTION_ASSIGN,
    DescriptionMappingDialog,
)
from tahmeed.ui.widgets.upload_busy import UploadBusy


async def run_daily_to_master_flow(
    parent: QWidget,
    *,
    verified_by: Optional[ObjectId] = None,
) -> Optional[dict]:
    """Map → date policy → confirm → truck gate → insert verified. No dedupe.

    Returns the commit result dict, or None if cancelled / aborted.
    """
    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Import daily Excel → Master Expenses",
        "",
        "Excel files (*.xlsx *.xls);;All files (*)",
    )
    if not path:
        return None

    try:
        with UploadBusy(parent, "Reading Excel file…", title="Import Daily") as busy:
            busy.update("Reading Excel file…")
            preview = await preview_daily_import(path)
            busy.update(
                f"Matched descriptions · {len(preview.rows):,} row(s) found…"
            )
    except Exception as exc:
        show_critical(
            parent,
            "Import Rejected",
            f"Could not import this file:\n\n{exc}",
        )
        return None

    if not preview.rows:
        show_info(
            parent,
            "Import",
            "No transaction rows were found in that file.\n\n"
            "Check that it is a Daily Register / MATUMIZI Excel with "
            "Date and Description columns.",
        )
        return None

    # ── Description → item (required — no skip; going straight to Master) ─
    if preview.unmapped:
        try:
            categories = await get_all_categories()
        except Exception as exc:
            show_critical(
                parent, "Import Error", f"Could not load items:\n{exc}"
            )
            return None
        if not categories:
            show_warning(
                parent,
                "No Items",
                "Import your Chart of Accounts into Items first\n"
                "(Manage → Items → Import Chart of Accounts).",
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
                allow_skip=False,
                total=total_unmapped,
            )
            if dlg.exec() != DescriptionMappingDialog.Accepted:
                return None
            if dlg.action() != ACTION_ASSIGN:
                return None
            chosen = dlg.selected_category()
            if chosen is None:
                return None
            await apply_mapping_to_preview(
                preview, key, chosen._id, chosen.name
            )

    # ── One register date for the whole upload ────────────────────────────
    if not resolve_import_date_policy(preview, parent=parent):
        return None

    # ── Confirm before truck check + commit ───────────────────────────────
    confirm = DailyImportPreviewDialog(
        preview,
        parent=parent,
        title="Review import before Master Expenses",
        note=(
            "Confirm to check vehicle numbers, then push these rows straight "
            "into Master Expenses (verified). They will not go through Verify."
        ),
        confirm_label=f"Continue with {len(preview.rows):,} rows",
    )
    if confirm.exec() != DailyImportPreviewDialog.Accepted:
        return None

    payloads = preview_rows_as_truck_dicts(preview)
    gate = await run_import_truck_gate(
        parent,
        payloads,
        feed_key="daily_register",
        upload_id=preview.upload_id,
        truck_field="truck_number",
        source_filename=preview.source_filename,
        sheet_label=preview.sheet_name or "",
        can_add=True,
    )
    if gate.aborted:
        return None
    if not gate.rows:
        show_info(
            parent,
            "Import",
            "No rows left to import after vehicle corrections.\n"
            + (
                f"{gate.skipped_count:,} row(s) were parked in Skipped."
                if gate.skipped_count
                else ""
            ),
        )
        return None

    try:
        with UploadBusy(parent, "Saving to Master Expenses…", title="Import Daily") as busy:
            busy.update(f"Inserting {len(gate.rows):,} verified row(s)…")
            result = await commit_daily_to_master(
                gate.rows, verified_by=verified_by
            )
    except Exception as exc:
        show_critical(
            parent,
            "Import Error",
            f"Could not save to Master Expenses:\n\n{exc}",
        )
        return None

    skipped_note = ""
    if gate.skipped_count:
        skipped_note = f"\n{gate.skipped_count:,} row(s) parked for vehicle follow-up."
    if result.get("skipped_no_item"):
        skipped_note += (
            f"\n{result['skipped_no_item']:,} row(s) skipped (missing item)."
        )
    if result.get("duplicates_skipped"):
        skipped_note += (
            f"\n{result['duplicates_skipped']:,} duplicate row(s) skipped."
        )

    date_note = ""
    min_d = result.get("min_date")
    max_d = result.get("max_date")
    if min_d is not None and max_d is not None:
        def _fmt(dt):
            d = dt.date() if hasattr(dt, "date") else dt
            return d.strftime("%d/%m/%Y")
        if min_d == max_d:
            date_note = f"\nTransaction date: {_fmt(min_d)}."
        else:
            date_note = f"\nTransaction dates: {_fmt(min_d)} – {_fmt(max_d)}."
        years = set()
        for dt in (min_d, max_d):
            years.add(dt.year if hasattr(dt, "year") else dt)
        if years:
            yrs = ", ".join(str(y) for y in sorted(years))
            date_note += (
                f"\nOpen Master Expenses for year {yrs} (All Months) to see every row."
                "\nTable → Uploads → Open works the same batch in Advanced / register."
            )

    show_info(
        parent,
        "Import Complete",
        f"Imported {result['inserted']:,} row(s) into Master Expenses "
        f"and Daily Transactions\nfrom {result.get('source') or preview.source_filename}."
        f"{date_note}{skipped_note}",
    )
    return result
