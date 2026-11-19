"""Paperwork → Records — company paper browse, custom folders, multi-upload.

No expense row required. Destinations: Item / Supplier / Separate / Custom.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import qtawesome as qta
from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.api_client import ApiError
from tahmeed.services.receipt_records_service import (
    create_custom_folder,
    delete_custom_folder,
    download_records_pack,
    list_destinations,
    list_record_files,
    rename_custom_folder,
    upload_record_files,
)
from tahmeed.ui.accountant.date_filters import style_calendar_popup
from tahmeed.ui.dialogs.confirm_dialog import ask_confirm
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading
from tahmeed.ui.widgets.popup_styles import with_light_popups
from tahmeed.ui.widgets.receipt_file_viewer import open_receipt_viewer

_BG = "#F3F4F6"
_CARD = "#FFFFFF"
_BORDER = "#E5E7EB"
_TEXT = "#111827"
_MUTED = "#6B7280"
_ACCENT = "#0077C5"
_NAVY = "#1B2B4B"
_STRIPE = "#F1F5F9"
_ROW_EVEN = "#FFFFFF"
_ROW_H = 24

_PAGE_SS = f"QWidget {{ background: {_BG}; }}"
_TABLE_SS = with_light_popups(f"""
QTableWidget {{
  background:{_CARD}; border:1px solid {_BORDER}; border-radius:6px;
  gridline-color:#F3F4F6; font-family:"Segoe UI"; font-size:12px; color:{_TEXT};
  selection-background-color:#E0F2FE; selection-color:{_TEXT};
  alternate-background-color:{_STRIPE};
}}
QHeaderView::section {{
  background:#F8FAFC; color:{_MUTED}; font-family:"Segoe UI"; font-size:11px; font-weight:600;
  border:none; border-bottom:1px solid {_BORDER}; border-right:1px solid #F3F4F6;
  padding:6px 8px;
}}
""")
_LIST_SS = with_light_popups(f"""
QListWidget {{
  background:{_CARD}; border:1px solid {_BORDER}; border-radius:6px;
  font:12px 'Segoe UI'; color:{_TEXT}; outline:none;
  padding:4px;
}}
QListWidget::item {{
  padding:6px 8px; border-radius:4px; margin:1px 0;
}}
QListWidget::item:selected {{
  background:#E0F2FE; color:{_TEXT};
}}
QListWidget::item:hover {{
  background:#F8FAFC;
}}
""")
_BTN_GHOST = with_light_popups(f"""
QPushButton {{
  background:{_CARD}; color:{_TEXT}; border:1px solid {_BORDER};
  border-radius:4px; padding:5px 10px; font:600 12px 'Segoe UI';
}}
QPushButton:hover {{ background:#F8FAFC; }}
QPushButton:disabled {{ color:#9CA3AF; }}
""")
_BTN_PRIMARY = with_light_popups(f"""
QPushButton {{
  background:{_ACCENT}; color:#FFFFFF; border:none;
  border-radius:4px; padding:5px 12px; font:600 12px 'Segoe UI';
}}
QPushButton:hover {{ background:#005EA3; }}
QPushButton:disabled {{ background:#93C5FD; }}
""")
_CHIP_BASE = """
QPushButton {{
  background:{bg}; color:{fg}; border:1px solid {border};
  border-radius:4px; padding:4px 10px; font:600 11px 'Segoe UI';
}}
QPushButton:hover {{ background:{hover}; }}
"""
_INPUT_SS = (
    "QLineEdit,QDateEdit{"
    f"font:12px 'Segoe UI';color:{_TEXT};"
    f"border:1px solid {_BORDER};border-radius:4px;padding:4px 8px;"
    "background:#FFFFFF;min-height:26px;}"
)

_KINDS: tuple[tuple[str, str, str], ...] = (
    ("custom", "Custom", "mdi.folder-plus-outline"),
    ("item", "Items", "mdi.tag-outline"),
    ("supplier", "Suppliers", "mdi.truck-delivery-outline"),
    ("separate", "Separate", "mdi.table-multiple"),
)


def _ghost(text: str, icon: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setIcon(qta.icon(icon, color="#374151"))
    btn.setIconSize(QSize(14, 14))
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(28)
    btn.setStyleSheet(_BTN_GHOST)
    return btn


def _primary(text: str, icon: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setIcon(qta.icon(icon, color="#FFFFFF"))
    btn.setIconSize(QSize(14, 14))
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(28)
    btn.setStyleSheet(_BTN_PRIMARY)
    return btn


def _chip_style(selected: bool) -> str:
    if selected:
        return _CHIP_BASE.format(
            bg="#E0F2FE", fg=_ACCENT, border=_ACCENT, hover="#DBEAFE"
        )
    return _CHIP_BASE.format(
        bg=_CARD, fg=_MUTED, border=_BORDER, hover="#F8FAFC"
    )


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _fmt_when(raw: Any) -> str:
    if isinstance(raw, datetime):
        return raw.astimezone().strftime("%Y-%m-%d %H:%M")
    if isinstance(raw, date):
        return raw.isoformat()
    text = str(raw or "").strip()
    if not text:
        return "—"
    if "T" in text:
        return text.replace("T", " ")[:16]
    return text[:16]


def _stripe(row: int) -> QColor:
    return QColor(_STRIPE if row % 2 else _ROW_EVEN)


class ReceiptRecordsPanel(QWidget):
    """Accountant Records home under Paperwork."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ReceiptRecordsPanel")
        self.setStyleSheet(_PAGE_SS)
        self._kind = "custom"
        self._destinations: dict[str, list[dict[str, Any]]] = {
            "custom": [],
            "item": [],
            "supplier": [],
            "separate": [],
        }
        self._selected: dict[str, Any] | None = None  # concrete destination
        self._files: list[dict[str, Any]] = []
        self._loading = False
        self._chip_buttons: dict[str, QPushButton] = {}
        self._build()
        asyncio.ensure_future(self.refresh())

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Records")
        title.setStyleSheet(
            f"color:{_NAVY}; font:700 18px 'Segoe UI'; background:transparent;"
        )
        sub = QLabel(
            "Company paper that does not need an expense row — file under Item, "
            "Supplier, Separate expense, or a reusable Custom folder."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"color:{_MUTED}; font:12px 'Segoe UI'; background:transparent;"
        )
        title_col.addWidget(title)
        title_col.addWidget(sub)
        head.addLayout(title_col, 1)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_rail())
        split.addWidget(self._build_files_pane())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 720])
        root.addWidget(split, 1)

        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color:{_MUTED}; font:11px 'Segoe UI'; background:transparent;"
        )
        root.addWidget(self._status)

    def _build_rail(self) -> QWidget:
        rail = QFrame()
        rail.setStyleSheet(
            f"QFrame{{background:{_CARD};border:1px solid {_BORDER};border-radius:8px;}}"
        )
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        chips = QHBoxLayout()
        chips.setSpacing(4)
        for key, label, _icon in _KINDS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setChecked(key == self._kind)
            btn.setStyleSheet(_chip_style(key == self._kind))
            btn.clicked.connect(lambda _=False, k=key: self._on_kind(k))
            self._chip_buttons[key] = btn
            chips.addWidget(btn)
        chips.addStretch(1)
        lay.addLayout(chips)

        self._dest_list = QListWidget()
        self._dest_list.setStyleSheet(_LIST_SS)
        self._dest_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._dest_list.currentItemChanged.connect(self._on_dest_changed)
        lay.addWidget(self._dest_list, 1)

        custom_row = QHBoxLayout()
        custom_row.setSpacing(6)
        self._new_btn = _ghost("New…", "mdi.folder-plus-outline")
        self._new_btn.clicked.connect(lambda: asyncio.ensure_future(self._new_custom()))
        self._rename_btn = _ghost("Rename…", "mdi.pencil-outline")
        self._rename_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._rename_custom())
        )
        self._delete_btn = _ghost("Delete…", "mdi.delete-outline")
        self._delete_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._delete_custom())
        )
        for b in (self._new_btn, self._rename_btn, self._delete_btn):
            custom_row.addWidget(b)
        custom_row.addStretch(1)
        lay.addLayout(custom_row)
        self._sync_custom_actions()
        return rail

    def _build_files_pane(self) -> QWidget:
        pane = QFrame()
        pane.setStyleSheet(
            f"QFrame{{background:{_CARD};border:1px solid {_BORDER};border-radius:8px;}}"
        )
        lay = QVBoxLayout(pane)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self._dest_hint = QLabel("Select a folder on the left to browse or upload.")
        self._dest_hint.setWordWrap(True)
        self._dest_hint.setStyleSheet(
            f"color:{_TEXT}; font:600 12px 'Segoe UI'; background:transparent;"
        )
        lay.addWidget(self._dest_hint)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self._upload_btn = _primary("Upload…", "mdi.file-upload-outline")
        self._upload_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._upload())
        )
        view_btn = _ghost("View", "mdi.eye-outline")
        view_btn.clicked.connect(self._view_selected)
        pack_btn = _ghost("Download ZIP", "mdi.zip-box-outline")
        pack_btn.setToolTip(
            "Download a ZIP of files for the current destination and date filters."
        )
        pack_btn.clicked.connect(lambda: asyncio.ensure_future(self._download_pack()))
        refresh_btn = _ghost("Refresh", "mdi.refresh")
        refresh_btn.clicked.connect(lambda: asyncio.ensure_future(self.refresh()))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search filename…")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(_INPUT_SS)
        self._search.returnPressed.connect(
            lambda: asyncio.ensure_future(self._load_files())
        )
        search_btn = _ghost("Search", "mdi.magnify")
        search_btn.clicked.connect(lambda: asyncio.ensure_future(self._load_files()))

        self._date_from = QDateEdit()
        self._date_from.setCalendarPopup(True)
        self._date_from.setDisplayFormat("yyyy-MM-dd")
        self._date_from.setDate(QDate.currentDate().addYears(-1))
        self._date_from.setStyleSheet(_INPUT_SS)
        style_calendar_popup(self._date_from)
        self._date_to = QDateEdit()
        self._date_to.setCalendarPopup(True)
        self._date_to.setDisplayFormat("yyyy-MM-dd")
        self._date_to.setDate(QDate.currentDate())
        self._date_to.setStyleSheet(_INPUT_SS)
        style_calendar_popup(self._date_to)

        toolbar.addWidget(self._upload_btn)
        toolbar.addWidget(view_btn)
        toolbar.addWidget(pack_btn)
        toolbar.addWidget(refresh_btn)
        toolbar.addSpacing(8)
        toolbar.addWidget(self._search, 1)
        toolbar.addWidget(search_btn)
        toolbar.addWidget(QLabel("From"))
        toolbar.addWidget(self._date_from)
        toolbar.addWidget(QLabel("To"))
        toolbar.addWidget(self._date_to)
        lay.addLayout(toolbar)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["S/NO", "Uploaded", "File", "Destination", "Note", "Size"]
        )
        self._table.setStyleSheet(_TABLE_SS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.setColumnWidth(0, 48)
        self._table.setColumnWidth(1, 130)
        self._table.setColumnWidth(3, 160)
        self._table.setColumnWidth(4, 140)
        self._table.setColumnWidth(5, 72)
        self._table.cellDoubleClicked.connect(lambda *_: self._view_selected())
        lay.addWidget(self._table, 1)
        self._sync_upload_enabled()
        return pane

    # ── Kind / destination selection ──────────────────────────────────────

    def _on_kind(self, kind: str) -> None:
        self._kind = kind
        for key, btn in self._chip_buttons.items():
            btn.setChecked(key == kind)
            btn.setStyleSheet(_chip_style(key == kind))
        self._sync_custom_actions()
        self._populate_dest_list()
        self._selected = None
        self._update_dest_hint()
        self._sync_upload_enabled()
        asyncio.ensure_future(self._load_files())

    def _populate_dest_list(self) -> None:
        self._dest_list.blockSignals(True)
        self._dest_list.clear()
        all_item = QListWidgetItem("All in this group")
        all_item.setData(Qt.UserRole, {"_all": True, "kind": self._kind})
        all_item.setIcon(qta.icon("mdi.folder-multiple-outline", color=_MUTED))
        self._dest_list.addItem(all_item)

        rows = list(self._destinations.get(self._kind) or [])
        for row in rows:
            label = str(row.get("label") or row.get("ref_key") or "Untitled")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, {**row, "kind": self._kind, "_all": False})
            item.setIcon(qta.icon("mdi.folder-outline", color=_ACCENT))
            self._dest_list.addItem(item)
        self._dest_list.blockSignals(False)
        if self._dest_list.count():
            self._dest_list.setCurrentRow(0)

    def _on_dest_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            self._selected = None
        else:
            data = current.data(Qt.UserRole) or {}
            if data.get("_all"):
                self._selected = None
            else:
                self._selected = dict(data)
        self._update_dest_hint()
        self._sync_upload_enabled()
        self._sync_custom_actions()
        asyncio.ensure_future(self._load_files())

    def _update_dest_hint(self) -> None:
        kind_label = next((lab for k, lab, _ in _KINDS if k == self._kind), self._kind)
        if self._selected:
            name = self._selected.get("label") or self._selected.get("ref_key") or ""
            self._dest_hint.setText(f"{kind_label} · {name}")
        else:
            self._dest_hint.setText(
                f"{kind_label} · All — pick a folder to upload, or browse everything below."
            )

    def _sync_custom_actions(self) -> None:
        is_custom = self._kind == "custom"
        self._new_btn.setVisible(is_custom)
        self._rename_btn.setVisible(is_custom)
        self._delete_btn.setVisible(is_custom)
        has_sel = bool(self._selected and self._selected.get("id"))
        self._rename_btn.setEnabled(is_custom and has_sel)
        self._delete_btn.setEnabled(is_custom and has_sel)

    def _sync_upload_enabled(self) -> None:
        self._upload_btn.setEnabled(self._selected is not None)

    # ── Data load ─────────────────────────────────────────────────────────

    async def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        show_widget_loading(self, "Loading Records…")
        try:
            payload = await list_destinations()
            self._destinations = {
                "custom": list(payload.get("custom") or []),
                "item": list(payload.get("items") or []),
                "supplier": list(payload.get("suppliers") or []),
                "separate": list(payload.get("separate") or []),
            }
            # Preserve selection by id/ref_key when possible
            prev = dict(self._selected) if self._selected else None
            self._populate_dest_list()
            if prev:
                self._reselect(prev)
            await self._load_files(show_overlay=False)
            self._status.setText(
                f"Custom {len(self._destinations['custom'])} · "
                f"Items {len(self._destinations['item'])} · "
                f"Suppliers {len(self._destinations['supplier'])} · "
                f"Separate {len(self._destinations['separate'])}"
            )
        except Exception as exc:
            self._status.setText(f"Could not load destinations: {exc}")
            QMessageBox.warning(self, "Records", f"Could not load destinations:\n{exc}")
        finally:
            hide_widget_loading(self)
            self._loading = False

    def _reselect(self, prev: dict[str, Any]) -> None:
        target_id = str(prev.get("id") or "")
        target_key = str(prev.get("ref_key") or "")
        for i in range(self._dest_list.count()):
            item = self._dest_list.item(i)
            data = item.data(Qt.UserRole) or {}
            if data.get("_all"):
                continue
            if target_id and str(data.get("id") or "") == target_id:
                self._dest_list.setCurrentRow(i)
                return
            if target_key and str(data.get("ref_key") or "") == target_key:
                self._dest_list.setCurrentRow(i)
                return

    async def _load_files(self, *, show_overlay: bool = True) -> None:
        if show_overlay:
            show_widget_loading(self._table, "Loading files…")
        try:
            kwargs: dict[str, Any] = {
                "kind": self._kind,
                "q": self._search.text().strip(),
                "date_from": self._date_from.date().toString("yyyy-MM-dd"),
                "date_to": self._date_to.date().toString("yyyy-MM-dd"),
                "limit": 300,
            }
            if self._selected:
                if self._kind == "custom" and self._selected.get("id"):
                    kwargs["folder_id"] = str(self._selected["id"])
                else:
                    kwargs["ref_key"] = str(self._selected.get("ref_key") or "")
            self._files = await list_record_files(**kwargs)
            self._fill_table()
            dest = (
                (self._selected or {}).get("label")
                or (self._selected or {}).get("ref_key")
                or f"All {self._kind}"
            )
            self._status.setText(f"{len(self._files)} file(s) · {dest}")
        except Exception as exc:
            self._files = []
            self._fill_table()
            self._status.setText(f"Could not load files: {exc}")
        finally:
            if show_overlay:
                hide_widget_loading(self._table)

    def _fill_table(self) -> None:
        self._table.setRowCount(0)
        for idx, row in enumerate(self._files):
            r = self._table.rowCount()
            self._table.insertRow(r)
            rec = row.get("record") or {}
            dest = str(rec.get("label") or rec.get("ref_key") or "—")
            values = [
                str(idx + 1),
                _fmt_when(row.get("uploaded_at")),
                str(row.get("original_filename") or "—"),
                dest,
                str(rec.get("note") or "—"),
                _fmt_size(int(row.get("size") or 0)),
            ]
            bg = _stripe(r)
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setBackground(bg)
                if c == 0:
                    item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                    item.setData(Qt.UserRole, str(row.get("id") or ""))
                self._table.setItem(r, c, item)
            self._table.setRowHeight(r, _ROW_H)

    def _selected_file_id(self) -> str:
        items = self._table.selectedItems()
        if not items:
            return ""
        row = items[0].row()
        cell = self._table.item(row, 0)
        return str(cell.data(Qt.UserRole) or "") if cell else ""

    def _view_selected(self) -> None:
        fid = self._selected_file_id()
        if not fid:
            QMessageBox.information(self, "Records", "Select a file to view.")
            return
        row = next((f for f in self._files if str(f.get("id")) == fid), None)
        title = str((row or {}).get("original_filename") or "")
        open_receipt_viewer(self, fid, title_hint=title)

    # ── Custom folder CRUD ────────────────────────────────────────────────

    async def _new_custom(self) -> None:
        label, ok = QInputDialog.getText(self, "New custom folder", "Folder name:")
        if not ok:
            return
        label = " ".join((label or "").split())
        if not label:
            return
        try:
            folder = await create_custom_folder(label)
        except ApiError as exc:
            QMessageBox.warning(self, "Records", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Records", f"Could not create folder:\n{exc}")
            return
        await self.refresh()
        self._kind = "custom"
        self._on_kind("custom")
        self._reselect({"id": folder.get("id"), "ref_key": folder.get("ref_key")})
        self._status.setText(f"Created custom folder “{folder.get('label')}”")

    async def _rename_custom(self) -> None:
        if not self._selected or not self._selected.get("id"):
            return
        current = str(self._selected.get("label") or "")
        label, ok = QInputDialog.getText(
            self, "Rename custom folder", "Folder name:", text=current
        )
        if not ok:
            return
        label = " ".join((label or "").split())
        if not label or label == current:
            return
        try:
            folder = await rename_custom_folder(str(self._selected["id"]), label)
        except ApiError as exc:
            QMessageBox.warning(self, "Records", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Records", f"Could not rename folder:\n{exc}")
            return
        await self.refresh()
        self._reselect({"id": folder.get("id"), "ref_key": folder.get("ref_key")})
        self._status.setText(f"Renamed to “{folder.get('label')}”")

    async def _delete_custom(self) -> None:
        if not self._selected or not self._selected.get("id"):
            return
        name = str(self._selected.get("label") or "this folder")
        if not ask_confirm(
            self,
            title="Delete custom folder",
            heading=f"Delete “{name}”?",
            body=(
                "Only empty folders can be deleted. "
                "If files are still filed here, delete will fail."
            ),
            accept_label="Delete",
            destructive=True,
        ):
            return
        try:
            await delete_custom_folder(str(self._selected["id"]))
        except ApiError as exc:
            QMessageBox.warning(self, "Records", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Records", f"Could not delete folder:\n{exc}")
            return
        self._selected = None
        await self.refresh()
        self._status.setText(f"Deleted custom folder “{name}”")

    # ── Upload ────────────────────────────────────────────────────────────

    async def _upload(self) -> None:
        if not self._selected:
            QMessageBox.information(
                self,
                "Records",
                "Select a concrete folder (not “All”) before uploading.",
            )
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Upload to Records",
            "",
            "Documents (*.pdf *.png *.jpg *.jpeg *.gif *.webp *.bmp *.tif *.tiff "
            "*.heic *.heif *.doc *.docx *.xls *.xlsx *.csv *.txt);;All files (*.*)",
        )
        if not paths:
            return
        kind = str(self._selected.get("kind") or self._kind)
        show_widget_loading(self, f"Uploading {len(paths)} file(s)…")
        try:
            result = await upload_record_files(
                paths,
                kind=kind,
                ref_key=str(self._selected.get("ref_key") or ""),
                folder_id=str(self._selected.get("id") or ""),
            )
            total = int(result.get("total") or 0)
            dest = (result.get("destination") or {}).get("label") or kind
            self._status.setText(f"Uploaded {total} file(s) → {dest}")
            await self._load_files(show_overlay=False)
        except ApiError as exc:
            QMessageBox.warning(self, "Upload failed", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Upload failed", str(exc))
        finally:
            hide_widget_loading(self)

    def _pack_filters(self) -> dict[str, str]:
        kwargs: dict[str, str] = {
            "kind": self._kind,
            "q": self._search.text().strip(),
            "date_from": self._date_from.date().toString("yyyy-MM-dd"),
            "date_to": self._date_to.date().toString("yyyy-MM-dd"),
        }
        if self._selected:
            if self._kind == "custom" and self._selected.get("id"):
                kwargs["folder_id"] = str(self._selected["id"])
            else:
                kwargs["ref_key"] = str(self._selected.get("ref_key") or "")
        return kwargs

    async def _download_pack(self) -> None:
        if not self._files:
            # Still allow pack from server filters even if table empty after a failed load
            pass
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Records ZIP",
            "tahmeed-records.zip",
            "ZIP files (*.zip)",
        )
        if not path:
            return
        show_widget_loading(self, "Building Records pack…")
        try:
            payload, meta = await download_records_pack(**self._pack_filters())
            out = Path(path)
            if out.suffix.lower() != ".zip":
                out = out.with_suffix(".zip")
            out.write_bytes(payload)
            count = meta.get("file_count") or "?"
            self._status.setText(
                f"Saved Records pack ({count} file(s)) → {out.name}"
            )
        except ApiError as exc:
            QMessageBox.warning(self, "Pack failed", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Pack failed", str(exc))
        finally:
            hide_widget_loading(self)
