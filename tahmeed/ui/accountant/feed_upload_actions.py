"""Shared soft-delete / undo helpers for Separate Expenses + Fuel uploads."""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence

import qtawesome as qta
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QWidget,
)

_RED = "#DC2626"
_RED_L = "#FEE2E2"
_BORDER = "#E5E7EB"
_T1 = "#111827"
_BG = "#F4F6F8"
_BLUE = "#0077C5"


def upload_browse_headers(data_headers: Sequence[str]) -> List[str]:
    """Checkbox + data columns + Action."""
    return ["", *list(data_headers), "ACTION"]


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


def confirm_move_uploads_to_trash(
    parent: QWidget,
    *,
    upload_count: int,
    record_count: int,
) -> bool:
    """Ask before soft-deleting several upload batches."""
    n = max(0, int(upload_count))
    if n <= 0:
        return False
    noun = "upload" if n == 1 else "uploads"
    return (
        QMessageBox.question(
            parent,
            "Move to Trash",
            f"Move {n:,} selected {noun} "
            f"({record_count:,} record{'s' if record_count != 1 else ''}) "
            "to Uploads Trash?\n\n"
            "You can restore from Uploads Trash, or Undo right after.",
        )
        == QMessageBox.Yes
    )


def _show_trashed_with_undo(parent: QWidget, *, filename: str, deleted: int) -> bool:
    """Success dialog with Undo. Returns True when the user chooses Undo."""
    from tahmeed.ui.dialogs.confirm_dialog import DialogAction, ask_choice

    name = (filename or "").strip() or "Upload"
    choice = ask_choice(
        parent,
        title="Moved to Trash",
        text=(
            f'"{name}" moved to Uploads Trash '
            f"({deleted:,} record{'s' if deleted != 1 else ''}).\n\n"
            "Undo restores it immediately. Or open Uploads Trash later to restore."
        ),
        actions=(
            DialogAction("undo", "Undo", "secondary"),
            DialogAction("ok", "OK", "primary"),
        ),
        badge="info",
        default_action_id="ok",
        dismiss_id="ok",
    )
    return choice == "undo"


def _show_bulk_trashed_with_undo(
    parent: QWidget, *, upload_count: int, deleted: int
) -> bool:
    from tahmeed.ui.dialogs.confirm_dialog import DialogAction, ask_choice

    choice = ask_choice(
        parent,
        title="Moved to Trash",
        text=(
            f"{upload_count:,} upload{'s' if upload_count != 1 else ''} moved to "
            f"Uploads Trash ({deleted:,} record{'s' if deleted != 1 else ''}).\n\n"
            "Undo restores them immediately. Or open Uploads Trash later to restore."
        ),
        actions=(
            DialogAction("undo", "Undo", "secondary"),
            DialogAction("ok", "OK", "primary"),
        ),
        badge="info",
        default_action_id="ok",
        dismiss_id="ok",
    )
    return choice == "undo"


async def soft_delete_upload_with_undo(
    parent: QWidget,
    *,
    feed_slug: str,
    upload_id: str,
    filename: str = "",
    on_done: Optional[Callable[[], None]] = None,
) -> None:
    """Soft-delete via API, offer Undo, then refresh the caller.

    Dialogs use ``await_ui`` and ``on_done`` is ``schedule_call``'d so Python
    3.14 + qasync does not stall the Uploads browse refresh after trash.
    """
    from tahmeed.services import feed_service as feeds
    from tahmeed.ui.async_utils import pause_background_polls, schedule_call
    from tahmeed.ui.widgets.export_runner import await_ui

    def _finish() -> None:
        if on_done is not None:
            schedule_call(on_done)

    uid = (upload_id or "").strip()
    if not uid:
        return
    try:
        deleted = await feeds.delete_upload(feed_slug, uid)
    except Exception as exc:
        with pause_background_polls(parent):
            await await_ui(lambda: QMessageBox.critical(parent, "Delete Error", str(exc)))
        return
    if deleted <= 0:
        with pause_background_polls(parent):
            await await_ui(lambda: QMessageBox.warning(
                parent, "Move to Trash", "No records were moved to trash.",
            ))
        return

    with pause_background_polls(parent):
        undo = await await_ui(
            lambda: _show_trashed_with_undo(parent, filename=filename, deleted=deleted)
        )
    if undo:
        try:
            restored = await feeds.restore_upload(feed_slug, uid)
        except Exception as exc:
            with pause_background_polls(parent):
                await await_ui(
                    lambda: QMessageBox.critical(parent, "Undo Failed", str(exc))
                )
        else:
            name = (filename or "Upload").strip() or "Upload"
            with pause_background_polls(parent):
                if restored > 0:
                    await await_ui(lambda: QMessageBox.information(
                        parent, "Restored", f'"{name}" was restored.',
                    ))
                else:
                    await await_ui(lambda: QMessageBox.warning(
                        parent, "Undo Failed", "Could not restore this upload.",
                    ))
    _finish()


