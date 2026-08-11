"""UI orchestration for fleet-checking accountant imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from tahmeed.services.import_truck_check import (
    apply_truck_resolutions,
    scan_import_trucks,
    skipped_docs_from_pairs,
    truck_field_for,
)
from tahmeed.services.truck_format import DEFAULT_PLACE_LABELS, merge_allowed_labels
from tahmeed.ui.async_utils import safe_process_events
from tahmeed.ui.dialogs.truck_correction_dialog import TruckCorrectionDialog


@dataclass
class ImportTruckGateResult:
    """Rows ready to save after fleet gate; None means user aborted."""

    rows: List[dict]
    skipped_count: int = 0
    aborted: bool = False


async def run_import_truck_gate(
    parent: QWidget,
    rows: List[dict],
    *,
    feed_key: str,
    upload_id: str,
    truck_field: Optional[str] = None,
    source_filename: str = "",
    sheet_label: str = "",
    can_add: bool = True,
) -> ImportTruckGateResult:
    """Progress scan → correction dialog → persist skips → return saveable rows.

    If there is no truck field for ``feed_key``, returns rows unchanged.
    """
    field = truck_field or truck_field_for(feed_key)
    if not field or not rows:
        return ImportTruckGateResult(rows=list(rows))

    from tahmeed.services.truck_service import get_fleet_numbers, add_fleet_by_collection
    from tahmeed.services import accountant_service as svc
    from tahmeed.services import settings_service

    # Load fleet BEFORE any modal/progress UI. Showing a WindowModal dialog and
    # pumping Qt events while this coroutine is current breaks Python 3.14 +
    # qasync (nested task enter), which used to collapse fleet to ``set()`` and
    # falsely flag every truck as not-in-registry.
    try:
        fleet = await get_fleet_numbers()
    except Exception as exc:
        QMessageBox.critical(
            parent,
            "Fleet registry",
            "Could not load the truck/trailer registry to check this import.\n\n"
            f"{exc}\n\n"
            "If this says a fleet route is missing (404), deploy the latest API "
            "that includes /v1/trucks. Otherwise check the connection and try again.\n"
            "Import was not saved.",
        )
        return ImportTruckGateResult(rows=[], skipped_count=0, aborted=True)

    try:
        from tahmeed.services.truck_service import get_fleet_kinds
        fleet_kinds = await get_fleet_kinds()
    except Exception:
        fleet_kinds = {}

    try:
        stored = await settings_service.get_setting("allowed_truck_labels")
    except Exception:
        stored = []
    labels = merge_allowed_labels(DEFAULT_PLACE_LABELS, stored or [])

    progress = QProgressDialog(
        "Checking truck numbers against fleet…", None, 0, 0, parent
    )
    progress.setWindowTitle("Checking trucks")
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.setCancelButton(None)
    progress.setMaximum(max(1, len(rows)))
    progress.setValue(0)
    progress.show()
    safe_process_events()

    def _on_progress(done: int, total: int) -> None:
        progress.setMaximum(max(1, total))
        progress.setValue(done)
        progress.setLabelText(
            f"Checking trucks… {done:,} / {total:,}"
        )
        safe_process_events()

    scan = scan_import_trucks(
        rows, field, fleet, allowed_labels=labels, progress=_on_progress
    )
    progress.setValue(progress.maximum())
    progress.close()
    safe_process_events()

    if not scan.issues:
        return ImportTruckGateResult(rows=list(rows))

    dlg = TruckCorrectionDialog(
        scan.issues,
        fleet,
        can_add=can_add,
        allowed_labels=labels,
        import_mode=True,
        fleet_kinds=fleet_kinds,
        parent=parent,
    )
    result_code = dlg.exec()
    # import_mode cancel also accepts after skipping remaining
    if result_code not in (TruckCorrectionDialog.Accepted, 1) and not dlg.issues:
        # Unexpected reject with nothing resolved — abort save
        return ImportTruckGateResult(rows=[], skipped_count=0, aborted=True)

    # Persist any registry adds queued by the dialog
    for kind, number in dlg.pending_registry_adds:
        try:
            await add_fleet_by_collection(kind, number)
        except Exception as exc:
            QMessageBox.warning(
                parent,
                "Registry",
                f"Could not add {number} to registry:\n{exc}",
            )

    if dlg.new_labels:
        try:
            merged = merge_allowed_labels(labels, dlg.new_labels)
            await settings_service.set_setting(
                "allowed_truck_labels", sorted(merged)
            )
        except Exception:
            pass

    to_save, skipped_pairs = apply_truck_resolutions(rows, field, dlg.issues)
    skipped_count = 0
    if skipped_pairs:
        docs = skipped_docs_from_pairs(
            skipped_pairs,
            feed_key=feed_key,
            truck_field=field,
            target_upload_id=upload_id,
            source_filename=source_filename,
            sheet_label=sheet_label,
        )
        try:
            skipped_count = await svc.save_skipped_import_rows(docs)
        except Exception as exc:
            QMessageBox.warning(
                parent,
                "Skipped rows",
                f"Could not park skipped rows for follow-up:\n{exc}",
            )
            skipped_count = len(skipped_pairs)

    return ImportTruckGateResult(rows=to_save, skipped_count=skipped_count)
