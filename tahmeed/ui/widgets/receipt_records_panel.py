"""Paperwork → Records — company paper browse, custom folders, multi-upload.

No expense row required. Destinations: Item / Supplier / Separate / Custom.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import qtawesome as qta
from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
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
    move_record_files,
    rename_custom_folder,
    upload_record_files,
)
from tahmeed.services.receipt_storage_service import trash_receipt_files
from tahmeed.ui.accountant.date_filters import (
    style_calendar_popup,
    sync_calendar_page_to_date,
)
from tahmeed.ui.async_utils import await_blocking, schedule_coro
from tahmeed.ui.dialogs.confirm_dialog import ask_confirm
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading
from tahmeed.ui.widgets.popup_styles import apply_light_menu, with_light_popups
from tahmeed.ui.widgets.receipt_file_viewer import open_receipt_viewer
from tahmeed.ui.widgets.upload_busy import UploadBusy, UploadCancelled
from tahmeed.ui.widgets.windows_command_bar import WindowsExplorerChrome

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
_CUT_FADE = "#9CA3AF"

_PAGE_SS = f"QWidget {{ background: {_BG}; }}"
_TABLE_SS = with_light_popups(f"""
QTableWidget {{
  background:{_CARD}; border:1px solid {_BORDER}; border-radius:6px;
  gridline-color:#F3F4F6; font-family:\"Segoe UI\"; font-size:12px; color:{_TEXT};
  selection-background-color:#E0F2FE; selection-color:{_TEXT};
  alternate-background-color:{_STRIPE};
}}
QHeaderView::section {{
  background:#F8FAFC; color:{_MUTED}; font-family:\"Segoe UI\"; font-size:11px; font-weight:600;
  border:none; border-bottom:1px solid {_BORDER}; border-right:1px solid #F3F4F6;
  padding:6px 8px;
}}
""")
_FOLDER = "#D97706"
# Kind roots — Windows Explorer nav-pane feel (library / drive icons)
_KIND_ICONS: dict[str, tuple[str, str]] = {
    "custom": ("mdi.folder-star", _FOLDER),
    "item": ("mdi.package-variant-closed", _ACCENT),
    "supplier": ("mdi.truck-outline", "#0F766E"),
    "separate": ("mdi.file-cabinet", "#7C3AED"),
}


def _tree_stylesheet() -> str:
    """Windows Explorer–style nav tree; chevrons from cached qtawesome PNGs."""
    from PySide6.QtWidgets import QApplication

    closed_url = ""
    opened_url = ""
    if QApplication.instance() is not None:
        cache = Path(__file__).resolve().parents[2] / ".cache" / "tree_branch"
        cache.mkdir(parents=True, exist_ok=True)
        closed = cache / "chevron_right.png"
        opened = cache / "chevron_down.png"
        if not closed.exists():
            qta.icon("mdi.chevron-right", color=_MUTED).pixmap(12, 12).save(str(closed))
        if not opened.exists():
            qta.icon("mdi.chevron-down", color=_MUTED).pixmap(12, 12).save(str(opened))
        closed_url = closed.as_posix()
        opened_url = opened.as_posix()

    branch_closed = (
        f'image: url("{closed_url}");' if closed_url else "image: none;"
    )
    branch_open = (
        f'image: url("{opened_url}");' if opened_url else "image: none;"
    )
    # show-decoration-selected:0 + transparent branches kill the Win red/black
    # selection strip that paints in the branch column.
    return with_light_popups(f"""
