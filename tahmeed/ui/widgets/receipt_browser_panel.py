"""Accountant receipt storage — Explorer (bucket/folder) + Pack & download."""

from __future__ import annotations

import io
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import qtawesome as qta
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.receipt_chase_service import (
    download_consolidate_pack,
    fetch_receipt_file_bytes,
    list_receipt_library,
    rematerialize_files,
)
from tahmeed.services.receipt_storage_service import (
    create_storage_folder,
    delete_storage_folder,
    get_storage_status,
    list_storage_objects,
    list_storage_tree,
    rename_storage_folder,
    trash_receipt_files,
)
from tahmeed.ui.async_utils import await_blocking, schedule_coro
from tahmeed.ui.dialogs.confirm_dialog import ask_confirm
from tahmeed.ui.widgets.popup_styles import apply_light_menu, with_light_popups
from tahmeed.ui.widgets.receipt_file_viewer import open_receipt_viewer
from tahmeed.ui.widgets.windows_command_bar import WindowsExplorerChrome
from tahmeed.ui.widgets.windows_drive_tiles import (
    WindowsDevicesAndDrives,
    mounts_from_storage_status,
)

_BG = "#F3F4F6"
_CARD = "#FFFFFF"
_TEXT = "#1F2937"
_MUTED = "#6B7280"
_BORDER = "#E5E7EB"
_PRIMARY = "#1B2B4B"
_FOLDER = "#D97706"
_ACCENT = "#0077C5"
_SEL = "#E0F2FE"
_CUT_FADE = "#9CA3AF"

_PAGE_SS = with_light_popups(
    f"QWidget#ReceiptBrowserPanel {{ background: {_BG}; color: {_TEXT}; }}"
)
_TAB_SS = with_light_popups(f"""
QTabWidget::pane {{
  border:1px solid {_BORDER}; border-radius:8px; top:-1px; background:{_CARD};
}}
QTabBar::tab {{
  background:{_CARD}; color:{_MUTED}; font-weight:600; font-size:12px; font-family:\"Segoe UI\";
  padding:8px 14px; margin-right:2px; border:1px solid transparent;
  border-bottom:2px solid transparent; border-top-left-radius:6px;
  border-top-right-radius:6px;
}}
QTabBar::tab:selected {{
  color:{_ACCENT}; background:{_CARD}; border-bottom:2px solid {_ACCENT};
}}
QTabBar::tab:hover {{ color:{_TEXT}; background:#F8FAFC; }}
""")
_INPUT_SS = (
    f"QLineEdit,QDateEdit{{font-size:12px; font-family:\"Segoe UI\";color:{_TEXT};"
    f"border:1px solid {_BORDER};border-radius:6px;padding:4px 8px;"
    f"background:{_CARD};min-height:28px;selection-background-color:{_SEL};"
    f"selection-color:{_TEXT};}}"
    f"QLineEdit:focus,QDateEdit:focus{{border-color:{_ACCENT};}}"
)
_TREE_SS = with_light_popups(f"""
QTreeWidget {{
  border:none; background:{_CARD}; color:{_TEXT}; font-size:13px; font-family:\"Segoe UI\";
  outline:none; show-decoration-selected:0;
}}
QTreeWidget::item {{
  padding:4px 2px; color:{_TEXT}; background:transparent;
  border:none; outline:none;
}}
QTreeWidget::item:hover {{ background:#F8FAFC; color:{_TEXT}; }}
QTreeWidget::item:selected,
QTreeWidget::item:selected:active,
QTreeWidget::item:selected:!active {{
  background:{_SEL}; color:{_TEXT}; border:none; outline:none;
}}
QTreeWidget::branch,
QTreeWidget::branch:selected,
QTreeWidget::branch:selected:active {{
  background:transparent; border:none;
}}
""")
_TABLE_SS = with_light_popups(f"""
QTableWidget {{
  border:none; background:{_CARD}; color:{_TEXT}; font-size:12px; font-family:\"Segoe UI\";
  gridline-color:#F3F4F6; alternate-background-color:#F8FAFC;
  selection-background-color:{_SEL}; selection-color:{_TEXT};
}}
QHeaderView::section {{
  background:{_BG}; border:none; padding:6px;
  font-weight:600; font-size:11px; font-family:\"Segoe UI\"; color:{_MUTED};
}}
""")


