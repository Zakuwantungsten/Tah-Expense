"""UI orchestration for fleet-checking accountant imports."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtWidgets import QWidget

from tahmeed.services.import_truck_check import (
    apply_truck_resolutions,
    scan_import_trucks_yielding,
    skipped_docs_from_pairs,
    truck_field_for,
)
from tahmeed.services.truck_format import DEFAULT_PLACE_LABELS, merge_allowed_labels
from tahmeed.ui.async_utils import pause_background_polls, safe_process_events
from tahmeed.ui.dialog_theme import show_critical, show_warning
from tahmeed.ui.dialogs.truck_correction_dialog import TruckCorrectionDialog
from tahmeed.ui.widgets.upload_busy import UploadBusy


@dataclass
class ImportTruckGateResult:
    """Rows ready to save after fleet gate; None means user aborted."""

    rows: List[dict]
    skipped_count: int = 0
    aborted: bool = False


@dataclass
class BulkImportTruckGateResult:
    """Per-batch rows after one combined fleet gate across many sheets."""

    batches: List[dict] = field(default_factory=list)
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
    with pause_background_polls(parent):
        return await _run_import_truck_gate_inner(
            parent,
            rows,
            feed_key=feed_key,
            upload_id=upload_id,
            truck_field=truck_field,
            source_filename=source_filename,
            sheet_label=sheet_label,
            can_add=can_add,
        )


async def _run_import_truck_gate_inner(
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
        show_critical(
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

    with UploadBusy(
        parent,
        "Checking truck numbers against fleet…",
        title="Checking trucks",
    ) as busy:
        busy.update(
            "Checking truck numbers against fleet…",
            value=0,
            maximum=max(1, len(rows)),
        )

        async def _on_progress(done: int, total: int) -> None:
            busy.update(
                f"Checked {done:,} of {total:,}",
                value=done,
                maximum=max(1, total),
            )
            await busy.tick()

        scan = await scan_import_trucks_yielding(
            rows,
            field,
            fleet,
            allowed_labels=labels,
            progress=_on_progress,
        )
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

    with UploadBusy(
        parent,
        "Applying truck corrections…",
        title="Preparing import",
    ) as busy:
        busy.update("Applying truck corrections…", value=0, maximum=3)
        await busy.tick()

        # Persist any registry adds queued by the dialog
        adds = list(dlg.pending_registry_adds)
        for i, (kind, number) in enumerate(adds):
            busy.update(
                f"Updating fleet registry… {i + 1:,} of {len(adds):,}",
                value=0,
                maximum=3,
            )
            await busy.tick()
            try:
                await add_fleet_by_collection(kind, number)
            except Exception as exc:
                show_warning(
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

        busy.update(
            f"Applying corrections to {len(rows):,} rows…",
            value=1,
            maximum=3,
        )
        await busy.tick()
        to_save, skipped_pairs = apply_truck_resolutions(rows, field, dlg.issues)

        skipped_count = 0
        if skipped_pairs:
            busy.update(
                f"Parking {len(skipped_pairs):,} skipped row(s)…",
                value=2,
                maximum=3,
            )
            await busy.tick()
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
                show_warning(
                    parent,
                    "Skipped rows",
                    f"Could not park skipped rows for follow-up:\n{exc}",
                )
                skipped_count = len(skipped_pairs)

        busy.update("Corrections applied", value=3, maximum=3)
        await busy.tick()

    return ImportTruckGateResult(rows=to_save, skipped_count=skipped_count)


async def run_bulk_import_truck_gate(
    parent: QWidget,
    batches: List[dict],
    *,
    feed_key: str,
    source_filename: str = "",
    can_add: bool = True,
) -> BulkImportTruckGateResult:
    """One scan + one correction dialog for every selected sheet.

    Each ``batch`` must include ``docs`` (row dicts already stamped with
    ``upload_id`` / ``sheet_label``), ``upload_id``, and ``sheet_label``.
    Returns the same batch list with ``docs`` replaced by gated rows.
    """
    with pause_background_polls(parent):
        return await _run_bulk_import_truck_gate_inner(
            parent,
            batches,
            feed_key=feed_key,
            source_filename=source_filename,
            can_add=can_add,
        )


async def _run_bulk_import_truck_gate_inner(
    parent: QWidget,
    batches: List[dict],
    *,
    feed_key: str,
    source_filename: str = "",
    can_add: bool = True,
) -> BulkImportTruckGateResult:
    field = truck_field_for(feed_key)
    if not field or not batches:
        return BulkImportTruckGateResult(batches=list(batches))

    prepared: List[dict] = []
    flat: List[dict] = []
    ranges: List[tuple[int, int, dict]] = []
    for batch in batches:
        docs = [dict(row) for row in (batch.get("docs") or [])]
        prepared_batch = {
            **batch,
            "docs": docs,
            "upload_id": str(batch.get("upload_id") or ""),
            "sheet_label": str(batch.get("sheet_label") or ""),
        }
        start = len(flat)
        flat.extend(docs)
        ranges.append((start, len(flat), prepared_batch))
        prepared.append(prepared_batch)

    if not flat:
        return BulkImportTruckGateResult(batches=prepared)

    from tahmeed.services.truck_service import get_fleet_numbers, add_fleet_by_collection
    from tahmeed.services import accountant_service as svc
    from tahmeed.services import settings_service

    try:
        fleet = await get_fleet_numbers()
    except Exception as exc:
        show_critical(
            parent,
            "Fleet registry",
            "Could not load the truck/trailer registry to check this import.\n\n"
            f"{exc}\n\n"
            "If this says a fleet route is missing (404), deploy the latest API "
            "that includes /v1/trucks. Otherwise check the connection and try again.\n"
            "Import was not saved.",
        )
        return BulkImportTruckGateResult(batches=[], skipped_count=0, aborted=True)

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

    with UploadBusy(
        parent,
        "Checking truck numbers against fleet…",
        title="Checking trucks",
    ) as busy:
        busy.update(
            "Checking truck numbers across all sheets…",
            value=0,
            maximum=max(1, len(flat)),
        )

        async def _on_progress(done: int, total: int) -> None:
            busy.update(
                f"Checked {done:,} of {total:,}",
                value=done,
                maximum=max(1, total),
            )
            await busy.tick()

        scan = await scan_import_trucks_yielding(
            flat,
            field,
            fleet,
            allowed_labels=labels,
            progress=_on_progress,
        )
    safe_process_events()

    if not scan.issues:
        return BulkImportTruckGateResult(batches=prepared)

    # Label each issue with its sheet so the single dialog stays navigable.
    for iss in scan.issues:
        for start, end, batch in ranges:
            if start <= iss.row < end:
                local = iss.row - start + 1
                sheet = batch.get("sheet_label") or "Sheet"
                iss.row_label = f'{sheet} · File row {local}'
                break

    sheet_count = len({b.get("sheet_label") for b in prepared if b.get("docs")})
    dlg = TruckCorrectionDialog(
        scan.issues,
        fleet,
        can_add=can_add,
        allowed_labels=labels,
        import_mode=True,
        fleet_kinds=fleet_kinds,
        heading="Correct truck numbers",
        intro=(
            f"{len(scan.issues):,} truck issue(s) across {sheet_count:,} sheet(s). "
            "Fix them all here before import continues."
        ),
        parent=parent,
    )
    result_code = dlg.exec()
    if result_code not in (TruckCorrectionDialog.Accepted, 1) and not dlg.issues:
        return BulkImportTruckGateResult(batches=[], skipped_count=0, aborted=True)

    sheet_total = max(1, len(ranges))
    with UploadBusy(
        parent,
        "Applying truck corrections…",
        title="Preparing import",
    ) as busy:
        busy.update("Applying truck corrections…", value=0, maximum=sheet_total + 1)
        await busy.tick()

        adds = list(dlg.pending_registry_adds)
        for i, (kind, number) in enumerate(adds):
            busy.update(
                f"Updating fleet registry… {i + 1:,} of {len(adds):,}",
                value=0,
                maximum=sheet_total + 1,
            )
            await busy.tick()
            try:
                await add_fleet_by_collection(kind, number)
            except Exception as exc:
                show_warning(
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

        out_batches: List[dict] = []
        total_skipped = 0
        for bi, (start, end, batch) in enumerate(ranges):
            label = str(batch.get("sheet_label") or f"Sheet {bi + 1}")
            busy.update(
                f"Applying corrections · {label} ({bi + 1:,}/{len(ranges):,})",
                value=bi + 1,
                maximum=sheet_total + 1,
            )
            await busy.tick()
            slice_rows = flat[start:end]
            local_issues = []
            for iss in dlg.issues:
                if start <= iss.row < end:
                    local = copy(iss)
                    local.row = iss.row - start
                    local_issues.append(local)
            to_save, skipped_pairs = apply_truck_resolutions(
                slice_rows, field, local_issues
            )
            if skipped_pairs:
                busy.update(
                    f"Parking {len(skipped_pairs):,} skipped · {label}",
                    value=bi + 1,
                    maximum=sheet_total + 1,
                )
                await busy.tick()
                docs = skipped_docs_from_pairs(
                    skipped_pairs,
                    feed_key=feed_key,
                    truck_field=field,
                    target_upload_id=str(batch.get("upload_id") or ""),
                    source_filename=source_filename,
                    sheet_label=str(batch.get("sheet_label") or ""),
                )
                try:
                    total_skipped += await svc.save_skipped_import_rows(docs)
                except Exception as exc:
                    show_warning(
                        parent,
                        "Skipped rows",
                        f"Could not park skipped rows for follow-up:\n{exc}",
                    )
                    total_skipped += len(skipped_pairs)
            out_batches.append({**batch, "docs": to_save})

        busy.update("Corrections applied", value=sheet_total + 1, maximum=sheet_total + 1)
        await busy.tick()

    return BulkImportTruckGateResult(
        batches=out_batches,
        skipped_count=total_skipped,
    )
