"""Paperwork → Trash — Windows Recycle Bin style for receipt files."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.receipt_storage_service import (
    empty_receipt_trash,
    list_receipt_trash,
    purge_receipt_trash,
    restore_all_receipt_trash,
    restore_receipt_trash,
)
from tahmeed.ui.dialogs.confirm_dialog import ask_confirm
from tahmeed.ui.widgets.popup_styles import apply_light_menu, with_light_popups
from tahmeed.ui.widgets.receipt_file_viewer import open_receipt_viewer
from tahmeed.ui.widgets.windows_command_bar import WindowsExplorerChrome

_BG = "#F3F4F6"
_CARD = "#FFFFFF"
_TEXT = "#111827"
_MUTED = "#6B7280"
_BORDER = "#E5E7EB"
_ACCENT = "#0077C5"
_SEL = "#E0F2FE"
_DANGER = "#B91C1C"

_PAGE_SS = with_light_popups(
    f"QWidget#ReceiptTrashPanel {{ background: {_BG}; color: {_TEXT}; }}"
)
_TABLE_SS = with_light_popups(f"""
QTableWidget {{
  border:1px solid {_BORDER}; border-radius:6px; background:{_CARD}; color:{_TEXT};
  font-size:12px; font-family:\"Segoe UI\"; gridline-color:#F3F4F6; alternate-background-color:#F8FAFC;
  selection-background-color:{_SEL}; selection-color:{_TEXT};
}}
QHeaderView::section {{
  background:{_BG}; border:none; border-bottom:1px solid {_BORDER};
  padding:6px 8px; font-weight:600; font-size:11px; font-family:\"Segoe UI\"; color:{_MUTED};
}}
""")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _fmt_when(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    text = str(value or "").strip()
    if not text:
        return "—"
    return text.replace("T", " ")[:16]


class ReceiptTrashPanel(QWidget):
    """Windows Recycle Bin analogue for soft-deleted receipt files."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ReceiptTrashPanel")
        self.setStyleSheet(_PAGE_SS)
        self._rows: list[dict[str, Any]] = []
        self._build()
        asyncio.ensure_future(self.refresh())

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._chrome = WindowsExplorerChrome(
            search_placeholder="Search Trash",
            address="Paperwork > Trash",
        )
        self._chrome.set_address_icon("mdi.trash-can-outline")
        self._chrome.set_nav_enabled(back=False, forward=False, up=False)
        self._chrome.refresh_clicked.connect(
            lambda: asyncio.ensure_future(self.refresh())
        )
        self._chrome.search_submitted.connect(
            lambda _q: asyncio.ensure_future(self.refresh())
        )
        self._search = self._chrome.search

        ribbon = self._chrome.ribbon
        ribbon.add_button(
            "restore",
            "mdi.restore",
            "Restore selected",
            lambda: asyncio.ensure_future(self._restore_selected()),
        )
        ribbon.add_button(
            "restore_all",
            "mdi.folder-clock-outline",
            "Restore all items",
            lambda: asyncio.ensure_future(self._restore_all()),
        )
        ribbon.add_sep()
        ribbon.add_button(
            "delete",
            "mdi.delete-forever",
            "Delete permanently",
            lambda: asyncio.ensure_future(self._purge_selected()),
            color=_DANGER,
        )
        ribbon.add_button(
            "empty",
            "mdi.delete-empty-outline",
            "Empty Trash",
            lambda: asyncio.ensure_future(self._empty_trash()),
            color=_DANGER,
        )
        ribbon.add_sep()
        ribbon.add_button(
            "check_all",
            "mdi.checkbox-marked-outline",
            "Check all",
            lambda: self._set_checks(True),
        )
        ribbon.add_button(
            "clear_checks",
            "mdi.checkbox-blank-outline",
            "Clear checks",
            lambda: self._set_checks(False),
        )
        ribbon.add_button(
            "view",
            "mdi.eye-outline",
            "View",
            self._view_selected,
        )
        ribbon.add_stretch()
        self._cmd = ribbon
        root.addWidget(self._chrome)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            [
                "",
                "Name",
                "Original location",
                "Date deleted",
                "Size",
                "Item type",
            ]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.cellDoubleClicked.connect(lambda *_: self._view_selected())
        self._table.setStyleSheet(_TABLE_SS)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 36)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        root.addWidget(self._table, 1)

        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color:{_MUTED}; font-size:11px; font-family:\"Segoe UI\"; background:transparent;"
        )
        root.addWidget(self._status)

    async def refresh(self) -> None:
        q = self._search.text().strip()
        try:
            payload = await list_receipt_trash(q=q)
        except Exception as exc:
            QMessageBox.critical(self, "Trash", str(exc))
            return
        self._rows = list(payload.get("items") or [])
        total = int(payload.get("total") or len(self._rows))
        self._table.setRowCount(0)
        for row in self._rows:
            r = self._table.rowCount()
            self._table.insertRow(r)
            check = QTableWidgetItem("")
            check.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
            )
            check.setCheckState(Qt.Unchecked)
            name = str(row.get("original_filename") or "—")
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in {"pdf"}:
                icon = qta.icon("mdi.file-pdf-box", color="#B91C1C")
            elif ext in {"xls", "xlsx", "csv"}:
                icon = qta.icon("mdi.file-excel", color="#15803D")
            elif ext in {"png", "jpg", "jpeg", "gif", "webp"}:
                icon = qta.icon("mdi.file-image", color=_ACCENT)
            else:
                icon = qta.icon("mdi.file-document-outline", color=_MUTED)
            name_item = QTableWidgetItem(icon, name)
            values = [
                check,
                name_item,
                QTableWidgetItem(str(row.get("original_location") or "—")),
                QTableWidgetItem(_fmt_when(row.get("trashed_at"))),
                QTableWidgetItem(_fmt_size(int(row.get("size") or 0))),
                QTableWidgetItem(str(row.get("item_type") or "File")),
            ]
            for c, cell in enumerate(values):
                cell.setData(Qt.UserRole, row)
                cell.setForeground(QBrush(QColor(_TEXT)))
                self._table.setItem(r, c, cell)
        shown = len(self._rows)
        if q:
            self._status.setText(f"{shown} item(s) match “{q}” · {total} in Trash")
        else:
            self._status.setText(f"{total} item(s)")
        self._chrome.set_address("Paperwork > Trash", push_history=False)
        self._sync_cmd()

    def _sync_cmd(self) -> None:
        has = bool(self._rows)
        self._cmd.set_enabled("restore_all", has)
        self._cmd.set_enabled("empty", has)

    def _set_checks(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self._table.rowCount()):
            cell = self._table.item(r, 0)
            if cell is not None:
                cell.setCheckState(state)

    def _checked_or_selected_ids(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for r in range(self._table.rowCount()):
            cell = self._table.item(r, 0)
            if cell is None or cell.checkState() != Qt.Checked:
                continue
            row = cell.data(Qt.UserRole) or {}
            fid = str(row.get("id") or "").strip()
            if fid and fid not in seen:
                seen.add(fid)
                ids.append(fid)
        if ids:
            return ids
        for item in self._table.selectedItems():
            row = item.data(Qt.UserRole) or {}
            fid = str(row.get("id") or "").strip()
            if fid and fid not in seen:
                seen.add(fid)
                ids.append(fid)
        return ids

    def _view_selected(self) -> None:
        ids = self._checked_or_selected_ids()
        if not ids:
            QMessageBox.information(self, "Trash", "Select a file first.")
            return
        fid = ids[0]
        name = next(
            (
                str(r.get("original_filename") or "")
                for r in self._rows
                if str(r.get("id") or "") == fid
            ),
            "",
        )
        open_receipt_viewer(self, fid, title_hint=name)

    def _context_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        if index.isValid():
            self._table.selectRow(index.row())
        menu = apply_light_menu(QMenu(self))
        view_act = menu.addAction("Open")
        restore_act = menu.addAction("Restore")
        purge_act = menu.addAction("Delete permanently")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == view_act:
            self._view_selected()
        elif chosen == restore_act:
            asyncio.ensure_future(self._restore_selected())
        elif chosen == purge_act:
            asyncio.ensure_future(self._purge_selected())

    async def _restore_selected(self) -> None:
        ids = self._checked_or_selected_ids()
        if not ids:
            QMessageBox.information(self, "Trash", "Select item(s) to restore.")
            return
        n = len(ids)
        if not ask_confirm(
            self,
            title="Restore",
            heading=f"Restore {n} item{'s' if n != 1 else ''}?",
            body="Selected files return to Receipt storage / Records from Trash.",
            accept_label="Restore",
            destructive=False,
        ):
            return
        try:
            result = await restore_receipt_trash(ids)
            self._status.setText(
                f"Restored {int(result.get('restored') or 0)} item(s)"
            )
            await self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Restore failed", str(exc))

    async def _restore_all(self) -> None:
        if not self._rows:
            return
        if not ask_confirm(
            self,
            title="Restore all items",
            heading="Restore everything in Trash?",
            body="All soft-deleted receipt files return to browse / Records.",
            accept_label="Restore all",
            destructive=False,
        ):
            return
        try:
            result = await restore_all_receipt_trash()
            self._status.setText(
                f"Restored {int(result.get('restored') or 0)} item(s)"
            )
            await self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Restore all failed", str(exc))

    async def _purge_selected(self) -> None:
        ids = self._checked_or_selected_ids()
        if not ids:
            QMessageBox.information(
                self, "Trash", "Select item(s) to delete permanently."
            )
            return
        if not ask_confirm(
            self,
            title="Delete permanently",
            heading=f"Permanently delete {len(ids)} item(s)?",
            body=(
                "This cannot be undone. Files linked to expenses are unlinked "
                "from those expenses, then removed."
            ),
            accept_label="Delete permanently",
            destructive=True,
        ):
            return
        try:
            result = await purge_receipt_trash(ids)
            purged = int(result.get("purged") or 0)
            skipped = int(result.get("skipped") or 0)
            # Drop purged rows immediately so the list never looks stuck.
            if purged > 0:
                gone = set(ids)
                self._rows = [
                    r for r in self._rows if str(r.get("id") or "") not in gone
                ]
                self._table.setRowCount(0)
            self._status.setText(
                f"Purged {purged}"
                + (f" · skipped {skipped}" if skipped else "")
            )
            await self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Delete failed", str(exc))

    async def _empty_trash(self) -> None:
        if not self._rows:
            return
        if not ask_confirm(
            self,
            title="Empty Trash",
            heading="Empty Trash?",
            body=(
                "Permanently deletes all items in Trash. "
                "Expense-linked files are unlinked first. This cannot be undone."
            ),
            accept_label="Empty Trash",
            destructive=True,
        ):
            return
        try:
            result = await empty_receipt_trash()
            purged = int(result.get("purged") or 0)
            skipped = int(result.get("skipped") or 0)
            if purged > 0 and skipped == 0:
                self._rows = []
                self._table.setRowCount(0)
            self._status.setText(
                f"Emptied · purged {purged}"
                + (f" · skipped {skipped}" if skipped else "")
            )
            await self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Empty Trash failed", str(exc))