def _ghost(text: str, icon: str) -> QPushButton:
    btn = QPushButton(qta.icon(icon, color=_PRIMARY), text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(30)
    btn.setStyleSheet(
        f"QPushButton{{background:{_CARD};color:{_TEXT};border:1px solid {_BORDER};"
        f"border-radius:6px;padding:0 10px;font-weight:600; font-size:12px; font-family:\"Segoe UI\";}}"
        f"QPushButton:hover{{background:{_BG};}}"
    )
    return btn


def _primary(text: str, icon: str) -> QPushButton:
    btn = QPushButton(qta.icon(icon, color="#FFFFFF"), text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(30)
    btn.setStyleSheet(
        f"QPushButton{{background:{_PRIMARY};color:#FFFFFF;border:none;"
        f"border-radius:6px;padding:0 12px;font-weight:600; font-size:12px; font-family:\"Segoe UI\";}}"
        f"QPushButton:hover{{background:#243a63;}}"
    )
    return btn


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _tree_item(label: str, *, icon=None) -> QTreeWidgetItem:
    """Tree row with forced dark text — OS dark mode otherwise paints white-on-white."""
    item = QTreeWidgetItem([label])
    item.setForeground(0, QBrush(QColor(_TEXT)))
    if icon is not None:
        item.setIcon(0, icon)
    return item


class ReceiptBrowserPanel(QWidget):
    """Windows/bucket-style browse of the receipt disk + pack/download tools."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ReceiptBrowserPanel")
        self.setStyleSheet(_PAGE_SS)
        self._prefix = ""
        self._at_this_pc = True  # Storages overview (server mounts)
        self._objects: list[dict[str, Any]] = []
        self._day_rows: list[dict[str, Any]] = []
        self._tree_loading = False
        self._clip: dict[str, Any] | None = None  # {mode, files:[{file_id,name}]}
        self._storage_status: dict[str, Any] = {}
        self._build()
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._clear_clip)
        schedule_coro(self._bootstrap())

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(_TAB_SS)
        self._tabs.addTab(
            self._build_explorer_tab(),
            qta.icon("mdi.folder-open-outline", color=_ACCENT),
            "Explorer",
        )
        self._tabs.addTab(
            self._build_pack_tab(),
            qta.icon("mdi.package-down", color=_ACCENT),
            "Pack & download",
        )
        root.addWidget(self._tabs, 1)

        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color:{_MUTED}; font-size:11px; font-family:\"Segoe UI\"; background:transparent;"
        )
        root.addWidget(self._status)

    # ── Explorer (directory / bucket) ─────────────────────────────────────

    def _build_explorer_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        self._chrome = WindowsExplorerChrome(
            search_placeholder="Search storages",
            address="Storages",
        )
        self._chrome.set_address_icon("mdi.harddisk")
        self._chrome.back_clicked.connect(self._nav_back)
        self._chrome.forward_clicked.connect(self._nav_forward)
        self._chrome.up_clicked.connect(lambda: schedule_coro(self._go_up()))
        self._chrome.refresh_clicked.connect(
            lambda: schedule_coro(self._reload_explorer())
        )
        self._chrome.search_submitted.connect(
            lambda _q: schedule_coro(self._load_objects())
        )
        self._chrome.address_activated.connect(self._on_address_activated)
        self._search = self._chrome.search
        # Keep a crumb alias for older helpers that set crumb text
        self._crumb = self._chrome.address

        self._cmd = self._chrome.ribbon
        self._cmd.add_button(
            "new", "mdi.plus-circle-outline", "New folder",
            lambda: schedule_coro(self._new_folder()),
        )
        self._cmd.add_sep()
        self._cmd.add_button(
            "cut", "mdi.content-cut", "Cut", self._clip_cut
        )
        self._cmd.add_button(
            "copy", "mdi.content-copy", "Copy", self._clip_copy
        )
        self._cmd.add_button(
            "paste", "mdi.content-paste", "Paste (download clipboard; Cut also trashes)",
            lambda: schedule_coro(self._clip_paste()),
        )
        self._cmd.add_button(
            "rename", "mdi.rename-box", "Rename folder",
            lambda: schedule_coro(self._rename_selected_folder()),
        )
        self._cmd.add_button(
            "download", "mdi.download-outline", "Download checked / selected",
            lambda: schedule_coro(self._download_selected()),
        )
        self._cmd.add_button(
            "delete", "mdi.trash-can-outline", "Delete / Move to Trash",
            lambda: schedule_coro(self._toolbar_delete()),
            color="#B91C1C",
        )
        self._cmd.add_sep()
        self._cmd.add_button(
            "check_all", "mdi.checkbox-marked-outline", "Check all files",
            lambda: self._set_file_checks(True),
        )
        self._cmd.add_button(
            "clear_checks", "mdi.checkbox-blank-outline", "Clear checks",
            lambda: self._set_file_checks(False),
        )
        self._cmd.add_stretch()
        lay.addWidget(self._chrome)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        left.setStyleSheet("background:transparent; border:none;")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 8, 0)
        left_lay.setSpacing(0)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(True)
        self._tree.setStyleSheet(_TREE_SS)
        self._tree.itemExpanded.connect(self._on_tree_expand)
        self._tree.itemClicked.connect(self._on_tree_click)
        left_lay.addWidget(self._tree, 1)
        split.addWidget(left)

        right = QWidget()
        right.setStyleSheet("background:transparent; border:none;")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        # Optional browse caption (hidden on Storages overview)
        self._contents_lbl = QLabel("Folder contents")
        self._contents_lbl.setStyleSheet(
            f"color:{_MUTED}; font-weight:600; font-size:11px; font-family:\"Segoe UI\"; background:transparent;"
            "border:none; padding:0 0 4px 0;"
        )
        self._contents_lbl.setVisible(False)
        right_lay.addWidget(self._contents_lbl)

        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet("background:transparent; border:none;")
        self._drives_page = WindowsDevicesAndDrives()
        self._drives_page.mount_activated.connect(self._on_mount_activated)
        self._content_stack.addWidget(self._drives_page)  # index 0 = Storages

        browse_page = QWidget()
        browse_lay = QVBoxLayout(browse_page)
        browse_lay.setContentsMargins(0, 0, 0, 0)
        browse_lay.setSpacing(0)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["", "Name", "Type", "Status", "Size", "Path"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._table_context_menu)
        self._table.setIconSize(self._table.iconSize())
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 36)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setStretchLastSection(True)
        self._table.setStyleSheet(_TABLE_SS)
        self._table.cellDoubleClicked.connect(self._on_content_double_clicked)
        browse_lay.addWidget(self._table, 1)
        self._content_stack.addWidget(browse_page)  # index 1 = folder browse
        right_lay.addWidget(self._content_stack, 1)
        split.addWidget(right)
        split.setSizes([280, 720])
        lay.addWidget(split, 1)

        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._tree_context_menu)
        return page

    # ── Pack & download ───────────────────────────────────────────────────

    def _build_pack_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        tip = QLabel(
            "Ship receipts for a date range / item / payee into one ZIP or folder. "
            "Day list below uses Library (upload date) so you can see volume before packing "
            "(pack itself uses transaction date, same as evidence consolidate)."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"color:{_MUTED}; font-size:11px; font-family:\"Segoe UI\"; background:transparent;"
        )
        lay.addWidget(tip)

        filters = QHBoxLayout()
        self._pack_from = QDateEdit()
        self._pack_from.setCalendarPopup(True)
        self._pack_from.setDisplayFormat("yyyy-MM-dd")
        self._pack_from.setDate(QDate.currentDate().addMonths(-1))
        self._pack_from.setStyleSheet(_INPUT_SS)
        self._pack_to = QDateEdit()
        self._pack_to.setCalendarPopup(True)
        self._pack_to.setDisplayFormat("yyyy-MM-dd")
        self._pack_to.setDate(QDate.currentDate())
        self._pack_to.setStyleSheet(_INPUT_SS)
        self._pack_item = QLineEdit()
        self._pack_item.setPlaceholderText("Item (optional)")
        self._pack_item.setStyleSheet(_INPUT_SS)
        self._pack_payee = QLineEdit()
        self._pack_payee.setPlaceholderText("Payee (optional)")
        self._pack_payee.setStyleSheet(_INPUT_SS)
        self._pack_own = QLineEdit()
        self._pack_own.setPlaceholderText("Ownership (optional)")
        self._pack_own.setStyleSheet(_INPUT_SS)
        self._pack_approved = QCheckBox("Approved only")
        self._pack_approved.setChecked(True)
        self._pack_approved.setStyleSheet(
            f"QCheckBox{{color:{_TEXT};font-size:12px; font-family:\"Segoe UI\";background:transparent;}}"
        )
        for text in ("From", "To"):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:{_MUTED};font-size:11px; font-family:\"Segoe UI\";background:transparent;"
            )
            filters.addWidget(lbl)
            if text == "From":
                filters.addWidget(self._pack_from)
            else:
                filters.addWidget(self._pack_to)
        filters.addWidget(self._pack_item, 1)
        filters.addWidget(self._pack_payee, 1)
        filters.addWidget(self._pack_own, 1)
        filters.addWidget(self._pack_approved)
        lay.addLayout(filters)

        year_row = QHBoxLayout()
        year_row.addWidget(QLabel("Quick year:"))
        this_year = QDate.currentDate().year()
        for y in (this_year, this_year - 1, this_year - 2):
            btn = _ghost(str(y), "mdi.calendar")
            btn.clicked.connect(lambda _=False, year=y: self._set_year(year))
            year_row.addWidget(btn)
        year_row.addStretch()
        scan_btn = _ghost("Scan days in range", "mdi.chart-bar")
        scan_btn.clicked.connect(lambda: schedule_coro(self._scan_days()))
        year_row.addWidget(scan_btn)
        lay.addLayout(year_row)

        actions = QHBoxLayout()
        zip_btn = _primary("Download ZIP", "mdi.zip-box-outline")
        zip_btn.clicked.connect(
            lambda: schedule_coro(self._pack_download(to_folder=False))
        )
        folder_btn = _ghost("Save to folder…", "mdi.folder-download-outline")
        folder_btn.clicked.connect(
            lambda: schedule_coro(self._pack_download(to_folder=True))
        )
        day_zip = _ghost("Pack selected days", "mdi.calendar-export")
        day_zip.clicked.connect(
            lambda: schedule_coro(self._pack_selected_days())
        )
        actions.addWidget(zip_btn)
        actions.addWidget(folder_btn)
        actions.addWidget(day_zip)
        actions.addStretch()
        lay.addLayout(actions)

        days_lbl = QLabel("Receipts by upload day (select rows, then Pack selected days)")
        days_lbl.setStyleSheet(
            f"color:{_TEXT}; font-weight:600; font-size:12px; font-family:\"Segoe UI\"; background:transparent;"
        )
        lay.addWidget(days_lbl)
        self._days = QTableWidget(0, 4)
        self._days.setHorizontalHeaderLabels(
            ["Day", "Files", "Approved", "Items (sample)"]
        )
        self._days.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._days.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._days.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._days.setAlternatingRowColors(True)
        self._days.verticalHeader().setVisible(False)
        self._days.setShowGrid(False)
        self._days.horizontalHeader().setStretchLastSection(True)
        self._days.setStyleSheet(
            _TABLE_SS
            + f"QTableWidget{{border:1px solid {_BORDER}; border-radius:6px;}}"
        )
        lay.addWidget(self._days, 1)
        return page

    # ── Bootstrap / navigation ────────────────────────────────────────────

    def refresh(self) -> None:
        schedule_coro(self._reload_explorer())

    async def _bootstrap(self) -> None:
        await self._reload_explorer()

    def _set_year(self, year: int) -> None:
        self._pack_from.setDate(QDate(year, 1, 1))
        self._pack_to.setDate(QDate(year, 12, 31))

    def _crumb_text(self) -> str:
        if self._at_this_pc:
            return "Storages"
        if not self._prefix:
            return "Storages > Receipt disk"
        return "Storages > Receipt disk > " + self._prefix.replace("/", " > ")

    def _sync_address_chrome(self, *, push_history: bool = True) -> None:
        text = self._crumb_text()
        if hasattr(self, "_chrome"):
            self._chrome.set_address(text, push_history=push_history)
            self._chrome.set_nav_enabled(up=bool(self._prefix) or not self._at_this_pc)
            if self._at_this_pc:
                self._chrome.set_search_placeholder("Search storages")
            else:
                self._chrome.set_search_placeholder(
                    f"Search {self._prefix.split('/')[-1] if self._prefix else 'Receipt disk'}"
                )
        elif hasattr(self, "_crumb"):
            self._crumb.setText(text)

    def _show_this_pc_view(self) -> None:
        if hasattr(self, "_content_stack"):
            self._content_stack.setCurrentIndex(0)
        if hasattr(self, "_contents_lbl"):
            self._contents_lbl.setVisible(False)
        if hasattr(self, "_search"):
            self._search.setEnabled(False)

    def _show_browse_view(self) -> None:
        if hasattr(self, "_content_stack"):
            self._content_stack.setCurrentIndex(1)
        if hasattr(self, "_contents_lbl"):
            self._contents_lbl.setText("Folder contents")
            self._contents_lbl.setVisible(False)
        if hasattr(self, "_search"):
            self._search.setEnabled(True)

    def _on_mount_activated(self, mount: dict[str, Any]) -> None:
        prefix = str(mount.get("open_prefix") or "")
        schedule_coro(self._open_mount(prefix))

    async def _open_mount(self, prefix: str = "") -> None:
        self._at_this_pc = False
        await self._go_prefix(prefix or "")

    async def _go_this_pc(self, *, push_history: bool = True) -> None:
        self._at_this_pc = True
        self._prefix = ""
        if hasattr(self, "_search"):
            self._search.clear()
        self._sync_address_chrome(push_history=push_history)
        self._select_tree_this_pc()
        await self._load_this_pc()

    def _nav_back(self) -> None:
        if not hasattr(self, "_chrome"):
            return
        path = self._chrome.history_back()
        if path is None:
            return
        schedule_coro(self._go_address_path(path, push_history=False))

    def _nav_forward(self) -> None:
        if not hasattr(self, "_chrome"):
            return
        path = self._chrome.history_forward()
        if path is None:
            return
        schedule_coro(self._go_address_path(path, push_history=False))

    def _on_address_activated(self, text: str) -> None:
        schedule_coro(self._go_address_path(text, push_history=True))

    async def _go_address_path(self, text: str, *, push_history: bool) -> None:
        raw = (text or "").strip()
        lower = raw.casefold().strip()
        # Bare Storages overview (also accept legacy “This PC”)
        if lower in {
            "",
            "storages",
            "storages >",
            "this pc",
            "this pc >",
        }:
            await self._go_this_pc(push_history=push_history)
            return
        marker = "receipt disk"
        if marker in lower:
            after = raw[lower.index(marker) + len(marker) :].lstrip(" >\\/")
            prefix = after.replace(" > ", "/").replace("\\", "/").strip("/")
            self._at_this_pc = False
            self._prefix = prefix
            if hasattr(self, "_search"):
                self._search.clear()
            self._sync_address_chrome(push_history=push_history)
            self._select_tree_prefix(self._prefix)
            await self._load_objects()
            return
        prefix = raw.replace(" > ", "/").replace("\\", "/").strip("/")
        for root_name in ("storages/", "this pc/"):
            if prefix.casefold().startswith(root_name):
                prefix = prefix.split("/", 1)[-1]
                break
        if prefix.casefold() in {"storages", "this pc", ""}:
            await self._go_this_pc(push_history=push_history)
            return
        if prefix.casefold().startswith("receipt disk"):
            prefix = prefix[len("receipt disk") :].lstrip("/ ")
        self._at_this_pc = False
        self._prefix = prefix
        if hasattr(self, "_search"):
            self._search.clear()
        self._sync_address_chrome(push_history=push_history)
        self._select_tree_prefix(self._prefix)
        await self._load_objects()

    async def _reload_explorer(self) -> None:
        await self._rebuild_tree()
        if self._at_this_pc:
            await self._load_this_pc()
        else:
            await self._load_objects()

    async def _go_prefix(self, prefix: str) -> None:
        self._at_this_pc = False
        self._prefix = prefix or ""
        if hasattr(self, "_search"):
            self._search.clear()
        self._sync_address_chrome(push_history=True)
        self._select_tree_prefix(self._prefix)
        await self._load_objects()

    async def _go_up(self) -> None:
        if self._at_this_pc:
            return
        if not self._prefix:
            await self._go_this_pc(push_history=True)
            return
        parts = self._prefix.split("/")
        await self._go_prefix("/".join(parts[:-1]))

    async def _rebuild_tree(self) -> None:
        self._tree_loading = True
        try:
            tree = await list_storage_tree("")
        except Exception as exc:
            QMessageBox.critical(self, "Receipt storage", str(exc))
            self._tree_loading = False
            return
        # Build only after await so we never touch items cleared by a concurrent reload.
        self._tree.clear()
        this_pc = _tree_item(
            "Storages", icon=qta.icon("mdi.server", color=_PRIMARY)
        )
        this_pc.setData(0, Qt.UserRole, {"_this_pc": True, "loaded": True})
        disk = _tree_item(
            "Receipt disk", icon=qta.icon("mdi.harddisk", color=_PRIMARY)
        )
        disk.setData(
            0,
            Qt.UserRole,
            {"prefix": "", "loaded": True, "mount_id": "primary"},
        )
        for folder in tree.get("folders") or []:
            child = _tree_item(
                str(folder.get("name") or ""),
                icon=qta.icon("mdi.folder", color=_FOLDER),
            )
            child.setData(
                0,
                Qt.UserRole,
                {"prefix": str(folder.get("prefix") or ""), "loaded": False},
            )
            child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            # Placeholder so expand chevron appears
            child.addChild(_tree_item("…"))
            disk.addChild(child)
        this_pc.addChild(disk)
        self._tree.addTopLevelItem(this_pc)
        this_pc.setExpanded(True)
        disk.setExpanded(True)
        self._tree_loading = False
        if self._at_this_pc:
            self._select_tree_this_pc()
        else:
            self._select_tree_prefix(self._prefix)

    def _select_tree_this_pc(self) -> None:
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            data = item.data(0, Qt.UserRole) or {}
            if data.get("_this_pc"):
                self._tree.setCurrentItem(item)
                return

    def _select_tree_prefix(self, prefix: str) -> None:
        target = prefix or ""

        def walk(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
            data = item.data(0, Qt.UserRole) or {}
            if data.get("_this_pc"):
                # Skip This PC; match Receipt disk / folders only
                pass
            else:
                item_prefix = str(data.get("prefix") or "")
                if item_prefix == target:
                    if target == "" and not data.get("mount_id"):
                        # Avoid accidental match on a non-mount empty prefix
                        pass
                    else:
                        return item
            for i in range(item.childCount()):
                found = walk(item.child(i))
                if found is not None:
                    return found
            return None

        for i in range(self._tree.topLevelItemCount()):
            hit = walk(self._tree.topLevelItem(i))
            if hit is not None:
                self._tree.setCurrentItem(hit)
                return

    def _on_tree_click(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("_this_pc"):
            schedule_coro(self._go_this_pc(push_history=True))
            return
        prefix = str(data.get("prefix") or "")
        schedule_coro(self._go_prefix(prefix))

    def _on_tree_expand(self, item: QTreeWidgetItem) -> None:
        if self._tree_loading:
            return
        data = item.data(0, Qt.UserRole) or {}
        if data.get("loaded"):
            return
        schedule_coro(self._load_tree_children(item))

    async def _load_tree_children(self, item: QTreeWidgetItem) -> None:
        data = dict(item.data(0, Qt.UserRole) or {})
        prefix = str(data.get("prefix") or "")
        try:
            tree = await list_storage_tree(prefix)
        except Exception as exc:
            QMessageBox.critical(self, "Receipt storage", str(exc))
            return
        # Clear placeholder children
        while item.childCount():
            item.removeChild(item.child(0))
        for folder in tree.get("folders") or []:
            child = _tree_item(
                str(folder.get("name") or ""),
                icon=qta.icon("mdi.folder", color=_FOLDER),
            )
            child.setData(
                0,
                Qt.UserRole,
                {"prefix": str(folder.get("prefix") or ""), "loaded": False},
            )
            child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            child.addChild(_tree_item("…"))
            item.addChild(child)
        data["loaded"] = True
        item.setData(0, Qt.UserRole, data)

    async def _load_this_pc(self) -> None:
        self._show_this_pc_view()
        self._sync_address_chrome(push_history=False)
        try:
            status = await get_storage_status()
        except Exception as exc:
            self._drives_page.set_mounts([])
            self._status.setText(f"Storages · could not load mounts: {exc}")
            return
        self._storage_status = status if isinstance(status, dict) else {}
        mounts = mounts_from_storage_status(self._storage_status)
        self._drives_page.set_mounts(mounts)
        if mounts:
            self._drives_page.select_mount_id(str(mounts[0].get("id") or "primary"))
        probe = (
            self._storage_status.get("probe")
            if isinstance(self._storage_status.get("probe"), dict)
            else {}
        )
        free_h = probe.get("free_human") or "—"
        total_h = probe.get("total_human") or "—"
        host = str(self._storage_status.get("host") or "")
        self._status.setText(
            f"Storages · {len(mounts)} mount(s) on server"
            + (f" · {free_h} free of {total_h}" if free_h != "—" else "")
            + (f" · {host}" if host else "")
            + " · double-click a storage to open"
        )
        self._sync_cmd_enabled()

    async def _load_objects(self) -> None:
        self._at_this_pc = False
        self._show_browse_view()
        q = self._search.text().strip()
        try:
            objects = await list_storage_objects(prefix=self._prefix, q=q)
        except Exception as exc:
            QMessageBox.critical(self, "Receipt storage", str(exc))
            return
        self._objects = list(objects.get("items") or [])
        self._sync_address_chrome(push_history=False)
        self._table.setRowCount(0)
        folder_n = 0
        file_n = 0
        cut_ids = self._cut_file_ids()
        for row in self._objects:
            r = self._table.rowCount()
            self._table.insertRow(r)
            is_folder = str(row.get("kind") or "") == "folder"
            fid = str(row.get("file_id") or "").strip()
            is_cut = bool(not is_folder and fid and fid in cut_ids)
            fg = QColor(_CUT_FADE if is_cut else _TEXT)
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsEnabled)
            if is_folder:
                folder_n += 1
                icon = qta.icon("mdi.folder", color=_FOLDER)
                type_s = "Folder"
                status_s = "—"
                folder_size = int(row.get("size") or 0)
                size_s = _fmt_size(folder_size) if folder_size > 0 else "—"
            else:
                file_n += 1
                icon = qta.icon(
                    "mdi.file-document-outline",
                    color=_CUT_FADE if is_cut else _PRIMARY,
                )
                type_s = str(
                    row.get("display_type")
                    or row.get("record_label")
                    or (str(row.get("content_type") or "").split("/")[-1])
                    or "file"
                )
                status_s = str(
                    row.get("display_status")
                    or (
                        "disk only"
                        if not row.get("indexed")
                        else (row.get("inbox_status") or "—")
                    )
                )
                # Never show chase "approved" for Records company paper
                if str(row.get("source") or "").lower() == "records" or row.get(
                    "record_label"
                ):
                    if status_s.lower() in {"approved", "inbox_approved"}:
                        status_s = "Records"
                size_s = _fmt_size(int(row.get("size") or 0))
                check.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
                )
                check.setCheckState(Qt.Unchecked)
            name_item = QTableWidgetItem(icon, str(row.get("name") or ""))
            name_item.setData(Qt.UserRole, row)
            if is_cut:
                name_item.setToolTip("Cut — Paste downloads a copy (Esc cancels)")
            values = [
                check,
                name_item,
                QTableWidgetItem(type_s),
                QTableWidgetItem(status_s),
                QTableWidgetItem(size_s),
                QTableWidgetItem(str(row.get("key") or row.get("prefix") or "")),
            ]
            for c, cell in enumerate(values):
                cell.setData(Qt.UserRole, row)
                cell.setForeground(QBrush(fg))
                self._table.setItem(r, c, cell)
        self._status.setText(
            f"{self._prefix or 'root'} · {folder_n} folder(s) · {file_n} file(s)"
            + (f" · search “{q}”" if q else "")
        )
        self._sync_cmd_enabled()

    def _set_file_checks(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self._table.rowCount()):
            cell = self._table.item(r, 0)
            if cell is None:
                continue
            if cell.flags() & Qt.ItemIsUserCheckable:
                cell.setCheckState(state)

    def _checked_file_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for r in range(self._table.rowCount()):
            cell = self._table.item(r, 0)
            if cell is None or not (cell.flags() & Qt.ItemIsUserCheckable):
                continue
            if cell.checkState() != Qt.Checked:
                continue
            row = cell.data(Qt.UserRole) or {}
            if not isinstance(row, dict):
                continue
            if str(row.get("kind") or "") == "folder":
                continue
            key = str(row.get("file_id") or row.get("key") or id(row))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        return rows

    def _selected_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in self._table.selectedItems():
            row = item.data(Qt.UserRole) or {}
            if not isinstance(row, dict):
                continue
            key = str(row.get("file_id") or row.get("key") or row.get("prefix") or id(row))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        return rows

    def _on_content_double_clicked(self, row: int, _col: int = 0) -> None:
        item = self._table.item(row, 0)
        data = (item.data(Qt.UserRole) if item else None) or {}
        if not isinstance(data, dict) or not data:
            rows = self._selected_rows()
            if not rows:
                return
            data = rows[0]
        if str(data.get("kind") or "") == "folder":
            prefix = str(data.get("prefix") or data.get("key") or "")
            schedule_coro(self._go_prefix(prefix))
            return
        fid = str(data.get("file_id") or "").strip()
        if not fid:
            QMessageBox.information(
                self,
                "Receipt storage",
                "This disk object has no receipt_files id, so it cannot open in the viewer.",
            )
            return
        open_receipt_viewer(self, fid, title_hint=str(data.get("name") or ""))

    def _view_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Receipt storage", "Select a file first.")
            return
        row = rows[0]
        if str(row.get("kind") or "") == "folder":
            prefix = str(row.get("prefix") or row.get("key") or "")
            schedule_coro(self._go_prefix(prefix))
            return
        fid = str(row.get("file_id") or "").strip()
        if not fid:
            QMessageBox.information(
                self,
                "Receipt storage",
                "This disk object has no receipt_files id, so it cannot open in the viewer.",
            )
            return
        open_receipt_viewer(self, fid, title_hint=str(row.get("name") or ""))

    def _table_context_menu(self, pos) -> None:
        rows = self._selected_rows()
        # Ensure right-click target is selected
        index = self._table.indexAt(pos)
        if index.isValid():
            item = self._table.item(index.row(), 0)
            data = (item.data(Qt.UserRole) if item else None) or {}
            if isinstance(data, dict) and data not in rows:
                self._table.selectRow(index.row())
                rows = self._selected_rows()
        menu = apply_light_menu(QMenu(self))
        open_act = menu.addAction("Open")
        open_act.setIcon(qta.icon("mdi.folder-open-outline", color=_TEXT))
        menu.addSeparator()
        new_act = menu.addAction("New folder…")
        new_act.setIcon(qta.icon("mdi.folder-plus-outline", color=_TEXT))
        new_act.setEnabled(bool(self._prefix))
        menu.addSeparator()
        dl_act = menu.addAction("Download")
        dl_act.setIcon(qta.icon("mdi.download-outline", color=_TEXT))
        remat_act = menu.addAction("Rematerialize")
        remat_act.setIcon(qta.icon("mdi.folder-sync-outline", color=_TEXT))
        trash_act = menu.addAction("Move to Trash")
        trash_act.setIcon(qta.icon("mdi.delete-outline", color=_TEXT))
        del_folder_act = menu.addAction("Delete empty folder")
        del_folder_act.setIcon(qta.icon("mdi.folder-remove-outline", color=_TEXT))

        only_folders = bool(rows) and all(
            str(r.get("kind") or "") == "folder" for r in rows
        )
        has_files = any(str(r.get("kind") or "") != "folder" for r in rows)
        has_indexed = any(
            str(r.get("file_id") or "").strip() and str(r.get("kind") or "") != "folder"
            for r in rows
        )
        dl_act.setEnabled(has_indexed)
        remat_act.setEnabled(has_indexed)
        trash_act.setEnabled(has_indexed)
        del_folder_act.setEnabled(only_folders and len(rows) == 1)
        open_act.setEnabled(bool(rows))

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == open_act:
            self._view_selected()
        elif chosen == new_act:
            schedule_coro(self._new_folder())
        elif chosen == dl_act:
            schedule_coro(self._download_selected())
        elif chosen == remat_act:
            schedule_coro(self._rematerialize_selected())
        elif chosen == trash_act:
            schedule_coro(self._trash_selected())
        elif chosen == del_folder_act and rows:
            schedule_coro(
                self._delete_empty_folder(
                    str(rows[0].get("prefix") or rows[0].get("key") or "")
                )
            )

    def _tree_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        QToolTip.hideText()
        self._tree.setCurrentItem(item)
        data = item.data(0, Qt.UserRole) or {}
        prefix = str(data.get("prefix") or "")
        roots = {"", "files", "approved", "quarantine", "parked", "records"}
        can_mutate = bool(prefix) and prefix not in roots

        menu = apply_light_menu(QMenu(self))
        open_act = menu.addAction("Open")
        open_act.setIcon(qta.icon("mdi.folder-open-outline", color=_TEXT))
        refresh_act = menu.addAction("Refresh")
        refresh_act.setIcon(qta.icon("mdi.refresh", color=_TEXT))
        menu.addSeparator()
        new_act = menu.addAction("New folder…")
        new_act.setIcon(qta.icon("mdi.folder-plus-outline", color=_TEXT))
        new_act.setEnabled(bool(prefix))
        rename_act = menu.addAction("Rename…")
        rename_act.setIcon(qta.icon("mdi.rename-box", color=_TEXT))
        rename_act.setEnabled(can_mutate)
        menu.addSeparator()
        del_act = menu.addAction("Delete…")
        del_act.setIcon(qta.icon("mdi.folder-remove-outline", color=_TEXT))
        del_act.setEnabled(can_mutate)
        chosen = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == open_act:
            schedule_coro(self._go_prefix(prefix))
        elif chosen == refresh_act:
            schedule_coro(self._reload_explorer())
        elif chosen == new_act:
            schedule_coro(self._go_prefix(prefix))
            schedule_coro(self._new_folder_under(prefix))
        elif chosen == rename_act:
            schedule_coro(self._rename_folder_prefix(prefix))
        elif chosen == del_act:
            schedule_coro(self._delete_empty_folder(prefix))

    async def _new_folder(self) -> None:
        await self._new_folder_under(self._prefix)

    async def _new_folder_under(self, prefix: str) -> None:
        if not prefix:
            QMessageBox.information(
                self,
                "New folder",
                "Open files, approved, records, etc. first — then create a subfolder.",
            )
            return
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if not ok or not str(name).strip():
            return
        try:
            created = await create_storage_folder(prefix=prefix, name=str(name).strip())
            await self._reload_explorer()
            await self._go_prefix(str(created.get("prefix") or prefix))
            self._status.setText(f"Created folder “{created.get('name')}”")
        except Exception as exc:
            QMessageBox.warning(self, "New folder", str(exc))

    async def _delete_empty_folder(self, prefix: str) -> None:
        """Delete a storage folder; cascade moves indexed files to Trash first."""
        if not prefix:
            return
        roots = {"files", "approved", "quarantine", "parked", "records"}
        if prefix in roots:
            QMessageBox.information(
                self, "Delete folder", "Workspace root folders cannot be deleted."
            )
            return
        if not ask_confirm(
            self,
            title="Delete folder",
            heading=f"Delete folder “{prefix}”?",
            body=(
                "Indexed files inside are moved to Trash (restorable). "
                "The folder is then removed from disk."
            ),
            accept_label="Delete",
            destructive=True,
        ):
            return
        try:
            result = await delete_storage_folder(prefix=prefix, cascade=True)
            parent = "/".join(prefix.split("/")[:-1])
            await self._reload_explorer()
            await self._go_prefix(parent)
            n = int(result.get("trashed_files") or 0)
            self._status.setText(
                f"Deleted folder “{prefix}” · {n} file(s) moved to Trash"
            )
        except Exception as exc:
            QMessageBox.warning(self, "Delete folder", str(exc))

    def _clip_rows_payload(self) -> list[dict[str, str]]:
        rows = self._rows_for_file_action()
        out: list[dict[str, str]] = []
        for r in rows:
            fid = str(r.get("file_id") or "").strip()
            if not fid:
                continue
            out.append({"file_id": fid, "name": str(r.get("name") or fid)})
        return out

    def _cut_file_ids(self) -> set[str]:
        clip = self._clip
        if not clip or clip.get("mode") != "cut":
            return set()
        return {
            str(f.get("file_id") or "").strip()
            for f in (clip.get("files") or [])
            if str(f.get("file_id") or "").strip()
        }

    def _apply_cut_fade(self) -> None:
        """Repaint Explorer rows so cut files look faded like Windows."""
        cut_ids = self._cut_file_ids()
        for r in range(self._table.rowCount()):
            cell0 = self._table.item(r, 0)
            row = (cell0.data(Qt.UserRole) if cell0 else None) or {}
            if not isinstance(row, dict):
                continue
            if str(row.get("kind") or "") == "folder":
                continue
            fid = str(row.get("file_id") or "").strip()
            is_cut = bool(fid and fid in cut_ids)
            fg = QBrush(QColor(_CUT_FADE if is_cut else _TEXT))
            name_cell = self._table.item(r, 1)
            if name_cell is not None:
                name_cell.setIcon(
                    qta.icon(
                        "mdi.file-document-outline",
                        color=_CUT_FADE if is_cut else _PRIMARY,
                    )
                )
                name_cell.setToolTip(
                    "Cut — Paste downloads a copy (Esc cancels)" if is_cut else ""
                )
            for c in range(self._table.columnCount()):
                cell = self._table.item(r, c)
                if cell is not None:
                    cell.setForeground(fg)

    def _clip_cut(self) -> None:
        files = self._clip_rows_payload()
        if not files:
            QMessageBox.information(self, "Cut", "Select or check indexed file(s) first.")
            return
        self._clip = {"mode": "cut", "files": files}
        self._status.setText(
            f"Cut {len(files)} file(s) — Paste downloads, then Trash (Esc cancels)"
        )
        self._apply_cut_fade()
        self._sync_cmd_enabled()

    def _clip_copy(self) -> None:
        files = self._clip_rows_payload()
        if not files:
            QMessageBox.information(self, "Copy", "Select or check indexed file(s) first.")
            return
        self._clip = {"mode": "copy", "files": files}
        self._status.setText(f"Copied {len(files)} file(s) — Paste to download")
        self._apply_cut_fade()
        self._sync_cmd_enabled()

    def _clear_clip(self) -> None:
        if not self._clip:
            return
        self._clip = None
        self._apply_cut_fade()
        self._sync_cmd_enabled()
        self._status.setText("Clipboard cleared")

    async def _clip_paste(self) -> None:
        clip = self._clip
        if not clip or not clip.get("files"):
            QMessageBox.information(self, "Paste", "Clipboard is empty.")
            return
        # Paste = download clipboard files; Cut also moves them to Trash after.
        fake_rows = [
            {"file_id": f["file_id"], "name": f.get("name") or f["file_id"]}
            for f in clip["files"]
        ]
        from tahmeed.ui.file_dialogs import get_existing_directory

        dest = await await_blocking(
            lambda: get_existing_directory(
                self, "Paste / save clipboard files to folder"
            )
        )
        if not dest:
            return
        out_dir = Path(dest) / "tahmeed-pasted-receipts"
        out_dir.mkdir(parents=True, exist_ok=True)
        ok = 0
        failed = 0
        for row in fake_rows:
            fid = str(row.get("file_id") or "")
            name = str(row.get("name") or f"{fid}.bin")
            safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:100]
            try:
                payload, _meta = await fetch_receipt_file_bytes(fid)
                (out_dir / f"{fid[:8]}_{safe}").write_bytes(payload)
                ok += 1
            except Exception:
                failed += 1
        if clip.get("mode") == "cut" and ok:
            ids = [str(f["file_id"]) for f in clip["files"]]
            if not ask_confirm(
                self,
                title="Move to Trash",
                heading=f"Move {len(ids)} cut file(s) to Trash?",
                body=(
                    f"Downloaded to “{out_dir.name}”. "
                    "Confirm to remove the originals from Receipt storage "
                    "(recoverable from Paperwork → Trash)."
                ),
                accept_label="Move to Trash",
                destructive=True,
            ):
                self._status.setText(
                    f"Pasted {ok} file(s) — originals left in place (Cut cancelled)"
                )
                self._sync_cmd_enabled()
                return
            try:
                await trash_receipt_files(ids)
                self._clip = None
                await self._load_objects()
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Cut incomplete",
                    f"Downloaded {ok} file(s), but Trash failed:\n{exc}",
                )
        self._status.setText(
            f"Pasted {ok} file(s)"
            + (f", {failed} failed" if failed else "")
            + (" · moved to Trash" if clip.get("mode") == "cut" and ok else "")
        )
        self._sync_cmd_enabled()

    async def _rename_selected_folder(self) -> None:
        rows = self._selected_rows()
        prefix = ""
        if rows and str(rows[0].get("kind") or "") == "folder":
            prefix = str(rows[0].get("prefix") or rows[0].get("key") or "")
        if not prefix:
            # Fall back to current tree location if it's a renameable folder
            prefix = self._prefix
        await self._rename_folder_prefix(prefix)

    async def _rename_folder_prefix(self, prefix: str) -> None:
        roots = {"", "files", "approved", "quarantine", "parked", "records"}
        if not prefix or prefix in roots:
            QMessageBox.information(
                self,
                "Rename",
                "Select a subfolder to rename (not a workspace root).",
            )
            return
        current = prefix.split("/")[-1]
        name, ok = QInputDialog.getText(
            self, "Rename folder", "New name:", text=current
        )
        if not ok or not str(name).strip() or str(name).strip() == current:
            return
        try:
            result = await rename_storage_folder(
                prefix=prefix, name=str(name).strip()
            )
            await self._reload_explorer()
            await self._go_prefix(str(result.get("prefix") or prefix))
            self._status.setText(f"Renamed to “{result.get('name')}”")
        except Exception as exc:
            QMessageBox.warning(self, "Rename failed", str(exc))

    async def _toolbar_delete(self) -> None:
        rows = self._selected_rows()
        folders = [
            r for r in rows if str(r.get("kind") or "") == "folder"
        ]
        files = self._rows_for_file_action()
        if folders and not files:
            await self._delete_empty_folder(
                str(folders[0].get("prefix") or folders[0].get("key") or "")
            )
            return
        if files:
            await self._trash_selected()
            return
        if self._prefix and self._prefix not in {
            "files",
            "approved",
            "quarantine",
            "parked",
            "records",
        }:
            await self._delete_empty_folder(self._prefix)
            return
        QMessageBox.information(
            self, "Delete", "Select a folder or indexed file(s) first."
        )

    def _sync_cmd_enabled(self) -> None:
        if not hasattr(self, "_cmd"):
            return
        if getattr(self, "_at_this_pc", False):
            for key in (
                "cut",
                "copy",
                "paste",
                "download",
                "delete",
                "new",
                "rename",
                "check_all",
                "clear_checks",
            ):
                self._cmd.set_enabled(key, False)
            return
        has_files = bool(self._rows_for_file_action())
        has_clip = bool(self._clip and self._clip.get("files"))
        self._cmd.set_enabled("cut", True)
        self._cmd.set_enabled("copy", True)
        self._cmd.set_enabled("paste", has_clip)
        self._cmd.set_enabled("download", True)
        self._cmd.set_enabled("delete", True)
        self._cmd.set_enabled("new", bool(self._prefix))
        self._cmd.set_enabled("rename", True)
        self._cmd.set_enabled("check_all", True)
        self._cmd.set_enabled("clear_checks", True)
        _ = has_files  # selection checked at click time

    async def _trash_selected(self) -> None:
        ids = [
            str(r.get("file_id") or "").strip()
            for r in self._rows_for_file_action()
            if r.get("file_id")
        ]
        if not ids:
            QMessageBox.information(
                self,
                "Trash",
                "Select indexed file(s) to move to Trash.",
            )
            return
        if not ask_confirm(
            self,
            title="Move to Trash",
            heading=f"Move {len(ids)} file(s) to Trash?",
            body="They leave browse until restored from Paperwork → Trash.",
            accept_label="Move to Trash",
            destructive=True,
        ):
            return
        try:
            result = await trash_receipt_files(ids)
            n = int(result.get("trashed") or 0)
            self._status.setText(f"Moved {n} file(s) to Trash")
            await self._load_objects()
        except Exception as exc:
            QMessageBox.critical(self, "Trash failed", str(exc))

    async def _rematerialize_selected(self) -> None:
        ids = [
            str(r.get("file_id") or "").strip()
            for r in self._rows_for_file_action()
            if r.get("file_id")
        ]
        if not ids:
            QMessageBox.information(
                self,
                "Receipt storage",
                "Select one or more indexed files to rematerialize.",
            )
            return
        try:
            result = await rematerialize_files(file_ids=ids)
            count = int(result.get("materialized") or 0)
            self._status.setText(f"Rematerialized {count} file set(s)")
            await self._load_objects()
            QMessageBox.information(
                self,
                "Rematerialize",
                f"Refreshed approved/ links.\nMaterialized sets: {count}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Rematerialize failed", str(exc))

    def _rows_for_file_action(self) -> list[dict[str, Any]]:
        """Prefer checkbox selection; fall back to highlighted rows."""
        checked = [r for r in self._checked_file_rows() if r.get("file_id")]
        if checked:
            return checked
        return [
            r
            for r in self._selected_rows()
            if str(r.get("kind") or "") != "folder" and r.get("file_id")
        ]

    async def _download_selected(self) -> None:
        rows = self._rows_for_file_action()
        if not rows:
            QMessageBox.information(
                self,
                "Download",
                "Check one or more indexed files (or select rows), then download.",
            )
            return
        from tahmeed.ui.file_dialogs import get_existing_directory

        dest = await await_blocking(
            lambda: get_existing_directory(
                self, "Save selected receipts to folder"
            )
        )
        if not dest:
            return
        out_dir = Path(dest) / "tahmeed-selected-receipts"
        out_dir.mkdir(parents=True, exist_ok=True)
        ok = 0
        failed = 0
        for row in rows:
            fid = str(row.get("file_id") or "")
            name = str(row.get("name") or f"{fid}.bin")
            safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:100]
            try:
                payload, _meta = await fetch_receipt_file_bytes(fid)
                (out_dir / f"{fid[:8]}_{safe}").write_bytes(payload)
                ok += 1
            except Exception:
                failed += 1
        self._status.setText(f"Downloaded {ok} file(s)" + (f", {failed} failed" if failed else ""))
        QMessageBox.information(
            self,
            "Download selected",
            f"Saved {ok} file(s) to:\n{out_dir}"
            + (f"\nFailed: {failed}" if failed else ""),
        )

    # ── Pack tab actions ──────────────────────────────────────────────────

    async def _scan_days(self) -> None:
        d0 = self._pack_from.date().toPython()
        d1 = self._pack_to.date().toPython()
        try:
            rows = await list_receipt_library(
                date_from=d0.isoformat(),
                date_to=d1.isoformat(),
                item=self._pack_item.text().strip(),
                payee=self._pack_payee.text().strip(),
                ownership=self._pack_own.text().strip(),
                status="approved" if self._pack_approved.isChecked() else "all",
                limit=500,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Scan days", str(exc))
            return
        by_day: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"files": 0, "approved": 0, "items": set()}
        )
        for row in rows:
            raw = row.get("uploaded_at")
            if hasattr(raw, "date"):
                day = raw.date().isoformat()
            else:
                day = str(raw or "")[:10] or "unknown"
            bucket = by_day[day]
            bucket["files"] += 1
            if str(row.get("inbox_status") or "") == "approved":
                bucket["approved"] += 1
            item = str(row.get("item") or "").strip()
            if item:
                bucket["items"].add(item.split(",")[0].strip())
        self._day_rows = []
        self._days.setRowCount(0)
        for day in sorted(by_day.keys(), reverse=True):
            info = by_day[day]
            sample = ", ".join(sorted(info["items"])[:3])
            rec = {
                "day": day,
                "files": info["files"],
                "approved": info["approved"],
                "items": sample,
            }
            self._day_rows.append(rec)
            r = self._days.rowCount()
            self._days.insertRow(r)
            for c, value in enumerate(
                [day, str(info["files"]), str(info["approved"]), sample]
            ):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, rec)
                cell.setForeground(QBrush(QColor(_TEXT)))
                self._days.setItem(r, c, cell)
        self._status.setText(
            f"Scan: {len(rows)} file(s) across {len(self._day_rows)} day(s)"
        )

    def _selected_days(self) -> list[str]:
        days: list[str] = []
        seen: set[str] = set()
        for item in self._days.selectedItems():
            row = item.data(Qt.UserRole) or {}
            day = str(row.get("day") or "")
            if day and day not in seen and day != "unknown":
                seen.add(day)
                days.append(day)
        return days

    async def _pack_download(self, *, to_folder: bool) -> None:
        d0 = self._pack_from.date().toPython()
        d1 = self._pack_to.date().toPython()
        if d1 < d0:
            QMessageBox.information(self, "Pack", "End date must be on or after start.")
            return
        await self._run_pack(
            date_from=d0,
            date_to=d1,
            to_folder=to_folder,
        )

    async def _pack_selected_days(self) -> None:
        days = self._selected_days()
        if not days:
            QMessageBox.information(
                self,
                "Pack selected days",
                "Scan the range first, then select one or more day rows.",
            )
            return
        # Pack each selected calendar day (transaction-date filter = that day)
        combined = io.BytesIO()
        total_files = 0
        with zipfile.ZipFile(combined, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for day in days:
                try:
                    payload, meta = await download_consolidate_pack(
                        date_from=day,
                        date_to=day,
                        item=self._pack_item.text().strip(),
                        payee=self._pack_payee.text().strip(),
                        ownership=self._pack_own.text().strip(),
                        approved_only=self._pack_approved.isChecked(),
                    )
                except Exception as exc:
                    QMessageBox.warning(
                        self, "Pack selected days", f"{day} failed:\n{exc}"
                    )
                    continue
                total_files += int(meta.get("files") or 0) if str(meta.get("files") or "").isdigit() else 0
                with zipfile.ZipFile(io.BytesIO(payload), "r") as zin:
                    for info in zin.infolist():
                        if info.is_dir():
                            continue
                        name = info.filename
                        if name.endswith("SUMMARY.txt"):
                            name = f"{day}/SUMMARY.txt"
                        else:
                            name = f"{day}/{name}"
                        zout.writestr(name, zin.read(info))
        data = combined.getvalue()
        if not data:
            QMessageBox.information(self, "Pack selected days", "Nothing packed.")
            return
        suggested = (
            f"tahmeed-days_{days[-1]}_{days[0]}.zip"
            if len(days) > 1
            else f"tahmeed-day_{days[0]}.zip"
        )
        from tahmeed.ui.file_dialogs import get_save_file_name

        path, _ = await await_blocking(
            lambda: get_save_file_name(
                self,
                "Save multi-day pack",
                suggested,
                "ZIP files (*.zip)",
            )
        )
        if not path:
            return
        out = Path(path)
        if out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        out.write_bytes(data)
        self._status.setText(f"Saved multi-day pack · {len(days)} day(s)")
        await await_blocking(
            lambda: QMessageBox.information(
                self,
                "Pack selected days",
                f"Saved {len(days)} day folder(s) to:\n{out}\nFiles reported: {total_files}",
            )
        )

    async def _run_pack(
        self, *, date_from: date, date_to: date, to_folder: bool
    ) -> None:
        try:
            payload, meta = await download_consolidate_pack(
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
                item=self._pack_item.text().strip(),
                payee=self._pack_payee.text().strip(),
                ownership=self._pack_own.text().strip(),
                approved_only=self._pack_approved.isChecked(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Pack failed", str(exc))
            return
        default_name = (
            meta.get("filename")
            or f"tahmeed-receipts_{date_from.isoformat()}_{date_to.isoformat()}.zip"
        )
        summary = (
            f"Rows: {meta.get('rows') or '?'}\n"
            f"With file: {meta.get('with_file') or '?'}\n"
            f"Files: {meta.get('files') or '?'}\n"
            f"Missing: {meta.get('missing') or '0'}\n"
            f"SHA-256: {meta.get('sha256') or ''}"
        )
        from tahmeed.ui.file_dialogs import get_existing_directory, get_save_file_name

        if to_folder:
            dest = await await_blocking(
                lambda: get_existing_directory(self, "Save pack to folder")
            )
            if not dest:
                return
            out_dir = Path(dest) / Path(default_name).stem
            out_dir.mkdir(parents=True, exist_ok=True)
            zip_path = out_dir / default_name
            zip_path.write_bytes(payload)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(out_dir)
            self._status.setText(f"Packed to {out_dir}")
            await await_blocking(
                lambda: QMessageBox.information(
                    self, "Pack", f"Saved and extracted to:\n{out_dir}\n\n{summary}"
                )
            )
            return
        path, _ = await await_blocking(
            lambda: get_save_file_name(
                self, "Save pack ZIP", default_name, "ZIP files (*.zip)"
            )
        )
        if not path:
            return
        out = Path(path)
        if out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        out.write_bytes(payload)
        self._status.setText(f"Saved {out.name}")
        await await_blocking(
            lambda: QMessageBox.information(
                self, "Pack", f"Saved to:\n{out}\n\n{summary}"
            )
        )