async def soft_delete_uploads_with_undo(
    parent: QWidget,
    *,
    feed_slug: str,
    uploads: List[dict],
    on_done: Optional[Callable[[], None]] = None,
) -> None:
    """Soft-delete many uploads, then one Undo that restores all of them.

    Dialogs use ``await_ui`` and ``on_done`` is ``schedule_call``'d so Python
    3.14 + qasync does not stall the Uploads browse refresh after trash.
    """
    from tahmeed.services import feed_service as feeds
    from tahmeed.ui.async_utils import pause_background_polls, schedule_call
    from tahmeed.ui.widgets.export_runner import await_ui

    def _finish() -> None:
        if on_done is not None:
            schedule_call(on_done)

    items: List[tuple[str, str]] = []
    for up in uploads:
        uid = str(up.get("_id") or "").strip()
        if not uid:
            continue
        name = str(up.get("source_filename") or "").strip() or "Upload"
        sheet = str(up.get("sheet_label") or "").strip()
        label = f"{name} / {sheet}" if sheet else name
        items.append((uid, label))
    if not items:
        return

    deleted_total = 0
    done_ids: List[tuple[str, str]] = []
    try:
        for uid, _label in items:
            deleted = await feeds.delete_upload(feed_slug, uid)
            if deleted > 0:
                deleted_total += deleted
                done_ids.append((uid, _label))
    except Exception as exc:
        with pause_background_polls(parent):
            await await_ui(lambda: QMessageBox.critical(parent, "Delete Error", str(exc)))
        _finish()
        return

    if not done_ids:
        with pause_background_polls(parent):
            await await_ui(lambda: QMessageBox.warning(
                parent, "Move to Trash", "No records were moved to trash.",
            ))
        _finish()
        return

    with pause_background_polls(parent):
        undo = await await_ui(lambda: _show_bulk_trashed_with_undo(
            parent, upload_count=len(done_ids), deleted=deleted_total,
        ))
    if undo:
        failed = 0
        for uid, _label in done_ids:
            try:
                restored = await feeds.restore_upload(feed_slug, uid)
            except Exception:
                restored = 0
            if restored <= 0:
                failed += 1
        with pause_background_polls(parent):
            if failed:
                await await_ui(lambda: QMessageBox.warning(
                    parent,
                    "Undo Failed",
                    f"Could not restore {failed:,} of {len(done_ids):,} upload(s).",
                ))
            else:
                n = len(done_ids)
                await await_ui(lambda: QMessageBox.information(
                    parent,
                    "Restored",
                    f"{n:,} upload{'s' if n != 1 else ''} restored.",
                ))
    _finish()


def make_upload_check_item() -> QTableWidgetItem:
    item = QTableWidgetItem("")
    item.setFlags(
        Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
    )
    item.setCheckState(Qt.Unchecked)
    item.setTextAlignment(Qt.AlignCenter)
    return item


def make_upload_delete_button(on_click: Callable[[], None]) -> QWidget:
    """Compact trash button for the Action column."""
    btn = QPushButton()
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedSize(28, 24)
    btn.setToolTip("Move to Trash")
    try:
        btn.setIcon(qta.icon("mdi.trash-can-outline", color=_RED))
        btn.setIconSize(QSize(14, 14))
    except Exception:
        btn.setText("Del")
    btn.setStyleSheet(
        f"QPushButton{{background:transparent;border:1px solid transparent;"
        f"border-radius:4px;}}"
        f"QPushButton:hover{{background:{_RED_L};border-color:#FECACA;}}"
    )
    btn.clicked.connect(on_click)
    wrap = QWidget()
    wrap.setStyleSheet("background:transparent;")
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setAlignment(Qt.AlignCenter)
    lay.addWidget(btn)
    return wrap


def make_upload_browse_toolbar(
    *,
    on_select_all: Callable[[], None],
    on_clear: Callable[[], None],
) -> QWidget:
    """Toolbar with Select all / Clear on the right (bulk delete lives on the ribbon)."""
    bar = QWidget()
    bar.setStyleSheet("background:transparent;")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)

    def _secondary(text: str) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(28)
        b.setStyleSheet(
            f"QPushButton{{background:#FFF;color:{_T1};border:1px solid {_BORDER};"
            f"border-radius:5px;font-size:12px;font-weight:600;padding:0 10px;}}"
            f"QPushButton:hover{{background:{_BG};}}"
        )
        return b

    lay.addStretch(1)

    sel = _secondary("Select all")
    sel.clicked.connect(on_select_all)
    lay.addWidget(sel)

    clear = _secondary("Clear")
    clear.clicked.connect(on_clear)
    lay.addWidget(clear)

    return bar
