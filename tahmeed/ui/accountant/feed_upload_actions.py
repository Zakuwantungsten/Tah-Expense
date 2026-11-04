"""Shared soft-delete / undo helpers for Separate Expenses + Fuel uploads."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import QMessageBox, QWidget


def confirm_move_upload_to_trash(
    parent: QWidget,
    *,
    filename: str,
    count: int,
) -> bool:
    """Ask before soft-deleting an upload batch."""
    name = (filename or "").strip() or "this upload"
    return (
        QMessageBox.question(
            parent,
            "Move to Trash",
            f'Move "{name}" and all {count:,} records to Uploads Trash?\n\n'
            "You can restore from Uploads Trash, or Undo right after.",
        )
        == QMessageBox.Yes
    )


def _show_trashed_with_undo(parent: QWidget, *, filename: str, deleted: int) -> bool:
    """Success dialog with Undo. Returns True when the user chooses Undo."""
    name = (filename or "").strip() or "Upload"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle("Moved to Trash")
    box.setText(
        f'"{name}" moved to Uploads Trash '
        f"({deleted:,} record{'s' if deleted != 1 else ''})."
    )
    box.setInformativeText(
        "Undo restores it immediately. Or open Uploads Trash later to restore."
    )
    undo_btn = box.addButton("Undo", QMessageBox.ActionRole)
    box.addButton(QMessageBox.Ok)
    ok_btn = box.button(QMessageBox.Ok)
    if ok_btn is not None:
        box.setDefaultButton(ok_btn)
    box.exec()
    return box.clickedButton() is undo_btn


async def soft_delete_upload_with_undo(
    parent: QWidget,
    *,
    feed_slug: str,
    upload_id: str,
    filename: str = "",
    on_done: Optional[Callable[[], None]] = None,
) -> None:
    """Soft-delete via API, offer Undo, then refresh the caller."""
    from tahmeed.services import feed_service as feeds

    uid = (upload_id or "").strip()
    if not uid:
        return
    try:
        deleted = await feeds.delete_upload(feed_slug, uid)
    except Exception as exc:
        QMessageBox.critical(parent, "Delete Error", str(exc))
        return
    if deleted <= 0:
        QMessageBox.warning(
            parent, "Move to Trash", "No records were moved to trash."
        )
        return

    if _show_trashed_with_undo(parent, filename=filename, deleted=deleted):
        try:
            restored = await feeds.restore_upload(feed_slug, uid)
        except Exception as exc:
            QMessageBox.critical(parent, "Undo Failed", str(exc))
        else:
            if restored > 0:
                QMessageBox.information(
                    parent,
                    "Restored",
                    f'"{(filename or "Upload").strip() or "Upload"}" was restored.',
                )
            else:
                QMessageBox.warning(
                    parent, "Undo Failed", "Could not restore this upload."
                )
    if on_done is not None:
        on_done()