QTreeWidget {{
  background:{_CARD}; border:1px solid {_BORDER}; border-radius:6px;
  font-size:12px; font-family:\"Segoe UI\"; color:{_TEXT}; outline:none;
  padding:4px 2px;
  show-decoration-selected: 0;
}}
QTreeWidget::item {{
  padding:3px 6px; border-radius:4px; min-height:24px;
  border: none; outline: none;
}}
QTreeWidget::item:selected,
QTreeWidget::item:selected:active,
QTreeWidget::item:selected:!active {{
  background:#E0F2FE; color:{_TEXT};
  border: none; outline: none;
}}
QTreeWidget::item:hover:!selected {{
  background:#F3F4F6;
}}
QTreeWidget::branch {{
  background: transparent; border: none; border-image: none;
}}
QTreeWidget::branch:selected,
QTreeWidget::branch:selected:active {{
  background: transparent; border: none;
}}
QTreeWidget::branch:has-siblings:!adjoins-item,
QTreeWidget::branch:!has-children:!has-siblings:adjoins-item,
QTreeWidget::branch:!has-children:has-siblings:adjoins-item {{
  border-image: none; image: none; background: transparent;
}}
QTreeWidget::branch:has-children:!has-siblings:closed,
QTreeWidget::branch:closed:has-children:has-siblings {{
  border-image: none; background: transparent;
  {branch_closed}
}}
QTreeWidget::branch:open:has-children:!has-siblings,
QTreeWidget::branch:open:has-children:has-siblings {{
  border-image: none; background: transparent;
  {branch_open}
}}
""")
_BTN_GHOST = with_light_popups(f"""
QPushButton {{
  background:{_CARD}; color:{_TEXT}; border:1px solid {_BORDER};
  border-radius:4px; padding:5px 10px; font-weight:600; font-size:12px; font-family:\"Segoe UI\";
}}
QPushButton:hover {{ background:#F8FAFC; }}
QPushButton:disabled {{ color:#9CA3AF; }}
""")
_BTN_PRIMARY = with_light_popups(f"""
QPushButton {{
  background:{_ACCENT}; color:#FFFFFF; border:none;
  border-radius:4px; padding:5px 12px; font-weight:600; font-size:12px; font-family:\"Segoe UI\";
}}
QPushButton:hover {{ background:#005EA3; }}
QPushButton:disabled {{ background:#93C5FD; }}
""")
_CHIP_BASE = """
QPushButton {{
  background:{bg}; color:{fg}; border:1px solid {border};
  border-radius:4px; padding:4px 10px; font-weight:600; font-size:11px; font-family:\"Segoe UI\";
}}
QPushButton:hover {{ background:{hover}; }}
"""
_INPUT_SS = (
    "QLineEdit,QDateEdit{"
    f"font-size:12px; font-family:\"Segoe UI\";color:{_TEXT};"
    f"border:1px solid {_BORDER};border-radius:4px;padding:4px 8px;"
    "background:#FFFFFF;min-height:26px;}"
)

_KINDS: tuple[tuple[str, str, str], ...] = (
    ("custom", "Custom", "mdi.folder-star"),
    ("item", "Items", "mdi.package-variant-closed"),
    ("supplier", "Suppliers", "mdi.truck-outline"),
    ("separate", "Separate", "mdi.file-cabinet"),
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


def _file_month_key(row: dict[str, Any]) -> str:
    """YYYY-MM from record.file_date, else uploaded_at."""
    rec = row.get("record") if isinstance(row.get("record"), dict) else {}
    filed = str((rec or {}).get("file_date") or "").strip()
    if len(filed) >= 7 and filed[4] == "-":
        return filed[:7]
    raw = row.get("uploaded_at")
    if isinstance(raw, datetime):
        return raw.astimezone().strftime("%Y-%m")
    text = str(raw or "").strip()
    if len(text) >= 7 and text[4] == "-":
        return text[:7]
    return ""


def _month_folder_label(month_key: str) -> str:
    try:
        y, m = month_key.split("-", 1)
        return datetime(int(y), int(m), 1).strftime("%B %Y")
    except (TypeError, ValueError):
        return month_key


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
        self._month_key: str | None = None  # "YYYY-MM" when drilled into a filing month
        self._files: list[dict[str, Any]] = []
        self._content_rows: list[dict[str, Any]] = []  # folders + files for Explorer pane
        self._loading = False
        self._kind_roots: dict[str, QTreeWidgetItem] = {}
        self._expanded_kinds: set[str] = {"custom"}
        self._clip: dict[str, Any] | None = None
        self._build()
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._clear_clip)
        schedule_coro(self.refresh())

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._chrome = WindowsExplorerChrome(
            search_placeholder="Search Records",
            address="Paperwork > Records",
        )
        self._chrome.set_address_icon("mdi.folder-multiple-outline")
        self._chrome.back_clicked.connect(self._nav_back)
        self._chrome.forward_clicked.connect(self._nav_forward)
        self._chrome.up_clicked.connect(self._go_up)
        self._chrome.refresh_clicked.connect(
            lambda: schedule_coro(self.refresh())
        )
        self._chrome.search_submitted.connect(
            lambda _q: schedule_coro(self._load_files())
        )
        self._search = self._chrome.search

        self._cmd = self._chrome.ribbon
        self._cmd.add_button(
            "new", "mdi.plus-circle-outline", "New custom folder",
            lambda: schedule_coro(self._new_custom()),
        )
        self._cmd.add_button(
            "sub", "mdi.folder-pound-outline", "New subfolder",
            lambda: schedule_coro(self._new_custom_subfolder()),
        )
        self._cmd.add_sep()
        self._cmd.add_button(
            "cut", "mdi.content-cut", "Cut", self._clip_cut
        )
        self._cmd.add_button(
            "copy", "mdi.content-copy", "Copy", self._clip_copy
        )
        self._cmd.add_button(
            "paste", "mdi.content-paste", "Paste into current folder",
            lambda: schedule_coro(self._clip_paste()),
        )
        self._cmd.add_button(
            "rename", "mdi.rename-box", "Rename custom folder",
            lambda: schedule_coro(self._rename_custom()),
        )
        self._cmd.add_button(
            "upload", "mdi.file-upload-outline", "Upload…",
            lambda: schedule_coro(self._upload()),
            color=_ACCENT,
        )
        self._cmd.add_button(
            "view", "mdi.eye-outline", "View", self._view_selected
        )
        self._cmd.add_button(
            "download", "mdi.zip-box-outline", "Download ZIP",
            lambda: schedule_coro(self._download_pack()),
        )
        self._cmd.add_button(
            "delete", "mdi.trash-can-outline", "Delete / Move to Trash",
            lambda: schedule_coro(self._toolbar_delete()),
            color="#B91C1C",
        )
        self._cmd.add_sep()
        # Compact date filters sit on the ribbon (same height as icon buttons)
        _RIBBON_DATE = (
            "QDateEdit{"
            f"font-size:11px; font-family:\"Segoe UI\";color:{_TEXT};"
            f"border:1px solid {_BORDER};border-radius:4px;padding:1px 4px;"
            "background:#FFFFFF;min-height:24px;max-height:26px;min-width:96px;}"
        )
        self._cmd.add_label("File as")
        self._file_as = QDateEdit()
        self._file_as.setCalendarPopup(True)
        self._file_as.setDisplayFormat("yyyy-MM-dd")
        self._file_as.setDate(QDate.currentDate())
        self._file_as.setFixedHeight(26)
        self._file_as.setStyleSheet(_RIBBON_DATE)
        self._file_as.setToolTip(
            "Filing month for Upload — controls records/…/YYYY/MM/ on the server.\n"
            "Changing the calendar month updates this date (click a day or flip months)."
        )
        style_calendar_popup(self._file_as)
        sync_calendar_page_to_date(self._file_as)
        self._cmd.add_widget(self._file_as)
        self._cmd.add_label("From")
        self._date_from = QDateEdit()
        self._date_from.setCalendarPopup(True)
        self._date_from.setDisplayFormat("yyyy-MM-dd")
        self._date_from.setDate(QDate.currentDate().addYears(-1))
        self._date_from.setFixedHeight(26)
        self._date_from.setStyleSheet(_RIBBON_DATE)
        style_calendar_popup(self._date_from)
        self._date_from.dateChanged.connect(
            lambda *_: schedule_coro(self._load_files())
        )
        self._cmd.add_widget(self._date_from)
        self._cmd.add_label("To")
        self._date_to = QDateEdit()
        self._date_to.setCalendarPopup(True)
        self._date_to.setDisplayFormat("yyyy-MM-dd")
        self._date_to.setDate(QDate.currentDate())
        self._date_to.setFixedHeight(26)
        self._date_to.setStyleSheet(_RIBBON_DATE)
        style_calendar_popup(self._date_to)
        self._date_to.dateChanged.connect(
            lambda *_: schedule_coro(self._load_files())
        )
        self._cmd.add_widget(self._date_to)
        self._cmd.add_stretch()
        self._upload_btn = self._cmd.buttons["upload"]
        self._up_btn = self._chrome.btn_up
        self._new_btn = self._cmd.buttons["new"]
        self._sub_btn = self._cmd.buttons["sub"]
        self._rename_btn = self._cmd.buttons["rename"]
        self._delete_btn = self._cmd.buttons["delete"]
        root.addWidget(self._chrome)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_rail())
        split.addWidget(self._build_files_pane())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 720])
        root.addWidget(split, 1)

        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color:{_MUTED}; font-size:11px; font-family:\"Segoe UI\"; background:transparent;"
        )
        root.addWidget(self._status)
        self._sync_upload_enabled()
        self._sync_custom_actions()
        self._sync_records_address(push_history=True)

    def _build_rail(self) -> QWidget:
        rail = QFrame()
        rail.setStyleSheet(
            f"QFrame{{background:{_CARD};border:1px solid {_BORDER};border-radius:8px;}}"
        )
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        nav_lbl = QLabel("Navigation")
        nav_lbl.setStyleSheet(
            f"color:{_MUTED}; font-weight:600; font-size:11px; font-family:\"Segoe UI\"; background:transparent;"
        )
        lay.addWidget(nav_lbl)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self._folder_filter = QLineEdit()
        self._folder_filter.setPlaceholderText("Filter folders…")
        self._folder_filter.setClearButtonEnabled(True)
        self._folder_filter.setStyleSheet(_INPUT_SS)
        self._folder_filter.setToolTip(
            "Search folder names. Typing also shows empty folders that match."
        )
        self._folder_filter.textChanged.connect(self._on_folder_filter_changed)
        filter_row.addWidget(self._folder_filter, 1)
        lay.addLayout(filter_row)

        self._show_empty = QCheckBox("Show empty")
        self._show_empty.setChecked(False)
        self._show_empty.setStyleSheet(
            f"QCheckBox{{color:{_MUTED};font-size:11px; font-family:\"Segoe UI\";background:transparent;}}"
        )
        self._show_empty.setToolTip(
            "Off: only folders that already have Records files. "
            "On: full catalog (needed for first upload into an unused item)."
        )
        self._show_empty.toggled.connect(self._on_show_empty_toggled)
        lay.addWidget(self._show_empty)

        self._dest_tree = QTreeWidget()
        self._dest_tree.setStyleSheet(_tree_stylesheet())
        self._dest_tree.setHeaderHidden(True)
        self._dest_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._dest_tree.setAnimated(True)
        self._dest_tree.setExpandsOnDoubleClick(True)
        self._dest_tree.setRootIsDecorated(True)
        self._dest_tree.setItemsExpandable(True)
        self._dest_tree.setIndentation(16)
        self._dest_tree.setIconSize(QSize(18, 18))
        self._dest_tree.setUniformRowHeights(True)
        self._dest_tree.setAllColumnsShowFocus(False)
        self._dest_tree.setFrameShape(QFrame.NoFrame)
        self._dest_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._dest_tree.customContextMenuRequested.connect(self._dest_context_menu)
        self._dest_tree.currentItemChanged.connect(self._on_dest_changed)
        self._dest_tree.itemExpanded.connect(self._on_kind_expanded)
        self._dest_tree.itemCollapsed.connect(self._on_kind_collapsed)
        lay.addWidget(self._dest_tree, 1)

        hint = QLabel("Expand Custom / Items / Suppliers / Separate — like Windows Explorer.")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{_MUTED}; font-size:11px; font-family:\"Segoe UI\"; background:transparent;"
        )
        lay.addWidget(hint)
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

        self._dest_hint = QLabel("")
        self._dest_hint.hide()

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Type", "Status", "Filed", "Destination", "Note", "Size"]
        )
        self._table.setStyleSheet(_TABLE_SS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setFocusPolicy(Qt.StrongFocus)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._table_context_menu)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.setColumnWidth(1, 160)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(3, 110)
        self._table.setColumnWidth(4, 100)
        self._table.setColumnWidth(5, 140)
        self._table.setColumnWidth(6, 72)
        self._table.cellDoubleClicked.connect(self._on_content_double_clicked)
        lay.addWidget(self._table, 1)
        self._sync_upload_enabled()
        self._sync_custom_actions()
        return pane

    # ── Kind / destination selection ──────────────────────────────────────

    def _on_kind(self, kind: str) -> None:
        """Select a kind root in the nav tree (All for that group)."""
        self._kind = kind
        self._expanded_kinds.add(kind)
        root = self._kind_roots.get(kind)
        if root is not None:
            root.setExpanded(True)
            self._dest_tree.setCurrentItem(root)
            return
        self._populate_dest_tree()
        root = self._kind_roots.get(kind)
        if root is not None:
            self._dest_tree.setCurrentItem(root)

    def _make_tree_item(self, data: dict[str, Any], *, label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label])
        tip = str(data.get("display_label") or label)
        count = int(data.get("file_count") or 0)
        if not data.get("_kind_root") and not data.get("_all") and count > 0:
            tip = f"{tip} · {count} file(s)"
        item.setToolTip(0, tip)
        item.setData(0, Qt.UserRole, data)
        if data.get("_kind_root"):
            icon_name, color = _KIND_ICONS.get(
                str(data.get("kind") or ""), ("mdi.folder", _FOLDER)
            )
            item.setIcon(0, qta.icon(icon_name, color=color))
        else:
            item.setIcon(0, qta.icon("mdi.folder", color=_FOLDER))
        return item

    def _folder_filter_text(self) -> str:
        return self._folder_filter.text().strip().casefold()

    def _include_empty_folders(self) -> bool:
        # Typing a filter also surfaces empty matches so first-time upload works.
        return self._show_empty.isChecked() or bool(self._folder_filter_text())

    def _row_matches_filter(self, row: dict[str, Any], needle: str) -> bool:
        if not needle:
            return True
        hay = " ".join(
            str(row.get(k) or "")
            for k in ("label", "ref_key", "display_label", "slug")
        ).casefold()
        path = " ".join(str(x) for x in (row.get("path_labels") or [])).casefold()
        return needle in hay or needle in path

    def _visible_dest_rows(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Destinations for the left tree / Explorer folder rows."""
        kind = kind or self._kind
        rows = list(self._destinations.get(kind) or [])
        needle = self._folder_filter_text()
        include_empty = self._include_empty_folders()

        if kind == "custom":
            # Always list custom folders (even empty) so right-click Rename/Delete works.
            by_id = {
                str(r.get("id") or ""): r for r in rows if str(r.get("id") or "")
            }
            keep: set[str] = set()
            for row in rows:
                fid = str(row.get("id") or "")
                if not fid:
                    continue
                if not self._row_matches_filter(row, needle):
                    continue
                keep.add(fid)
                parent_id = str(row.get("parent_id") or "")
                while parent_id and parent_id in by_id:
                    if parent_id in keep:
                        break
                    keep.add(parent_id)
                    parent_id = str(by_id[parent_id].get("parent_id") or "")
            return [
                {**r, "kind": "custom"}
                for r in rows
                if str(r.get("id") or "") in keep
            ]

        out: list[dict[str, Any]] = []
        for row in rows:
            count = int(row.get("file_count") or 0)
            if not include_empty and count <= 0:
                continue
            if not self._row_matches_filter(row, needle):
                continue
            out.append({**row, "kind": kind})
        return out

    def _on_folder_filter_changed(self, _text: str = "") -> None:
        self._populate_dest_tree()
        schedule_coro(self._load_files())

    def _on_show_empty_toggled(self, _checked: bool = False) -> None:
        self._populate_dest_tree()
        schedule_coro(self._load_files())

    def _on_kind_expanded(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("_kind_root"):
            self._expanded_kinds.add(str(data.get("kind") or ""))

    def _on_kind_collapsed(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("_kind_root"):
            self._expanded_kinds.discard(str(data.get("kind") or ""))

    def _add_custom_children(
        self, parent: QTreeWidgetItem, visible: list[dict[str, Any]]
    ) -> None:
        by_id: dict[str, dict[str, Any]] = {}
        for row in visible:
            fid = str(row.get("id") or "")
            if fid:
                by_id[fid] = row
        nodes: dict[str, QTreeWidgetItem] = {}
        pending = list(visible)
        guard = 0
        while pending and guard < len(visible) + 5:
            guard += 1
            next_pending: list[dict[str, Any]] = []
            for row in pending:
                fid = str(row.get("id") or "")
                if not fid:
                    continue
                parent_id = str(row.get("parent_id") or "")
                leaf = str(row.get("label") or row.get("ref_key") or "Untitled")
                data = {**row, "kind": "custom", "_all": False}
                node = self._make_tree_item(data, label=leaf)
                if parent_id and parent_id in nodes:
                    nodes[parent_id].addChild(node)
                    nodes[fid] = node
                elif not parent_id or parent_id not in by_id:
                    parent.addChild(node)
                    nodes[fid] = node
                else:
                    next_pending.append(row)
            if len(next_pending) == len(pending):
                for row in next_pending:
                    fid = str(row.get("id") or "")
                    if not fid or fid in nodes:
                        continue
                    leaf = str(row.get("label") or row.get("ref_key") or "Untitled")
                    node = self._make_tree_item(
                        {**row, "kind": "custom", "_all": False}, label=leaf
                    )
                    parent.addChild(node)
                    nodes[fid] = node
                break
            pending = next_pending

    def _populate_dest_tree(self) -> None:
        tree = self._dest_tree
        prev_kind = self._kind
        prev_selected = dict(self._selected) if self._selected else None
        tree.blockSignals(True)
        tree.clear()
        self._kind_roots = {}

        total_shown = 0
        total_all = 0
        with_files_all = 0

        for key, label, _icon in _KINDS:
            all_rows = list(self._destinations.get(key) or [])
            visible = self._visible_dest_rows(key)
            with_files = sum(1 for r in all_rows if int(r.get("file_count") or 0) > 0)
            total_shown += len(visible)
            total_all += len(all_rows)
            with_files_all += with_files

            root = self._make_tree_item(
                {
                    "_kind_root": True,
                    "_all": True,
                    "kind": key,
                    "file_count": with_files,
                },
                label=f"{label}  ({with_files})",
            )
            if key == "custom":
                self._add_custom_children(root, visible)
            else:
                for row in visible:
                    leaf = str(row.get("label") or row.get("ref_key") or "Untitled")
                    node = self._make_tree_item(
                        {**row, "kind": key, "_all": False}, label=leaf
                    )
                    root.addChild(node)

            tree.addTopLevelItem(root)
            self._kind_roots[key] = root
            if visible and (key in self._expanded_kinds or key == prev_kind):
                root.setExpanded(True)

        tree.blockSignals(False)

        # Restore selection
        if prev_selected:
            self._reselect(prev_selected)
        else:
            root = self._kind_roots.get(prev_kind) or next(
                iter(self._kind_roots.values()), None
            )
            if root is not None:
                tree.setCurrentItem(root)

        needle = self._folder_filter_text()
        if needle:
            self._status.setText(
                f"{total_shown} folder(s) match “{self._folder_filter.text().strip()}”"
                f" · {with_files_all} with files · {total_all} total"
            )
        elif self._show_empty.isChecked():
            self._status.setText(
                f"{total_shown} folder(s) · {with_files_all} with files · {total_all} total"
            )
        else:
            self._status.setText(
                f"{total_shown} with files · {total_all} total"
                + (
                    " · turn on Show empty for the full catalog"
                    if total_all > total_shown
                    else ""
                )
            )

    def _on_dest_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        self._month_key = None
        if current is None:
            self._selected = None
        else:
            data = current.data(0, Qt.UserRole) or {}
            kind = str(data.get("kind") or self._kind)
            if kind in {"custom", "item", "supplier", "separate"}:
                self._kind = kind
            if data.get("_kind_root") or data.get("_all"):
                self._selected = None
            else:
                self._selected = dict(data)
                # Expand parent kind when diving into a folder
                parent = current.parent()
                while parent is not None:
                    pdata = parent.data(0, Qt.UserRole) or {}
                    if pdata.get("_kind_root"):
                        parent.setExpanded(True)
                        self._expanded_kinds.add(str(pdata.get("kind") or ""))
                        break
                    parent = parent.parent()
        self._update_dest_hint()
        self._sync_upload_enabled()
        self._sync_custom_actions()
        schedule_coro(self._load_files())

    def _records_address(self) -> str:
        kind_label = next((lab for k, lab, _ in _KINDS if k == self._kind), self._kind)
        parts = ["Paperwork", "Records", kind_label]
        if self._selected:
            name = (
                self._selected.get("display_label")
                or self._selected.get("label")
                or self._selected.get("ref_key")
                or ""
            )
            if name:
                parts.append(str(name))
            if self._month_key:
                parts.append(_month_folder_label(self._month_key))
        else:
            parts.append("All")
        return " > ".join(parts)

    def _sync_records_address(self, *, push_history: bool = True) -> None:
        if not hasattr(self, "_chrome"):
            return
        self._chrome.set_address(self._records_address(), push_history=push_history)
        self._chrome.set_search_placeholder(
            f"Search {next((lab for k, lab, _ in _KINDS if k == self._kind), 'Records')}"
        )
        self._sync_up_enabled()

    def _update_dest_hint(self) -> None:
        if hasattr(self, "_dest_hint") and self._dest_hint.isVisible():
            kind_label = next((lab for k, lab, _ in _KINDS if k == self._kind), self._kind)
            if self._selected:
                name = (
                    self._selected.get("display_label")
                    or self._selected.get("label")
                    or self._selected.get("ref_key")
                    or ""
                )
                self._dest_hint.setText(f"{kind_label} · {name}")
            else:
                self._dest_hint.setText(f"{kind_label} · All")
        self._sync_records_address(push_history=True)

    def _sync_up_enabled(self) -> None:
        can_up = False
        if self._month_key:
            can_up = True
        elif self._kind == "custom" and self._selected:
            if self._selected.get("parent_id") or self._selected.get("id"):
                can_up = True
        elif self._selected is not None:
            can_up = True
        if hasattr(self, "_chrome"):
            self._chrome.set_nav_enabled(up=can_up)
        elif hasattr(self, "_up_btn"):
            self._up_btn.setEnabled(can_up)

    def _nav_back(self) -> None:
        if not hasattr(self, "_chrome"):
            return
        path = self._chrome.history_back()
        if path:
            self._apply_address_path(path)

    def _nav_forward(self) -> None:
        if not hasattr(self, "_chrome"):
            return
        path = self._chrome.history_forward()
        if path:
            self._apply_address_path(path)

    def _apply_address_path(self, path: str) -> None:
        """Best-effort restore kind from address history string."""
        parts = [p.strip() for p in path.split(">") if p.strip()]
        # Paperwork > Records > Items > …
        if len(parts) >= 3:
            kind_label = parts[2]
            for key, lab, _ in _KINDS:
                if lab.casefold() == kind_label.casefold():
                    if key != self._kind:
                        self._on_kind(key)
                    break
        self._sync_records_address(push_history=False)

    def _child_folders(self) -> list[dict[str, Any]]:
        """Folders shown inside the current Explorer location."""
        rows = self._visible_dest_rows()
        if self._kind == "custom":
            if self._selected and self._selected.get("id"):
                parent_id = str(self._selected["id"])
                return [
                    {**r, "kind": "custom", "_all": False}
                    for r in rows
                    if str(r.get("parent_id") or "") == parent_id
                ]
            # All: root custom folders only
            return [
                {**r, "kind": "custom", "_all": False}
                for r in rows
                if not r.get("parent_id")
            ]
        # Item / supplier / separate — All shows each destination as a folder;
        # inside a destination there are no nested folders.
        if self._selected is None:
            return [{**r, "kind": self._kind, "_all": False} for r in rows]
        return []

    def _sync_custom_actions(self) -> None:
        is_custom = self._kind == "custom"
        has_sel = bool(self._selected and self._selected.get("id"))
        if hasattr(self, "_new_btn"):
            self._new_btn.setEnabled(is_custom)
            self._sub_btn.setEnabled(is_custom and has_sel)
            self._rename_btn.setEnabled(is_custom and has_sel)
        if hasattr(self, "_cmd"):
            self._cmd.set_enabled(
                "paste", bool(self._clip and self._clip.get("files"))
            )

    def _sync_upload_enabled(self) -> None:
        if hasattr(self, "_upload_btn"):
            self._upload_btn.setEnabled(self._selected is not None)

    def _dest_context_menu(self, pos) -> None:
        """Windows-style folder command menu for any Navigation tree node."""
        item = self._dest_tree.itemAt(pos)
        if item is None:
            return
        QToolTip.hideText()
        self._dest_tree.setCurrentItem(item)
        data = dict(item.data(0, Qt.UserRole) or {})
        kind = str(data.get("kind") or self._kind or "")
        is_root = bool(data.get("_kind_root") or data.get("_all"))
        is_custom_folder = kind == "custom" and not is_root and bool(data.get("id"))
        is_custom_root = kind == "custom" and is_root
        has_custom_sel = bool(self._selected and self._selected.get("id"))

        menu = apply_light_menu(QMenu(self))
        open_act = menu.addAction("Open")
        open_act.setIcon(qta.icon("mdi.folder-open-outline", color=_TEXT))
        refresh_act = menu.addAction("Refresh")
        refresh_act.setIcon(qta.icon("mdi.refresh", color=_TEXT))
        upload_act = menu.addAction("Upload files…")
        upload_act.setIcon(qta.icon("mdi.upload-outline", color=_TEXT))
        upload_act.setEnabled(not is_root and self._selected is not None)

        new_act = sub_act = rename_act = delete_act = None
        if is_custom_root or is_custom_folder:
            menu.addSeparator()
            new_act = menu.addAction("New folder…")
            new_act.setIcon(qta.icon("mdi.folder-plus-outline", color=_TEXT))
            sub_act = menu.addAction("New subfolder…")
            sub_act.setIcon(qta.icon("mdi.folder-pound-outline", color=_TEXT))
            sub_act.setEnabled(is_custom_folder or has_custom_sel)
            menu.addSeparator()
            rename_act = menu.addAction("Rename…")
            rename_act.setEnabled(is_custom_folder)
            delete_act = menu.addAction("Delete…")
            delete_act.setIcon(qta.icon("mdi.delete-outline", color=_TEXT))
            delete_act.setEnabled(is_custom_folder)

        chosen = menu.exec(self._dest_tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == open_act:
            schedule_coro(self._load_files())
        elif chosen == refresh_act:
            schedule_coro(self.refresh())
        elif chosen == upload_act:
            schedule_coro(self._upload())
        elif new_act is not None and chosen == new_act:
            schedule_coro(self._new_custom())
        elif sub_act is not None and chosen == sub_act:
            schedule_coro(self._new_custom_subfolder())
        elif rename_act is not None and chosen == rename_act:
            schedule_coro(self._rename_custom())
        elif delete_act is not None and chosen == delete_act:
            schedule_coro(self._delete_custom())

    def _table_context_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        if index.isValid():
            self._table.selectRow(index.row())
        rows = self._selected_content_rows()
        menu = apply_light_menu(QMenu(self))
        open_act = menu.addAction("Open")
        open_act.setIcon(qta.icon("mdi.folder-open-outline", color=_TEXT))
        view_act = menu.addAction("View")
        view_act.setIcon(qta.icon("mdi.eye-outline", color=_TEXT))
        menu.addSeparator()
        if self._kind == "custom":
            new_act = menu.addAction("New folder…")
            new_act.setIcon(qta.icon("mdi.folder-plus-outline", color=_TEXT))
            sub_act = menu.addAction("New subfolder…")
            sub_act.setIcon(qta.icon("mdi.folder-pound-outline", color=_TEXT))
            sub_act.setEnabled(bool(self._selected and self._selected.get("id")))
            rename_act = menu.addAction("Rename…")
            rename_act.setEnabled(bool(self._selected and self._selected.get("id")))
            del_folder_act = menu.addAction("Delete folder…")
            del_folder_act.setEnabled(bool(self._selected and self._selected.get("id")))
            menu.addSeparator()
        else:
            new_act = sub_act = rename_act = del_folder_act = None
        cut_act = menu.addAction("Cut")
        cut_act.setIcon(qta.icon("mdi.content-cut", color=_TEXT))
        copy_act = menu.addAction("Copy")
        copy_act.setIcon(qta.icon("mdi.content-copy", color=_TEXT))
        paste_act = menu.addAction("Paste")
        paste_act.setIcon(qta.icon("mdi.content-paste", color=_TEXT))
        menu.addSeparator()
        trash_act = menu.addAction("Move to Trash")
        trash_act.setIcon(qta.icon("mdi.delete-outline", color=_TEXT))
        folder_rows = [r for r in rows if r.get("_row_kind") == "folder"]
        month_rows = [r for r in rows if r.get("_row_kind") == "month"]
        file_rows = [r for r in rows if r.get("_row_kind") == "file"]
        open_act.setEnabled(bool(rows))
        view_act.setEnabled(bool(file_rows or month_rows or folder_rows))
        cut_act.setEnabled(bool(file_rows))
        copy_act.setEnabled(bool(file_rows))
        paste_act.setEnabled(bool(self._clip and self._clip.get("files")))
        trash_act.setEnabled(bool(file_rows))
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == open_act:
            if folder_rows:
                self._open_folder(folder_rows[0].get("folder") or {})
            elif month_rows:
                self._open_month(str(month_rows[0].get("month_key") or ""))
            elif file_rows:
                self._view_selected()
        elif chosen == view_act:
            self._view_selected()
        elif new_act is not None and chosen == new_act:
            schedule_coro(self._new_custom())
        elif sub_act is not None and chosen == sub_act:
            schedule_coro(self._new_custom_subfolder())
        elif rename_act is not None and chosen == rename_act:
            schedule_coro(self._rename_custom())
        elif del_folder_act is not None and chosen == del_folder_act:
            schedule_coro(self._delete_custom())
        elif chosen == cut_act:
            self._clip_cut()
        elif chosen == copy_act:
            self._clip_copy()
        elif chosen == paste_act:
            schedule_coro(self._clip_paste())
        elif chosen == trash_act:
            schedule_coro(self._trash_selected_files())

    def _selected_content_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        for item in self._table.selectedItems():
            data = item.data(Qt.UserRole)
            if not isinstance(data, dict):
                continue
            key = id(data)
            if key in seen:
                continue
            seen.add(key)
            rows.append(data)
        return rows

    async def _trash_selected_files(self) -> None:
        ids = [
            str(r.get("id") or "").strip()
            for r in self._selected_content_rows()
            if r.get("_row_kind") not in {"folder", "month"} and r.get("id")
        ]
        if not ids:
            QMessageBox.information(
                self, "Records", "Select file(s) to move to Trash."
            )
            return
        if not ask_confirm(
            self,
            title="Move to Trash",
            heading=f"Move {len(ids)} file(s) to Trash?",
            body="Open Paperwork → Trash to restore or permanently delete.",
            accept_label="Move to Trash",
            destructive=True,
        ):
            return
        try:
            result = await trash_receipt_files(ids)
            self._status.setText(
                f"Moved {int(result.get('trashed') or 0)} file(s) to Trash"
            )
            await self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Trash failed", str(exc))

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
            self._populate_dest_tree()
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

    def _iter_tree_items(self, root: QTreeWidgetItem | None = None):
        if root is None:
            for i in range(self._dest_tree.topLevelItemCount()):
                item = self._dest_tree.topLevelItem(i)
                yield item
                yield from self._iter_tree_items(item)
            return
        for i in range(root.childCount()):
            child = root.child(i)
            yield child
            yield from self._iter_tree_items(child)

    def _reselect(self, prev: dict[str, Any]) -> None:
        target_id = str(prev.get("id") or "")
        target_key = str(prev.get("ref_key") or "")
        for item in self._iter_tree_items():
            data = item.data(0, Qt.UserRole) or {}
            if data.get("_all"):
                continue
            hit = False
            if target_id and str(data.get("id") or "") == target_id:
                hit = True
            elif target_key and str(data.get("ref_key") or "") == target_key:
                hit = True
            if not hit:
                continue
            # Expand ancestors so the node is visible
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self._dest_tree.setCurrentItem(item)
            self._dest_tree.scrollToItem(item)
            return

    async def _load_files(self, *, show_overlay: bool = True) -> None:
        if show_overlay:
            show_widget_loading(self._table, "Loading…")
        try:
            folders = self._child_folders()
            # Filename search should not hide folder rows unless they match too
            needle = self._search.text().strip().lower()
            if needle:
                folders = [
                    f
                    for f in folders
                    if needle
                    in str(
                        f.get("display_label") or f.get("label") or f.get("ref_key") or ""
                    ).lower()
                ]
            # Windows Explorer style: kind roots (Items / Suppliers / …) show
            # destination folders only. Files appear only inside a concrete folder
            # so they do not duplicate at “All” and again under the item.
            # Exception: an active filename search at All still lists matches.
            q = self._search.text().strip()
            if self._selected is None and not q:
                self._files = []
            else:
                kwargs: dict[str, Any] = {
                    "kind": self._kind,
                    "q": q,
                    "date_from": self._date_from.date().toString("yyyy-MM-dd"),
                    "date_to": self._date_to.date().toString("yyyy-MM-dd"),
                    "limit": 500,
                }
                if self._selected:
                    if self._kind == "custom" and self._selected.get("id"):
                        kwargs["folder_id"] = str(self._selected["id"])
                    else:
                        kwargs["ref_key"] = str(self._selected.get("ref_key") or "")
                self._files = await list_record_files(**kwargs)

            # Inside an item/folder: group by File-as month (like Storage YYYY/MM),
            # unless searching or already drilled into a month.
            month_rows: list[dict[str, Any]] = []
            files_out = list(self._files)
            group_by_month = bool(
                self._selected is not None and not q and not self._month_key
            )
            if group_by_month:
                buckets: dict[str, list[dict[str, Any]]] = {}
                for row in self._files:
                    key = _file_month_key(row) or "unknown"
                    buckets.setdefault(key, []).append(row)
                for key in sorted(buckets.keys(), reverse=True):
                    rows = buckets[key]
                    total_size = sum(int(r.get("size") or 0) for r in rows)
                    month_rows.append(
                        {
                            "_row_kind": "month",
                            "month_key": key,
                            "label": (
                                _month_folder_label(key)
                                if key != "unknown"
                                else "Unknown month"
                            ),
                            "file_count": len(rows),
                            "total_size": total_size,
                        }
                    )
                files_out = []
            elif self._month_key:
                files_out = [
                    r
                    for r in self._files
                    if _file_month_key(r) == self._month_key
                ]

            self._content_rows = (
                [{"_row_kind": "folder", "folder": f} for f in folders]
                + month_rows
                + [{"_row_kind": "file", "file": f} for f in files_out]
            )
            self._fill_table()
            dest = (
                (self._selected or {}).get("display_label")
                or (self._selected or {}).get("label")
                or (self._selected or {}).get("ref_key")
                or f"All {self._kind}"
            )
            if self._month_key:
                dest = f"{dest} · {_month_folder_label(self._month_key)}"
            self._status.setText(
                f"{len(folders)} folder(s)"
                + (f" · {len(month_rows)} month(s)" if month_rows else "")
                + f" · {len(files_out)} file(s) · {dest}"
            )
            self._sync_records_address(push_history=False)
            self._sync_up_enabled()
        except Exception as exc:
            self._files = []
            self._content_rows = []
            self._fill_table()
            self._status.setText(f"Could not load files: {exc}")
        finally:
            if show_overlay:
                hide_widget_loading(self._table)

    def _fill_table(self) -> None:
        self._table.setRowCount(0)
        cut_ids = self._cut_file_ids()
        for idx, entry in enumerate(self._content_rows):
            r = self._table.rowCount()
            self._table.insertRow(r)
            bg = _stripe(r)
            is_cut = False
            if entry.get("_row_kind") == "folder":
                folder = entry.get("folder") or {}
                name = str(
                    folder.get("label")
                    or folder.get("ref_key")
                    or "Untitled"
                )
                path = str(
                    folder.get("display_label") or folder.get("label") or name
                )
                total_size = int(folder.get("total_size") or 0)
                file_count = int(folder.get("file_count") or 0)
                size_s = _fmt_size(total_size) if total_size > 0 else (
                    "0 B" if file_count == 0 else "—"
                )
                if total_size <= 0 and file_count > 0:
                    # Count known but sizes missing on older payloads
                    size_s = f"{file_count} file(s)"
                values = [name, "Folder", "—", "—", path, "—", size_s]
                tip = f"Open folder: {path}"
                if file_count or total_size:
                    tip = (
                        f"{file_count} file(s) · {_fmt_size(total_size)}\n{tip}"
                    )
                payload = {"_row_kind": "folder", "folder": folder}
                icon = qta.icon("mdi.folder-outline", color=_ACCENT)
            elif entry.get("_row_kind") == "month":
                month_key = str(entry.get("month_key") or "")
                name = str(entry.get("label") or _month_folder_label(month_key))
                total_size = int(entry.get("total_size") or 0)
                file_count = int(entry.get("file_count") or 0)
                size_s = _fmt_size(total_size) if total_size > 0 else (
                    "0 B" if file_count == 0 else "—"
                )
                ym_path = month_key.replace("-", "/") if month_key else "—"
                values = [
                    name,
                    "Month",
                    "—",
                    month_key or "—",
                    ym_path,
                    f"{file_count} file(s)",
                    size_s,
                ]
                tip = (
                    f"Open filing month {name}\n"
                    f"{file_count} file(s) · {_fmt_size(total_size)}\n"
                    f"Server path …/{ym_path}/"
                )
                payload = {
                    "_row_kind": "month",
                    "month_key": month_key,
                    "label": name,
                }
                icon = qta.icon("mdi.folder-clock-outline", color=_ACCENT)
            else:
                row = entry.get("file") or {}
                rec = row.get("record") or {}
                kind = str(rec.get("kind") or "").strip().lower()
                kind_title = {
                    "item": "Item",
                    "supplier": "Supplier",
                    "separate": "Separate",
                    "custom": "Custom",
                }.get(kind, kind.title() if kind else "Records")
                # Type = item / folder name (what the paper belongs under)
                type_s = str(rec.get("label") or rec.get("ref_key") or kind_title)
                if rec.get("from_approved"):
                    status_s = "Approved · bridged"
                else:
                    # Company paper — not chase approval
                    status_s = "Records"
                note = str(rec.get("note") or "—")
                if rec.get("from_approved") and note == "—":
                    note = "Chase approved"
                filed = str(rec.get("file_date") or "").strip()
                filed_display = filed if filed else _fmt_when(row.get("uploaded_at"))
                name = str(row.get("original_filename") or "—")
                values = [
                    name,
                    type_s,
                    status_s,
                    filed_display,
                    kind_title,
                    note,
                    _fmt_size(int(row.get("size") or 0)),
                ]
                tip = f"Uploaded: {_fmt_when(row.get('uploaded_at'))}"
                if filed:
                    tip = f"Filed as: {filed}\n{tip}"
                fid = str(row.get("id") or "")
                payload = {"_row_kind": "file", "id": fid}
                is_cut = bool(fid and fid in cut_ids)
                icon = qta.icon(
                    "mdi.file-outline",
                    color=_CUT_FADE if is_cut else _MUTED,
                )
                if is_cut:
                    tip = f"Cut — paste into another folder\n{tip}"

            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setBackground(bg)
                if c == 0:
                    item.setData(Qt.UserRole, payload)
                    item.setIcon(icon)
                    item.setToolTip(tip)
                if c == 3 and entry.get("_row_kind") == "file":
                    item.setToolTip(tip)
                if is_cut:
                    item.setForeground(QColor(_CUT_FADE))
                self._table.setItem(r, c, item)
            self._table.setRowHeight(r, _ROW_H)

    def _selected_content(self) -> dict[str, Any] | None:
        items = self._table.selectedItems()
        if not items:
            return None
        cell = self._table.item(items[0].row(), 0)
        data = cell.data(Qt.UserRole) if cell else None
        return data if isinstance(data, dict) else None

    def _selected_file_id(self) -> str:
        data = self._selected_content()
        if not data or data.get("_row_kind") != "file":
            return ""
        return str(data.get("id") or "")

    def _on_content_double_clicked(self, row: int, _col: int) -> None:
        cell = self._table.item(row, 0)
        data = cell.data(Qt.UserRole) if cell else None
        if not isinstance(data, dict):
            return
        if data.get("_row_kind") == "folder":
            folder = data.get("folder") or {}
            self._open_folder(folder)
            return
        if data.get("_row_kind") == "month":
            self._open_month(str(data.get("month_key") or ""))
            return
        self._view_selected()

    def _open_month(self, month_key: str) -> None:
        key = (month_key or "").strip()
        if not key or key == "unknown":
            # Still allow opening unknown bucket
            if not key:
                return
        self._month_key = key
        self._sync_records_address(push_history=True)
        schedule_coro(self._load_files())

    def _open_folder(self, folder: dict[str, Any]) -> None:
        """Drill into a folder — sync left tree + reload contents."""
        if not folder:
            return
        self._month_key = None
        self._reselect(
            {
                "id": folder.get("id"),
                "ref_key": folder.get("ref_key"),
            }
        )
        # _reselect sets current tree item → _on_dest_changed loads files

    def _go_up(self) -> None:
        if self._month_key:
            self._month_key = None
            self._sync_records_address(push_history=True)
            schedule_coro(self._load_files())
            return
        if not self._selected:
            return
        if self._kind == "custom":
            parent_id = str(self._selected.get("parent_id") or "")
            if parent_id:
                parent = next(
                    (
                        r
                        for r in (self._destinations.get("custom") or [])
                        if str(r.get("id") or "") == parent_id
                    ),
                    None,
                )
                if parent:
                    self._reselect(
                        {"id": parent.get("id"), "ref_key": parent.get("ref_key")}
                    )
                    return
        # Back to kind root (Custom / Items / …)
        root = self._kind_roots.get(self._kind)
        if root is not None:
            self._dest_tree.setCurrentItem(root)

    def _view_selected(self) -> None:
        data = self._selected_content()
        if data and data.get("_row_kind") == "folder":
            self._open_folder(data.get("folder") or {})
            return
        if data and data.get("_row_kind") == "month":
            self._open_month(str(data.get("month_key") or ""))
            return
        fid = self._selected_file_id()
        if not fid:
            QMessageBox.information(
                self, "Records", "Select a file to view, or double-click a folder."
            )
            return
        row = next((f for f in self._files if str(f.get("id")) == fid), None)
        title = str((row or {}).get("original_filename") or "")
        open_receipt_viewer(self, fid, title_hint=title)

    # ── Custom folder CRUD ────────────────────────────────────────────────

    async def _new_custom(self) -> None:
        label, ok = QInputDialog.getText(
            self,
            "New custom folder",
            "Folder name (or Parent/Child for a nested path):",
        )
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
        shown = folder.get("display_label") or folder.get("label")
        self._status.setText(f"Created custom folder “{shown}”")

    async def _new_custom_subfolder(self) -> None:
        if not self._selected or not self._selected.get("id"):
            QMessageBox.information(
                self, "Records", "Select a parent custom folder first."
            )
            return
        parent_name = str(
            self._selected.get("display_label")
            or self._selected.get("label")
            or "folder"
        )
        label, ok = QInputDialog.getText(
            self,
            "New subfolder",
            f"Subfolder name under “{parent_name}”:",
        )
        if not ok:
            return
        label = " ".join((label or "").split())
        if not label:
            return
        if "/" in label or "\\" in label:
            QMessageBox.information(
                self,
                "Records",
                "Enter a single folder name (no /). Nest further with Subfolder again.",
            )
            return
        try:
            folder = await create_custom_folder(
                label, parent_id=str(self._selected["id"])
            )
        except ApiError as exc:
            QMessageBox.warning(self, "Records", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Records", f"Could not create subfolder:\n{exc}")
            return
        await self.refresh()
        self._reselect({"id": folder.get("id"), "ref_key": folder.get("ref_key")})
        shown = folder.get("display_label") or folder.get("label")
        self._status.setText(f"Created subfolder “{shown}”")

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
        name = str(
            self._selected.get("display_label")
            or self._selected.get("label")
            or "this folder"
        )
        if not ask_confirm(
            self,
            title="Delete custom folder",
            heading=f"Delete “{name}”?",
            body=(
                "Files in this folder (and subfolders) move to Trash and can be "
                "restored from Paperwork → Trash. The folder is then removed."
            ),
            accept_label="Delete",
            destructive=True,
        ):
            return
        try:
            result = await delete_custom_folder(
                str(self._selected["id"]), cascade=True
            )
        except ApiError as exc:
            QMessageBox.warning(self, "Records", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Records", f"Could not delete folder:\n{exc}")
            return
        self._selected = None
        await self.refresh()
        n = int(result.get("trashed_files") or 0)
        self._status.setText(
            f"Deleted “{name}” · {n} file(s) moved to Trash"
        )

    def _cut_file_ids(self) -> set[str]:
        clip = self._clip
        if not clip or clip.get("mode") != "cut":
            return set()
        return {
            str(f.get("file_id") or "").strip()
            for f in (clip.get("files") or [])
            if str(f.get("file_id") or "").strip()
        }

    def _clip_cut(self) -> None:
        ids = [
            str(r.get("id") or "")
            for r in self._selected_content_rows()
            if r.get("_row_kind") == "file" and r.get("id")
        ]
        if not ids:
            QMessageBox.information(self, "Cut", "Select file(s) first.")
            return
        self._clip = {"mode": "cut", "files": [{"file_id": i} for i in ids]}
        self._status.setText(
            f"Cut {len(ids)} file(s) — open a folder and Paste (Esc cancels)"
        )
        self._fill_table()
        self._sync_custom_actions()

    def _clip_copy(self) -> None:
        ids = [
            str(r.get("id") or "")
            for r in self._selected_content_rows()
            if r.get("_row_kind") == "file" and r.get("id")
        ]
        if not ids:
            QMessageBox.information(self, "Copy", "Select file(s) first.")
            return
        self._clip = {"mode": "copy", "files": [{"file_id": i} for i in ids]}
        self._status.setText(
            f"Copied {len(ids)} file(s) — Paste downloads a local copy, "
            "or use Download ZIP"
        )
        self._fill_table()
        self._sync_custom_actions()

    def _clear_clip(self) -> None:
        if not self._clip:
            return
        self._clip = None
        self._fill_table()
        self._sync_custom_actions()
        self._status.setText("Clipboard cleared")

    async def _clip_paste(self) -> None:
        clip = self._clip
        if not clip or not clip.get("files"):
            QMessageBox.information(
                self, "Paste", "Clipboard is empty — Cut or Copy first."
            )
            return
        ids = [
            str(f.get("file_id") or "").strip()
            for f in (clip.get("files") or [])
            if str(f.get("file_id") or "").strip()
        ]
        if not ids:
            return
        mode = str(clip.get("mode") or "cut")

        if mode == "cut":
            if not self._selected:
                QMessageBox.information(
                    self,
                    "Paste",
                    "Open a concrete folder (not All) to paste into — "
                    "like Windows Explorer.",
                )
                return
            kind = str(self._selected.get("kind") or self._kind)
            label = str(
                self._selected.get("display_label")
                or self._selected.get("label")
                or self._selected.get("ref_key")
                or "folder"
            )
            if not ask_confirm(
                self,
                title="Paste",
                heading=f"Move {len(ids)} file(s) into “{label}”?",
                body="Files leave their current Records folder and appear here.",
                accept_label="Paste",
                destructive=False,
            ):
                return
            try:
                result = await move_record_files(
                    ids,
                    kind=kind,
                    ref_key=str(self._selected.get("ref_key") or ""),
                    folder_id=str(self._selected.get("id") or ""),
                )
            except ApiError as exc:
                QMessageBox.warning(self, "Paste failed", str(exc))
                return
            except Exception as exc:
                QMessageBox.critical(self, "Paste failed", str(exc))
                return
            self._clip = None
            await self.refresh()
            skipped = int(result.get("skipped") or 0)
            self._status.setText(
                f"Moved {int(result.get('moved') or 0)} file(s) → {label}"
                + (f" · skipped {skipped}" if skipped else "")
            )
            self._sync_custom_actions()
            return

        # Copy → save duplicates to a folder on this PC
        from tahmeed.services.receipt_chase_service import fetch_receipt_file_bytes

        from tahmeed.ui.file_dialogs import get_existing_directory

        dest = await await_blocking(
            lambda: get_existing_directory(
                self, "Paste copy — save files to a folder on this PC"
            )
        )
        if not dest:
            return
        out = Path(dest) / "tahmeed-pasted-records"
        out.mkdir(parents=True, exist_ok=True)
        ok = 0
        for fid in ids:
            try:
                payload, _ = await fetch_receipt_file_bytes(fid)
                (out / f"{fid[:8]}.bin").write_bytes(payload)
                ok += 1
            except Exception:
                pass
        self._status.setText(f"Pasted {ok} copy(ies) to {out}")
        self._sync_custom_actions()

    async def _toolbar_delete(self) -> None:
        file_ids = [
            str(r.get("id") or "")
            for r in self._selected_content_rows()
            if r.get("_row_kind") == "file" and r.get("id")
        ]
        if file_ids:
            await self._trash_selected_files()
            return
        if self._kind == "custom" and self._selected and self._selected.get("id"):
            await self._delete_custom()
            return
        QMessageBox.information(
            self,
            "Delete",
            "Select file(s) to Trash, or a custom folder to delete (cascade to Trash).",
        )

    # ── Upload ────────────────────────────────────────────────────────────

    async def _upload(self) -> None:
        if not self._selected:
            QMessageBox.information(
                self,
                "Records",
                "Select a concrete folder (not “All”) before uploading.",
            )
            return
        from tahmeed.ui.file_dialogs import get_open_file_names

        paths, _ = await await_blocking(
            lambda: get_open_file_names(
                self,
                "Upload to Records",
                "Documents (*.pdf *.png *.jpg *.jpeg *.gif *.webp *.bmp *.tif *.tiff "
                "*.heic *.heif *.doc *.docx *.xls *.xlsx *.csv *.txt);;All files (*.*)",
            )
        )
        if not paths:
            return
        kind = str(self._selected.get("kind") or self._kind)
        file_date = self._file_as.date().toString("yyyy-MM-dd")
        file_ym = self._file_as.date().toString("yyyy/MM")
        n = len(paths)
        with UploadBusy(
            self,
            f"Uploading 0/{n} → …/{file_ym}/",
            title="Records upload",
            cancellable=True,
        ) as busy:
            busy.update(f"Uploading 0/{n} → …/{file_ym}/", value=0, maximum=n)

            def on_progress(done: int, total: int, filename: str) -> None:
                busy.raise_if_cancelled()
                if done >= total:
                    busy.update(
                        f"Finishing → …/{file_ym}/",
                        value=total,
                        maximum=total,
                    )
                else:
                    busy.update(
                        f"Uploading {done + 1}/{total} — {filename}\n"
                        f"Browse folder …/{file_ym}/",
                        value=done,
                        maximum=total,
                    )

            try:
                result = await upload_record_files(
                    paths,
                    kind=kind,
                    ref_key=str(self._selected.get("ref_key") or ""),
                    folder_id=str(self._selected.get("id") or ""),
                    file_date=file_date,
                    on_progress=on_progress,
                    should_cancel=busy.should_cancel,
                )
                total = int(result.get("total") or 0)
                dest = (result.get("destination") or {}).get("label") or kind
                got_date = str(result.get("file_date") or file_date)
                # Refresh destinations so file_count / hide-empty updates.
                await self.refresh()
                self._status.setText(
                    f"Uploaded {total} file(s) → {dest} · filed as {got_date} "
                    f"(…/{got_date[:7].replace('-', '/')}/)"
                )
                if got_date != file_date:
                    QMessageBox.warning(
                        self,
                        "File as date",
                        f"You chose {file_date} but the server filed as {got_date}.\n"
                        "Restart/redeploy the API so File as controls YYYY/MM.",
                    )
            except (UploadCancelled, InterruptedError):
                self._status.setText("Upload cancelled — no files kept.")
                QMessageBox.information(
                    self,
                    "Upload cancelled",
                    "Upload was cancelled. Any files from this batch were removed.",
                )
            except ApiError as exc:
                QMessageBox.warning(
                    self,
                    "Upload failed",
                    f"{exc}\n\nNo files from this batch were kept.",
                )
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Upload failed",
                    f"{exc}\n\nNo files from this batch were kept.",
                )

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
        from tahmeed.ui.file_dialogs import get_save_file_name

        path, _ = await await_blocking(
            lambda: get_save_file_name(
                self,
                "Save Records ZIP",
                "tahmeed-records.zip",
                "ZIP files (*.zip)",
            )
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
