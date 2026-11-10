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


async def soft_delete_uploads_with_undo(
    parent: QWidget,
    *,
    feed_slug: str,
    uploads: List[dict],
    on_done: Optional[Callable[[], None]] = None,
) -> None:
    """Soft-delete many uploads, then one Undo that restores all of them."""
    from tahmeed.services import feed_service as feeds

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
        QMessageBox.critical(parent, "Delete Error", str(exc))
        if on_done is not None:
            on_done()
        return

    if not done_ids:
        QMessageBox.warning(
            parent, "Move to Trash", "No records were moved to trash."
        )
        if on_done is not None:
            on_done()
        return

    if _show_bulk_trashed_with_undo(
        parent, upload_count=len(done_ids), deleted=deleted_total
    ):
        failed = 0
        for uid, _label in done_ids:
            try:
                restored = await feeds.restore_upload(feed_slug, uid)
            except Exception:
                restored = 0
            if restored <= 0:
                failed += 1
        if failed:
            QMessageBox.warning(
                parent,
                "Undo Failed",
                f"Could not restore {failed:,} of {len(done_ids):,} upload(s).",
            )
        else:
            QMessageBox.information(
                parent,
                "Restored",
                f"{len(done_ids):,} upload{'s' if len(done_ids) != 1 else ''} restored.",
            )
    if on_done is not None:
        on_done()


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
    on_delete_selected: Callable[[], None],
    on_select_all: Callable[[], None],
    on_clear: Callable[[], None],
) -> tuple[QWidget, QPushButton]:
    """Toolbar with Select all / Clear / Delete selected. Returns (bar, delete_btn)."""
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

    sel = _secondary("Select all")
    sel.clicked.connect(on_select_all)
    lay.addWidget(sel)

    clear = _secondary("Clear")
    clear.clicked.connect(on_clear)
    lay.addWidget(clear)

    lay.addStretch(1)

    delete_btn = QPushButton("Delete selected")
    delete_btn.setCursor(Qt.PointingHandCursor)
    delete_btn.setFixedHeight(28)
    delete_btn.setEnabled(False)
    try:
        delete_btn.setIcon(qta.icon("mdi.trash-can-outline", color="#FFF"))
        delete_btn.setIconSize(QSize(14, 14))
    except Exception:
        pass
    delete_btn.setStyleSheet(
        f"QPushButton{{background:{_RED};color:#FFF;border:none;border-radius:5px;"
        f"font-size:12px;font-weight:600;padding:0 12px;}}"
        f"QPushButton:hover{{background:#B91C1C;}}"
        "QPushButton:disabled{background:#FCA5A5;}"
    )
    delete_btn.clicked.connect(on_delete_selected)
    lay.addWidget(delete_btn)
    return bar, delete_btn
