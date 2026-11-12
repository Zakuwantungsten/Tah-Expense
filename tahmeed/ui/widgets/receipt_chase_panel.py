"""Receipts desk — requests, compliance KPIs, inbox, link, evidence pack."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import qtawesome as qta
from PySide6.QtCore import QDate, Qt, QSize
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.receipt_chase_service import (
    approve_files,
    download_consolidate_pack,
    download_evidence_pack,
    get_compliance,
    link_files,
    list_chase,
    list_evidence_exports,
    list_inbox,
    list_receipt_library,
    list_requests,
    mark_reminded,
    reject_files,
    search_link_candidates,
    upload_parked_files,
)
from tahmeed.ui.widgets.receipt_file_viewer import open_receipt_viewer

# Fluent / Office-adjacent tokens (align with admin desk)
_BG = "#F3F4F6"
_CARD = "#FFFFFF"
_BORDER = "#E5E7EB"
_TEXT = "#111827"
_MUTED = "#6B7280"
_ACCENT = "#0077C5"
_DANGER = "#B91C1C"
_OK = "#15803D"
_WARN = "#B45309"
# Master / Verify slate zebra (odd rows)
_STRIPE = "#F1F5F9"
_ROW_EVEN = "#FFFFFF"

_PAGE_SS = f"QWidget {{ background: {_BG}; }}"
_CARD_SS = (
    f"QFrame#kpiCard {{ background:{_CARD}; border:1px solid {_BORDER}; "
    f"border-radius:8px; }}"
)
_TABLE_SS = f"""
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
"""


def _stripe_bg(row_idx: int) -> QColor:
    return QColor(_STRIPE if row_idx % 2 else _ROW_EVEN)


_TAB_SS = f"""
QTabWidget::pane {{ border:1px solid {_BORDER}; border-radius:6px; top:-1px;
  background:{_CARD}; }}
QTabBar::tab {{
  background:transparent; color:{_MUTED}; font:600 12px 'Segoe UI';
  padding:8px 14px; margin-right:2px; border:none;
  border-bottom:2px solid transparent;
}}
QTabBar::tab:selected {{ color:{_ACCENT}; border-bottom:2px solid {_ACCENT}; }}
QTabBar::tab:hover {{ color:{_TEXT}; }}
"""
_INPUT_SS = (
    "QLineEdit,QDoubleSpinBox,QDateEdit,QCheckBox{"
    f"font:12px 'Segoe UI';color:{_TEXT};}}"
    "QLineEdit,QDoubleSpinBox,QDateEdit{"
    f"border:1px solid {_BORDER};border-radius:4px;padding:4px 8px;"
    "background:#FFFFFF;min-height:26px;}"
)
_CHIP_SS = """
QPushButton {{
  background:{bg}; color:{fg}; border:1px solid {border};
  border-radius:4px; padding:4px 10px; font:600 11px 'Segoe UI';
}}
QPushButton:hover {{ background:{hover}; }}
"""
_BTN_GHOST = f"""
QPushButton {{
  background:{_CARD}; color:{_TEXT}; border:1px solid {_BORDER};
  border-radius:4px; padding:5px 10px; font:600 12px 'Segoe UI';
}}
QPushButton:hover {{ background:#F8FAFC; }}
QPushButton:disabled {{ color:#9CA3AF; }}
"""
_BTN_PRIMARY = f"""
QPushButton {{
  background:{_ACCENT}; color:#FFFFFF; border:none;
  border-radius:4px; padding:5px 12px; font:600 12px 'Segoe UI';
}}
QPushButton:hover {{ background:#005EA3; }}
QPushButton:disabled {{ background:#93C5FD; }}
"""

_REQUEST_PRESETS: tuple[tuple[str, str], ...] = (
    ("Today", "today"),
    ("Open", "open"),
    ("Overdue", "overdue"),
    ("Reminded+", "heavy_reminders"),
    ("Received", "received"),
    ("Waived", "waived"),
    ("All", "all"),
)


def _fmt_age(hours: float | None) -> str:
    if hours is None:
        return "—"
    if hours < 24:
        return f"{hours:.0f}h"
    return f"{hours / 24.0:.1f}d"


def _fmt_when(value: Any) -> str:
    if value is None:
        return "—"
    text = str(value)
    if "T" in text:
        return text.replace("T", " ")[:16]
    return text[:16]


def _chip_style(active: bool) -> str:
    if active:
        return _CHIP_SS.format(
            bg="#EFF6FF", fg=_ACCENT, border="#BFDBFE", hover="#DBEAFE"
        )
    return _CHIP_SS.format(
        bg=_CARD, fg="#374151", border=_BORDER, hover="#F9FAFB"
    )


def _ghost_btn(text: str, icon: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setIcon(qta.icon(icon, color="#374151"))
    btn.setIconSize(QSize(14, 14))
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(28)
    btn.setStyleSheet(_BTN_GHOST)
    return btn


def _primary_btn(text: str, icon: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setIcon(qta.icon(icon, color="#FFFFFF"))
    btn.setIconSize(QSize(14, 14))
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(28)
    btn.setStyleSheet(_BTN_PRIMARY)
    return btn


class _KpiCard(QFrame):
    def __init__(self, title: str, icon: str, accent: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("kpiCard")
        self.setStyleSheet(_CARD_SS)
        self.setFixedHeight(72)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        top = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(qta.icon(icon, color=accent).pixmap(14, 14))
        lab = QLabel(title)
        lab.setStyleSheet(f"color:{_MUTED}; font:600 11px 'Segoe UI'; background:transparent;")
        top.addWidget(ic)
        top.addWidget(lab)
        top.addStretch()
        lay.addLayout(top)
        self._value = QLabel("—")
        self._value.setStyleSheet(
            f"color:{_TEXT}; font:700 20px 'Segoe UI'; background:transparent;"
        )
        lay.addWidget(self._value)

    def set_value(self, value: Any) -> None:
        self._value.setText(str(value))


class ReceiptChasePanel(QWidget):
    """Sleek receipts desk for accountant / cashier."""

    def __init__(self, parent=None, *, can_export: bool = False) -> None:
        super().__init__(parent)
        self.setStyleSheet(_PAGE_SS + _INPUT_SS)
        self._can_export = bool(can_export)
        self._preset = "open"
        self._request_rows: list[dict[str, Any]] = []
        self._chase_rows: list[dict[str, Any]] = []
        self._inbox_rows: list[dict[str, Any]] = []
        self._library_rows: list[dict[str, Any]] = []
        self._tx_rows: list[dict[str, Any]] = []
        self._export_rows: list[dict[str, Any]] = []
        self._build()
        asyncio.ensure_future(self.refresh_all())

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        heading = QLabel("Receipts")
        heading.setStyleSheet(f"color:{_TEXT}; font:700 20px 'Segoe UI'; background:transparent;")
        sub = QLabel(
            "Chase desk: requests, inbox, link, library, view/print. "
            "Evidence pack here; folder browse + TRA/KRA consolidate live under Receipt storage."
            if self._can_export
            else "Chase desk: requests, inbox, link, library — view and print receipts "
            "(ZIP export is accountant-only)."
        )
        sub.setStyleSheet(f"color:{_MUTED}; font:12px 'Segoe UI'; background:transparent;")
        titles.addWidget(heading)
        titles.addWidget(sub)
        header.addLayout(titles, 1)
        self._refresh_btn = _ghost_btn("Refresh", "mdi.refresh")
        self._refresh_btn.clicked.connect(lambda: asyncio.ensure_future(self.refresh_all()))
        header.addWidget(self._refresh_btn, 0, Qt.AlignTop)
        root.addLayout(header)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(8)
        self._kpi_today = _KpiCard("Today requested", "mdi.calendar-today", _ACCENT)
        self._kpi_needed = _KpiCard("Still needed", "mdi.clipboard-alert-outline", _WARN)
        self._kpi_overdue = _KpiCard("Overdue (48h+)", "mdi.timer-sand", _DANGER)
        self._kpi_heavy = _KpiCard("Reminded 3+", "mdi.bell-ring-outline", _DANGER)
        self._kpi_received = _KpiCard("Received", "mdi.check-circle-outline", _OK)
        self._kpi_parked = _KpiCard("Parked files", "mdi.inbox-outline", _MUTED)
        for card in (
            self._kpi_today,
            self._kpi_needed,
            self._kpi_overdue,
            self._kpi_heavy,
            self._kpi_received,
            self._kpi_parked,
        ):
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(_TAB_SS)
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(self._build_requests_tab(), "Requests")
        self._tabs.addTab(self._build_chase_tab(), "Chase")
        self._tabs.addTab(self._build_inbox_tab(), "Inbox")
        self._tabs.addTab(self._build_link_tab(), "Link")
        self._tabs.addTab(self._build_library_tab(), "Library")
        if self._can_export:
            self._tabs.addTab(self._build_export_tab(), "Evidence pack")
        root.addWidget(self._tabs, 1)

        foot = QHBoxLayout()
        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{_OK}; font:12px 'Segoe UI'; background:transparent;")
        foot.addWidget(self._status)
        foot.addStretch()
        root.addLayout(foot)

    def _style_table(self, table: QTableWidget) -> None:
        table.setStyleSheet(_TABLE_SS)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.setFocusPolicy(Qt.NoFocus)

    def _fill_row(
        self,
        table: QTableWidget,
        row_idx: int,
        values: list[str],
        row_data: dict[str, Any],
        *,
        paint: dict[int, Any] | None = None,
        tips: dict[int, str] | None = None,
    ) -> None:
        """Write S/NO + cells with Master-style slate zebra striping."""
        bg = _stripe_bg(row_idx)
        sn = QTableWidgetItem(str(row_idx + 1))
        sn.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        sn.setData(Qt.UserRole, row_data)
        sn.setBackground(bg)
        table.setItem(row_idx, 0, sn)
        for c, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setData(Qt.UserRole, row_data)
            item.setBackground(bg)
            if paint and c in paint:
                item.setForeground(paint[c])
            if tips and c in tips:
                item.setToolTip(tips[c])
            table.setItem(row_idx, c + 1, item)

    def _build_requests_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background:{_CARD};")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.setAlignment(Qt.AlignVCenter)
        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        self._preset_buttons: dict[str, QPushButton] = {}
        for label, key in _REQUEST_PRESETS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setChecked(key == "open")
            btn.setStyleSheet(_chip_style(key == "open"))
            btn.clicked.connect(lambda _=False, k=key: self._on_preset(k))
            self._preset_group.addButton(btn)
            self._preset_buttons[key] = btn
            toolbar.addWidget(btn)

        toolbar.addStretch()

        self._own_search = QLineEdit()
        self._own_search.setPlaceholderText("Filter ownership…")
        self._own_search.setClearButtonEnabled(True)
        self._own_search.setFixedWidth(180)
        self._own_search.setFixedHeight(28)
        self._own_search.setStyleSheet(
            f"QLineEdit{{font:12px 'Segoe UI';color:{_TEXT};"
            f"border:1px solid {_BORDER};border-radius:4px;"
            "padding:0 8px;background:#FFFFFF;min-height:0;max-height:28px;}}"
        )
        self._own_search.returnPressed.connect(
            lambda: asyncio.ensure_future(self._load_requests())
        )
        toolbar.addWidget(self._own_search)
        find_btn = _ghost_btn("Find", "mdi.magnify")
        find_btn.clicked.connect(lambda: asyncio.ensure_future(self._load_requests()))
        toolbar.addWidget(find_btn)
        self._mark_req_btn = _ghost_btn("Remind", "mdi.bell-check-outline")
        self._mark_req_btn.setToolTip("Mark selected request(s) as reminded (Ctrl/Shift multi-select).")
        self._mark_req_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._mark_selected_requests())
        )
        self._upload_req_btn = _ghost_btn("Upload…", "mdi.file-upload-outline")
        self._upload_req_btn.setToolTip(
            "Upload receipt(s) on behalf of selected request row(s). "
            "One file can cover many rows; many files can cover one row."
        )
        self._upload_req_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._upload_for_selected_requests(partial=False))
        )
        self._upload_partial_req_btn = _ghost_btn("Upload partial…", "mdi.file-document-outline")
        self._upload_partial_req_btn.setToolTip(
            "Upload a partial receipt for selected row(s); chase stays open with a note."
        )
        self._upload_partial_req_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._upload_for_selected_requests(partial=True))
        )
        self._link_req_btn = _ghost_btn("Link…", "mdi.link-variant")
        self._link_req_btn.setToolTip(
            "Jump to Link with these request rows in mind — pick Inbox file(s) then Link."
        )
        self._link_req_btn.clicked.connect(self._go_link_from_requests)
        self._copy_req_btn = _ghost_btn("Copy line", "mdi.content-copy")
        self._copy_req_btn.clicked.connect(self._copy_request_line)
        toolbar.addWidget(self._mark_req_btn)
        toolbar.addWidget(self._upload_req_btn)
        toolbar.addWidget(self._upload_partial_req_btn)
        toolbar.addWidget(self._link_req_btn)
        toolbar.addWidget(self._copy_req_btn)
        lay.addLayout(toolbar)

        self._req_sel_hint = QLabel("Select rows (Ctrl/Shift) for Remind / Upload / Link.")
        self._req_sel_hint.setStyleSheet(
            f"color:{_MUTED}; font:11px 'Segoe UI'; background:transparent;"
        )
        lay.addWidget(self._req_sel_hint)

        self._requests = QTableWidget(0, 9)
        self._requests.setHorizontalHeaderLabels(
            [
                "S/NO",
                "Ownership",
                "Status",
                "Item",
                "Amount",
                "Age",
                "Reminders",
                "Opened",
                "Payee",
            ]
        )
        self._style_table(self._requests)
        self._requests.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._requests.itemSelectionChanged.connect(self._refresh_request_sel_hint)
        hdr = self._requests.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for col in range(1, 8):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(8, QHeaderView.Stretch)
        lay.addWidget(self._requests, 1)
        return page

    def _build_chase_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background:{_CARD};")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        hint = QLabel("Grouped by ownership — who still owes paper.")
        hint.setStyleSheet(f"color:{_MUTED}; font:12px 'Segoe UI'; background:transparent;")
        lay.addWidget(hint)
        self._chase = QTableWidget(0, 6)
        self._chase.setHorizontalHeaderLabels(
            ["S/NO", "Ownership", "Open", "Oldest", "Last reminded", "Reminders"]
        )
        self._style_table(self._chase)
        self._chase.setSelectionMode(QAbstractItemView.SingleSelection)
        lay.addWidget(self._chase, 1)
        row = QHBoxLayout()
        mark_btn = _ghost_btn("Mark reminded", "mdi.bell-check-outline")
        mark_btn.clicked.connect(lambda: asyncio.ensure_future(self._mark_selected_chase()))
        copy_btn = _ghost_btn("Copy chase line", "mdi.content-copy")
        copy_btn.clicked.connect(self._copy_chase_line)
        row.addWidget(mark_btn)
        row.addWidget(copy_btn)
        row.addStretch()
        lay.addLayout(row)
        return page

    def _build_inbox_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background:{_CARD};")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        self._inbox_status = "parked"
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._inbox_chip_group = QButtonGroup(self)
        self._inbox_chip_group.setExclusive(True)
        self._inbox_chip_buttons: dict[str, QPushButton] = {}
        for label, key in (
            ("Parked", "parked"),
            ("Unmatched", "unmatched"),
            ("Matched", "matched"),
            ("Approved", "approved"),
            ("Rejected", "rejected"),
            ("Quarantine", "quarantine"),
            ("All", "all"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.setChecked(key == "parked")
            btn.setStyleSheet(_chip_style(key == "parked"))
            btn.clicked.connect(lambda _=False, k=key: self._on_inbox_status(k))
            self._inbox_chip_group.addButton(btn)
            self._inbox_chip_buttons[key] = btn
            chips.addWidget(btn)
        chips.addStretch()
        lay.addLayout(chips)

        actions = QHBoxLayout()
        drop_btn = _ghost_btn("Park files…", "mdi.file-upload-outline")
        drop_btn.clicked.connect(lambda: asyncio.ensure_future(self._park_files()))
        view_btn = _ghost_btn("View", "mdi.eye-outline")
        view_btn.clicked.connect(self._view_selected_file)
        approve_btn = _primary_btn("Approve", "mdi.check-bold")
        approve_btn.clicked.connect(lambda: asyncio.ensure_future(self._approve_selected()))
        reject_btn = _ghost_btn("Reject…", "mdi.close-circle-outline")
        reject_btn.clicked.connect(lambda: asyncio.ensure_future(self._reject_selected()))
        link_go = _ghost_btn("Link selected…", "mdi.link-variant")
        link_go.clicked.connect(self._go_link_with_selection)
        actions.addWidget(drop_btn)
        actions.addWidget(view_btn)
        actions.addWidget(approve_btn)
        actions.addWidget(reject_btn)
        actions.addWidget(link_go)
        actions.addStretch()
        lay.addLayout(actions)

        self._inbox = QTableWidget(0, 6)
        self._inbox.setHorizontalHeaderLabels(
            ["S/NO", "File", "Source", "Status", "Uploaded", "Size"]
        )
        self._style_table(self._inbox)
        self._inbox.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._inbox.itemSelectionChanged.connect(self._refresh_link_file_hint)
        self._inbox.cellDoubleClicked.connect(lambda *_: self._view_selected_file())
        lay.addWidget(self._inbox, 1)
        tip = QLabel(
            "Parked = unmatched or matched but not permanent. "
            "View (or double-click) opens the file before you Approve / Reject / Link. "
            "Approve keeps the file under files/ as official evidence; "
            "Reject copies into quarantine/ with a reason. "
            "Park files… for early drop with no row yet."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{_MUTED}; font:11px 'Segoe UI'; background:transparent;")
        lay.addWidget(tip)
        return page

    def _build_library_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background:{_CARD};")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)

        filters = QHBoxLayout()
        self._lib_from = QDateEdit()
        self._lib_from.setCalendarPopup(True)
        self._lib_from.setDisplayFormat("yyyy-MM-dd")
        self._lib_from.setDate(QDate.currentDate().addDays(-30))
        self._lib_to = QDateEdit()
        self._lib_to.setCalendarPopup(True)
        self._lib_to.setDisplayFormat("yyyy-MM-dd")
        self._lib_to.setDate(QDate.currentDate())
        self._lib_item = QLineEdit()
        self._lib_item.setPlaceholderText("Item")
        self._lib_payee = QLineEdit()
        self._lib_payee.setPlaceholderText("Payee")
        self._lib_own = QLineEdit()
        self._lib_own.setPlaceholderText("Ownership")
        self._lib_status = QComboBox()
        for label, key in (
            ("All statuses", "all"),
            ("Parked", "parked"),
            ("Unmatched", "unmatched"),
            ("Matched", "matched"),
            ("Approved", "approved"),
            ("Rejected", "rejected"),
        ):
            self._lib_status.addItem(label, key)
        search_btn = _ghost_btn("Search", "mdi.magnify")
        search_btn.clicked.connect(lambda: asyncio.ensure_future(self._load_library()))
        view_btn = _ghost_btn("View", "mdi.eye-outline")
        view_btn.clicked.connect(self._view_library_file)
        filters.addWidget(self._lib_from)
        filters.addWidget(self._lib_to)
        filters.addWidget(self._lib_item, 1)
        filters.addWidget(self._lib_payee, 1)
        filters.addWidget(self._lib_own, 1)
        filters.addWidget(self._lib_status)
        filters.addWidget(search_btn)
        filters.addWidget(view_btn)
        lay.addLayout(filters)

        if self._can_export:
            pack_row = QHBoxLayout()
            self._lib_approved_only = QCheckBox("Approved files only")
            self._lib_approved_only.setChecked(True)
            zip_btn = _ghost_btn("Download ZIP", "mdi.zip-box-outline")
            zip_btn.clicked.connect(
                lambda: asyncio.ensure_future(self._consolidate_download(to_folder=False))
            )
            folder_btn = _ghost_btn("Save to folder…", "mdi.folder-download-outline")
            folder_btn.clicked.connect(
                lambda: asyncio.ensure_future(self._consolidate_download(to_folder=True))
            )
            pack_row.addWidget(self._lib_approved_only)
            pack_row.addWidget(zip_btn)
            pack_row.addWidget(folder_btn)
            pack_row.addStretch()
            lay.addLayout(pack_row)

        tip = QLabel(
            "Search uses upload date. Consolidate Download ZIP / Save to folder uses "
            "transaction date + item/payee/ownership (same range filters), unique files only."
            if self._can_export
            else "Submitted receipts by upload date. Item / payee / ownership match linked rows. "
            "View opens the shared preview (Print is on the viewer toolbar)."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{_MUTED}; font:11px 'Segoe UI'; background:transparent;")
        lay.addWidget(tip)

        self._library = QTableWidget(0, 9)
        self._library.setHorizontalHeaderLabels(
            [
                "S/NO",
                "Uploaded",
                "File",
                "Status",
                "Item",
                "Payee",
                "Ownership",
                "Links",
                "Size",
            ]
        )
        self._style_table(self._library)
        self._library.setSelectionMode(QAbstractItemView.SingleSelection)
        self._library.cellDoubleClicked.connect(lambda *_: self._view_library_file())
        lay.addWidget(self._library, 1)
        return page

    def _build_link_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background:{_CARD};")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        self._link_file_hint = QLabel(
            "Inbox selection: none — pick parked file(s) on Inbox. "
            "Then multi-select rows below: 1 file→N rows, N files→1 row, or N×M."
        )
        self._link_file_hint.setWordWrap(True)
        self._link_file_hint.setStyleSheet(
            f"color:{_TEXT}; font:600 12px 'Segoe UI'; background:transparent;"
        )
        lay.addWidget(self._link_file_hint)
        link_actions = QHBoxLayout()
        view_link_btn = _ghost_btn("View selected file", "mdi.eye-outline")
        view_link_btn.clicked.connect(self._view_selected_file)
        link_actions.addWidget(view_link_btn)
        link_actions.addStretch()
        lay.addLayout(link_actions)
        filters = QHBoxLayout()
        self._own_filter = QLineEdit()
        self._own_filter.setPlaceholderText("Ownership")
        self._amt_filter = QDoubleSpinBox()
        self._amt_filter.setRange(0, 1_000_000_000)
        self._amt_filter.setDecimals(2)
        self._amt_filter.setSpecialValueText("Any amount")
        self._amt_filter.setValue(0)
        self._link_today = QCheckBox("Today")
        self._link_needed = QCheckBox("Open / partial")
        self._link_needed.setChecked(True)
        search_btn = _ghost_btn("Find rows", "mdi.magnify")
        search_btn.clicked.connect(lambda: asyncio.ensure_future(self._load_candidates()))
        filters.addWidget(self._own_filter, 2)
        filters.addWidget(self._amt_filter, 1)
        filters.addWidget(self._link_today)
        filters.addWidget(self._link_needed)
        filters.addWidget(search_btn)
        lay.addLayout(filters)
        tip = QLabel(
            "N files ↔ M rows. One receipt can cover many ownership rows. "
            "Partial keeps chase open with your note."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{_MUTED}; font:11px 'Segoe UI'; background:transparent;")
        lay.addWidget(tip)
        self._candidates = QTableWidget(0, 7)
        self._candidates.setHorizontalHeaderLabels(
            ["S/NO", "Date", "Amount", "Item", "Ownership", "Coverage", "Payee"]
        )
        self._style_table(self._candidates)
        self._candidates.setSelectionMode(QAbstractItemView.ExtendedSelection)
        lay.addWidget(self._candidates, 1)
        partial_row = QHBoxLayout()
        self._partial_check = QCheckBox("Partial link")
        self._partial_note = QLineEdit()
        self._partial_note.setPlaceholderText("e.g. covers fuel only")
        self._partial_note.setEnabled(False)
        self._partial_check.toggled.connect(self._partial_note.setEnabled)
        partial_row.addWidget(self._partial_check)
        partial_row.addWidget(self._partial_note, 1)
        lay.addLayout(partial_row)
        link_btn = _primary_btn("Link selected", "mdi.link-variant")
        link_btn.clicked.connect(lambda: asyncio.ensure_future(self._do_link()))
        lay.addWidget(link_btn, 0, Qt.AlignLeft)
        return page

    def _build_export_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background:{_CARD};")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        form = QHBoxLayout()
        form.setSpacing(8)
        today = QDate.currentDate()
        self._pack_from = QDateEdit()
        self._pack_from.setCalendarPopup(True)
        self._pack_from.setDisplayFormat("yyyy-MM-dd")
        self._pack_from.setDate(today.addMonths(-1))
        self._pack_to = QDateEdit()
        self._pack_to.setCalendarPopup(True)
        self._pack_to.setDisplayFormat("yyyy-MM-dd")
        self._pack_to.setDate(today)
        self._pack_item = QLineEdit()
        self._pack_item.setPlaceholderText("Items (comma-sep, e.g. Diesel, LATRA)")
        self._pack_payee = QLineEdit()
        self._pack_payee.setPlaceholderText("Payee (optional)")
        self._pack_own = QLineEdit()
        self._pack_own.setPlaceholderText("Ownership (optional)")
        for w in (
            self._pack_from,
            self._pack_to,
            self._pack_item,
            self._pack_payee,
            self._pack_own,
        ):
            form.addWidget(w)
        lay.addLayout(form)
        pack_opts = QHBoxLayout()
        self._pack_approved_only = QCheckBox("Approved files only")
        self._pack_approved_only.setChecked(True)
        pack_opts.addWidget(self._pack_approved_only)
        pack_opts.addStretch()
        lay.addLayout(pack_opts)
        tip = QLabel(
            "ZIP folders mirror approved/year/month/<item>/ on disk. "
            "Canonical bytes stay under files/."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{_MUTED}; font:11px 'Segoe UI'; background:transparent;")
        lay.addWidget(tip)
        build_btn = _primary_btn("Build & download ZIP", "mdi.download-outline")
        build_btn.clicked.connect(lambda: asyncio.ensure_future(self._do_export()))
        lay.addWidget(build_btn, 0, Qt.AlignLeft)
        recent = QLabel("Recent exports")
        recent.setStyleSheet(
            f"color:{_TEXT}; font:600 12px 'Segoe UI'; margin-top:8px; background:transparent;"
        )
        lay.addWidget(recent)
        self._exports = QTableWidget(0, 7)
        self._exports.setHorizontalHeaderLabels(
            ["S/NO", "When", "Kind", "Range", "Rows", "SHA-256", "By"]
        )
        self._style_table(self._exports)
        lay.addWidget(self._exports, 1)
        return page

    def _on_preset(self, key: str) -> None:
        self._preset = key
        for k, btn in self._preset_buttons.items():
            btn.setStyleSheet(_chip_style(k == key))
            btn.setChecked(k == key)
        asyncio.ensure_future(self._load_requests())

    async def refresh_all(self) -> None:
        await asyncio.gather(
            self._load_compliance(),
            self._load_requests(),
            self._load_chase(),
            self._load_inbox(),
            self._load_library(),
        )
        if self._can_export:
            await self._load_exports()
        self._status.setText("Updated")

    def refresh(self) -> None:
        asyncio.ensure_future(self.refresh_all())

    async def _load_compliance(self) -> None:
        try:
            summary = await get_compliance()
        except Exception as exc:
            QMessageBox.critical(self, "Receipts", f"Compliance load failed:\n{exc}")
            return
        self._kpi_today.set_value(summary.get("today_requested", 0))
        self._kpi_needed.set_value(summary.get("still_needed", 0))
        self._kpi_overdue.set_value(summary.get("overdue", 0))
        self._kpi_heavy.set_value(summary.get("heavy_reminders", 0))
        self._kpi_received.set_value(summary.get("received", 0))
        self._kpi_parked.set_value(
            summary.get("parked_uploads", summary.get("unmatched_uploads", 0))
        )

    def _on_inbox_status(self, key: str) -> None:
        self._inbox_status = key
        for k, btn in self._inbox_chip_buttons.items():
            btn.setStyleSheet(_chip_style(k == key))
            btn.setChecked(k == key)
        asyncio.ensure_future(self._load_inbox())

    async def _load_requests(self) -> None:
        try:
            self._request_rows = await list_requests(
                preset=self._preset,
                ownership=self._own_search.text().strip(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Receipts", f"Requests load failed:\n{exc}")
            self._request_rows = []
        self._requests.setRowCount(0)
        for row in self._request_rows:
            r = self._requests.rowCount()
            self._requests.insertRow(r)
            amount = row.get("amount")
            amount_s = (
                f"{amount} {row.get('currency') or ''}".strip()
                if amount is not None
                else "—"
            )
            status = str(row.get("status") or "")
            paint: dict[int, Any] = {}
            if status == "needed":
                paint[1] = Qt.GlobalColor.darkYellow
            elif status == "received":
                paint[1] = Qt.GlobalColor.darkGreen
            self._fill_row(
                self._requests,
                r,
                [
                    str(row.get("ownership") or ""),
                    status,
                    str(row.get("item") or ""),
                    amount_s,
                    _fmt_age(row.get("age_hours")),
                    str(row.get("reminder_count") or 0),
                    _fmt_when(row.get("opened_at")),
                    str(row.get("payee") or ""),
                ],
                row,
                paint=paint,
            )

    async def _load_chase(self) -> None:
        try:
            self._chase_rows = await list_chase()
        except Exception as exc:
            QMessageBox.critical(self, "Receipts", f"Chase load failed:\n{exc}")
            self._chase_rows = []
        self._chase.setRowCount(0)
        for row in self._chase_rows:
            r = self._chase.rowCount()
            self._chase.insertRow(r)
            self._fill_row(
                self._chase,
                r,
                [
                    str(row.get("ownership") or ""),
                    str(row.get("open_count") or 0),
                    _fmt_age(row.get("oldest_age_hours")),
                    _fmt_when(row.get("last_reminded_at")),
                    str(row.get("reminder_count") or 0),
                ],
                row,
            )

    async def _load_inbox(self) -> None:
        try:
            self._inbox_rows = await list_inbox(status=getattr(self, "_inbox_status", "parked"))
        except Exception as exc:
            QMessageBox.critical(self, "Receipts", f"Inbox load failed:\n{exc}")
            self._inbox_rows = []
        self._inbox.setRowCount(0)
        for row in self._inbox_rows:
            r = self._inbox.rowCount()
            self._inbox.insertRow(r)
            status = str(row.get("inbox_status") or "")
            paint: dict[int, Any] = {}
            if status == "approved":
                paint[2] = Qt.GlobalColor.darkGreen
            elif status == "rejected":
                paint[2] = Qt.GlobalColor.darkRed
            elif status == "unmatched":
                paint[2] = Qt.GlobalColor.darkYellow
            self._fill_row(
                self._inbox,
                r,
                [
                    str(row.get("original_filename") or row.get("id") or ""),
                    str(row.get("source") or ""),
                    status,
                    _fmt_when(row.get("uploaded_at")),
                    str(row.get("size") or 0),
                ],
                row,
                paint=paint,
            )

    def _refresh_link_file_hint(self) -> None:
        ids = self._selected_file_ids()
        hint = getattr(self, "_link_file_hint", None)
        if hint is None:
            return
        if not ids:
            hint.setText("Inbox selection: none — pick parked file(s) on Inbox.")
            return
        names: list[str] = []
        for item in self._inbox.selectedItems():
            row = item.data(Qt.UserRole) or {}
            name = str(row.get("original_filename") or row.get("id") or "")
            if name and name not in names:
                names.append(name)
        shown = ", ".join(names[:4])
        extra = f" (+{len(names) - 4} more)" if len(names) > 4 else ""
        hint.setText(f"Inbox selection: {len(ids)} file(s) — {shown}{extra}")

    def _go_link_with_selection(self) -> None:
        if not self._selected_file_ids():
            QMessageBox.information(self, "Receipts", "Select one or more inbox files first.")
            return
        self._refresh_link_file_hint()
        self._tabs.setCurrentIndex(3)
        asyncio.ensure_future(self._load_candidates())

    async def _park_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Park receipt files",
            "",
            "Receipts (*.pdf *.png *.jpg *.jpeg *.heic *.webp *.doc *.docx *.xls *.xlsx *.csv *.txt);;All (*.*)",
        )
        if not paths:
            return
        try:
            result = await upload_parked_files(paths)
            self._status.setText(f"Parked {result.get('total') or len(paths)} file(s)")
            self._inbox_status = "parked"
            if hasattr(self, "_inbox_chip_buttons"):
                self._on_inbox_status("parked")
            else:
                await self._load_inbox()
            await self._load_compliance()
        except Exception as exc:
            QMessageBox.critical(self, "Park failed", str(exc))

    async def _load_candidates(self) -> None:
        self._refresh_link_file_hint()
        ownership = self._own_filter.text().strip()
        amount = self._amt_filter.value()
        amt = None if amount <= 0 else float(amount)
        try:
            self._tx_rows = await search_link_candidates(
                ownership=ownership,
                amount=amt,
                needed_only=self._link_needed.isChecked(),
                today=self._link_today.isChecked(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Receipts", f"Row search failed:\n{exc}")
            self._tx_rows = []
        self._candidates.setRowCount(0)
        for row in self._tx_rows:
            r = self._candidates.rowCount()
            self._candidates.insertRow(r)
            cov = str(row.get("coverage") or "")
            n_att = int(row.get("attachment_count") or 0)
            cov_label = f"{cov}" + (f" ({n_att})" if n_att else "")
            self._fill_row(
                self._candidates,
                r,
                [
                    _fmt_when(row.get("date")),
                    f"{row.get('amount')} {row.get('currency') or ''}".strip(),
                    str(row.get("item") or ""),
                    str(row.get("ownership") or ""),
                    cov_label,
                    str(row.get("payee") or ""),
                ],
                row,
            )

    async def _load_exports(self) -> None:
        if not self._can_export:
            return
        try:
            self._export_rows = await list_evidence_exports()
        except Exception as exc:
            QMessageBox.critical(self, "Receipts", f"Exports load failed:\n{exc}")
            self._export_rows = []
        self._exports.setRowCount(0)
        for row in self._export_rows:
            r = self._exports.rowCount()
            self._exports.insertRow(r)
            sha = str(row.get("sha256") or "")
            kind = str(row.get("kind") or "evidence")
            tips: dict[int, str] = {}
            if sha:
                tips[4] = sha
            tips[1] = str(row.get("filename") or kind)
            self._fill_row(
                self._exports,
                r,
                [
                    _fmt_when(row.get("created_at")),
                    kind,
                    f"{_fmt_when(row.get('date_from'))[:10]} → {_fmt_when(row.get('date_to'))[:10]}",
                    str(row.get("row_count") or 0),
                    f"{sha[:12]}…" if len(sha) > 12 else sha,
                    str(row.get("created_by_username") or ""),
                ],
                row,
                tips=tips,
            )

    def _selected_request_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in self._requests.selectedItems():
            row = item.data(Qt.UserRole) or {}
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            rows.append(row)
        return rows

    def _selected_request_ids(self) -> list[str]:
        return [str(r.get("id") or "") for r in self._selected_request_rows() if r.get("id")]

    def _selected_request_tx_ids(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for row in self._selected_request_rows():
            tid = str(row.get("transaction_id") or "")
            if tid and tid not in seen:
                seen.add(tid)
                ids.append(tid)
        return ids

    def _refresh_request_sel_hint(self) -> None:
        hint = getattr(self, "_req_sel_hint", None)
        if hint is None:
            return
        n = len(self._selected_request_rows())
        txs = len(self._selected_request_tx_ids())
        if n <= 0:
            hint.setText("Select rows (Ctrl/Shift) for Remind / Upload / Link.")
            return
        hint.setText(
            f"{n} request(s) selected · {txs} linked row(s) — "
            "Remind, Upload, Upload partial, or Link…"
        )

    def _selected_chase(self) -> dict[str, Any] | None:
        items = self._chase.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.UserRole)

    def _selected_file_ids(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for item in self._inbox.selectedItems():
            row = item.data(Qt.UserRole) or {}
            fid = str(row.get("id") or "")
            if fid and fid not in seen:
                seen.add(fid)
                ids.append(fid)
        return ids

    def _selected_file_row(self) -> dict[str, Any] | None:
        for item in self._inbox.selectedItems():
            row = item.data(Qt.UserRole) or {}
            if isinstance(row, dict) and row.get("id"):
                return row
        return None

    def _view_selected_file(self) -> None:
        row = self._selected_file_row()
        if not row:
            QMessageBox.information(
                self,
                "Receipts",
                "Select an inbox file with a stored id first (disk-only quarantine rows cannot be opened this way).",
            )
            return
        fid = str(row.get("id") or "").strip()
        if not fid:
            QMessageBox.information(self, "Receipts", "This row has no file id to open.")
            return
        open_receipt_viewer(
            self,
            fid,
            title_hint=str(row.get("original_filename") or ""),
        )

    async def _load_library(self) -> None:
        if not hasattr(self, "_library"):
            return
        d0 = self._lib_from.date().toPython()
        d1 = self._lib_to.date().toPython()
        status = self._lib_status.currentData() or "all"
        try:
            self._library_rows = await list_receipt_library(
                date_from=d0.isoformat(),
                date_to=d1.isoformat(),
                item=self._lib_item.text().strip(),
                payee=self._lib_payee.text().strip(),
                ownership=self._lib_own.text().strip(),
                status=str(status),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Receipts", f"Library load failed:\n{exc}")
            self._library_rows = []
        self._library.setRowCount(0)
        for row in self._library_rows:
            r = self._library.rowCount()
            self._library.insertRow(r)
            status_s = str(row.get("inbox_status") or "")
            paint: dict[int, Any] = {}
            if status_s == "approved":
                paint[2] = Qt.GlobalColor.darkGreen
            elif status_s == "rejected":
                paint[2] = Qt.GlobalColor.darkRed
            self._fill_row(
                self._library,
                r,
                [
                    _fmt_when(row.get("uploaded_at")),
                    str(row.get("original_filename") or ""),
                    status_s,
                    str(row.get("item") or ""),
                    str(row.get("payee") or ""),
                    str(row.get("ownership") or ""),
                    str(row.get("linked_count") or 0),
                    str(row.get("size") or 0),
                ],
                row,
                paint=paint,
            )

    def _selected_library_row(self) -> dict[str, Any] | None:
        if not hasattr(self, "_library"):
            return None
        for item in self._library.selectedItems():
            row = item.data(Qt.UserRole) or {}
            if isinstance(row, dict) and row.get("id"):
                return row
        return None

    def _view_library_file(self) -> None:
        row = self._selected_library_row()
        if not row:
            QMessageBox.information(self, "Receipts", "Select a library file first.")
            return
        open_receipt_viewer(
            self,
            str(row.get("id") or ""),
            title_hint=str(row.get("original_filename") or ""),
        )

    async def _consolidate_download(self, *, to_folder: bool) -> None:
        if not self._can_export or not hasattr(self, "_lib_from"):
            return
        d0 = self._lib_from.date().toPython()
        d1 = self._lib_to.date().toPython()
        if d1 < d0:
            QMessageBox.information(
                self, "Consolidate", "End date must be on or after start."
            )
            return
        approved_only = True
        if hasattr(self, "_lib_approved_only"):
            approved_only = self._lib_approved_only.isChecked()
        try:
            payload, meta = await download_consolidate_pack(
                date_from=d0.isoformat(),
                date_to=d1.isoformat(),
                item=self._lib_item.text().strip(),
                payee=self._lib_payee.text().strip(),
                ownership=self._lib_own.text().strip(),
                approved_only=approved_only,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Consolidate failed", str(exc))
            return

        default_name = (
            meta.get("filename")
            or f"tahmeed-receipts_{d0.isoformat()}_{d1.isoformat()}.zip"
        )
        summary = (
            f"Rows: {meta.get('rows') or '?'}\n"
            f"With file: {meta.get('with_file') or '?'}\n"
            f"Without file: {meta.get('without_file') or '?'}\n"
            f"Files in pack: {meta.get('files') or '?'}\n"
            f"Missing on disk: {meta.get('missing') or '0'}\n"
            f"SHA-256: {meta.get('sha256') or ''}"
        )

        if to_folder:
            dest = QFileDialog.getExistingDirectory(self, "Save consolidated receipts to folder")
            if not dest:
                return
            out_dir = Path(dest) / Path(default_name).stem
            out_dir.mkdir(parents=True, exist_ok=True)
            zip_path = out_dir / default_name
            zip_path.write_bytes(payload)
            try:
                import zipfile

                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(out_dir)
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Consolidate",
                    f"ZIP saved but extract failed:\n{zip_path}\n\n{exc}",
                )
                return
            self._status.setText(
                f"Consolidated to {out_dir} · files={meta.get('files') or '?'}"
            )
            QMessageBox.information(
                self,
                "Consolidate",
                f"Saved and extracted to:\n{out_dir}\n\n{summary}",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save consolidated receipts ZIP", default_name, "ZIP files (*.zip)"
        )
        if not path:
            return
        out = Path(path)
        if out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        out.write_bytes(payload)
        self._status.setText(
            f"Saved {out.name} · files={meta.get('files') or '?'} · "
            f"sha={(meta.get('sha256') or '')[:12]}…"
        )
        QMessageBox.information(
            self,
            "Consolidate",
            f"Saved to:\n{out}\n\n{summary}",
        )

    def _selected_tx_ids(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for item in self._candidates.selectedItems():
            row = item.data(Qt.UserRole) or {}
            tid = str(row.get("id") or "")
            if tid and tid not in seen:
                seen.add(tid)
                ids.append(tid)
        return ids

    async def _mark_selected_requests(self) -> None:
        ids = self._selected_request_ids()
        if not ids:
            QMessageBox.information(self, "Receipts", "Select one or more request rows.")
            return
        try:
            updated = await mark_reminded(request_ids=ids)
            self._status.setText(f"Reminded {updated} selected request(s)")
            await self._load_requests()
            await self._load_compliance()
            await self._load_chase()
        except Exception as exc:
            QMessageBox.critical(self, "Receipts", str(exc))

    def _go_link_from_requests(self) -> None:
        tx_ids = self._selected_request_tx_ids()
        if not tx_ids:
            QMessageBox.information(
                self,
                "Receipts",
                "Select one or more request rows that have a linked expense row, "
                "then pick Inbox file(s) on Link.",
            )
            return
        # Remember desired txs so Link can auto-highlight after Find.
        self._pending_link_tx_ids = set(tx_ids)
        self._tabs.setCurrentIndex(3)  # Link
        self._link_needed.setChecked(True)
        asyncio.ensure_future(self._load_candidates_and_select_pending())

    async def _load_candidates_and_select_pending(self) -> None:
        await self._load_candidates()
        pending = getattr(self, "_pending_link_tx_ids", None) or set()
        if not pending or not hasattr(self, "_candidates"):
            return
        self._candidates.clearSelection()
        for r in range(self._candidates.rowCount()):
            item = self._candidates.item(r, 0)
            row = (item.data(Qt.UserRole) if item else None) or {}
            tid = str(row.get("id") or "")
            if tid in pending:
                self._candidates.selectRow(r)
        n = len(self._selected_tx_ids())
        self._status.setText(
            f"Link: highlighted {n} row(s) from Requests — pick Inbox file(s) then Link selected"
        )

    async def _upload_for_selected_requests(self, *, partial: bool) -> None:
        """Park files then link them to every selected request's transaction (on behalf)."""
        tx_ids = self._selected_request_tx_ids()
        if not tx_ids:
            QMessageBox.information(
                self,
                "Receipts",
                "Select one or more open request rows first "
                "(Ctrl/Shift click), then Upload.",
            )
            return
        note = ""
        if partial:
            note, ok = QInputDialog.getText(
                self,
                "Partial upload",
                "Note for partial cover (required), e.g. covers fuel only:",
            )
            if not ok:
                return
            note = (note or "").strip()
            if len(note) < 3:
                QMessageBox.information(
                    self, "Receipts", "Enter a short partial note (3+ characters)."
                )
                return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Upload receipt(s) for selected request(s)",
            "",
            "Receipts (*.pdf *.png *.jpg *.jpeg *.heic *.webp *.doc *.docx);;All (*.*)",
        )
        if not paths:
            return
        try:
            result = await upload_parked_files(paths)
            uploaded = list(result.get("files") or result.get("items") or [])
            file_ids = [
                str(f.get("id") or "")
                for f in uploaded
                if isinstance(f, dict) and f.get("id")
            ]
            if not file_ids:
                # Fallback: some APIs return only total — reload inbox parked and take newest.
                await self._load_inbox()
                QMessageBox.warning(
                    self,
                    "Receipts",
                    "Files parked but no file ids returned. "
                    "Open Inbox → select the new file(s) → Link selected…",
                )
                self._tabs.setCurrentIndex(2)
                return
            link_result = await link_files(
                file_ids, tx_ids, partial=partial, partial_note=note
            )
            kind = "partial" if partial else "full"
            self._status.setText(
                f"On-behalf {kind} upload: "
                f"{link_result.get('linked')} link(s) · "
                f"{len(file_ids)} file(s) × {len(tx_ids)} row(s)"
            )
            await self.refresh_all()
            self._refresh_request_sel_hint()
        except Exception as exc:
            QMessageBox.critical(self, "Upload failed", str(exc))

    def _copy_request_line(self) -> None:
        rows = self._selected_request_rows()
        if not rows:
            QMessageBox.information(self, "Receipts", "Select a request row first.")
            return
        lines = []
        for row in rows:
            line = (
                f"{row.get('ownership')} | {row.get('status')} | "
                f"{row.get('item')} | {_fmt_age(row.get('age_hours'))} | "
                f"reminders={row.get('reminder_count')}"
            )
            if line not in lines:
                lines.append(line)
        QGuiApplication.clipboard().setText("\n".join(lines))
        self._status.setText("Request line(s) copied")

    async def _mark_selected_chase(self) -> None:
        row = self._selected_chase()
        if not row:
            QMessageBox.information(self, "Receipts", "Select an ownership group first.")
            return
        try:
            updated = await mark_reminded(
                ownership=str(row.get("ownership") or ""),
                request_ids=list(row.get("request_ids") or []),
            )
            self._status.setText(f"Marked reminded ({updated})")
            await self._load_chase()
            await self._load_compliance()
            await self._load_requests()
        except Exception as exc:
            QMessageBox.critical(self, "Receipts", str(exc))

    def _copy_chase_line(self) -> None:
        row = self._selected_chase()
        if not row:
            QMessageBox.information(self, "Receipts", "Select an ownership group first.")
            return
        line = (
            f"{row.get('ownership')} | open={row.get('open_count')} | "
            f"oldest={_fmt_age(row.get('oldest_age_hours'))} | "
            f"last_reminded={_fmt_when(row.get('last_reminded_at'))} | "
            f"reminders={row.get('reminder_count')}"
        )
        QGuiApplication.clipboard().setText(line)
        self._status.setText("Chase line copied")

    async def _approve_selected(self) -> None:
        ids = self._selected_file_ids()
        if not ids:
            QMessageBox.information(self, "Receipts", "Select one or more inbox files.")
            return
        try:
            result = await approve_files(ids)
            self._status.setText(f"Approved {result.get('updated')} file(s)")
            await self._load_inbox()
            await self._load_compliance()
        except Exception as exc:
            QMessageBox.critical(self, "Approve failed", str(exc))

    async def _reject_selected(self) -> None:
        ids = self._selected_file_ids()
        if not ids:
            QMessageBox.information(self, "Receipts", "Select one or more inbox files.")
            return
        reason, ok = QInputDialog.getText(
            self, "Reject receipt file", "Reason (required):"
        )
        if not ok:
            return
        if len((reason or "").strip()) < 3:
            QMessageBox.information(self, "Receipts", "Enter a short rejection reason.")
            return
        try:
            result = await reject_files(ids, reason.strip())
            self._status.setText(f"Rejected {result.get('updated')} file(s)")
            await self._load_inbox()
            await self._load_compliance()
        except Exception as exc:
            QMessageBox.critical(self, "Reject failed", str(exc))

    async def _do_link(self) -> None:
        file_ids = self._selected_file_ids()
        tx_ids = self._selected_tx_ids()
        if not file_ids:
            QMessageBox.information(self, "Receipts", "Select inbox file(s) on the Inbox tab.")
            self._tabs.setCurrentIndex(2)
            return
        if not tx_ids:
            QMessageBox.information(self, "Receipts", "Select one or more rows below.")
            return
        partial = self._partial_check.isChecked()
        note = self._partial_note.text().strip()
        if partial and len(note) < 3:
            QMessageBox.information(
                self, "Receipts", "Partial link needs a short note (e.g. covers fuel only)."
            )
            return
        try:
            result = await link_files(
                file_ids, tx_ids, partial=partial, partial_note=note
            )
            kind = "partial" if result.get("partial") else "full"
            self._status.setText(
                f"Linked {result.get('linked')} {kind} connection(s) "
                f"({result.get('files')} file(s) × {result.get('transactions')} row(s))"
            )
            await self.refresh_all()
            await self._load_candidates()
        except Exception as exc:
            QMessageBox.critical(self, "Link failed", str(exc))

    async def _do_export(self) -> None:
        d0 = self._pack_from.date().toPython()
        d1 = self._pack_to.date().toPython()
        if d1 < d0:
            QMessageBox.information(self, "Evidence pack", "End date must be on or after start.")
            return
        default = f"tahmeed-evidence_{d0.isoformat()}_{d1.isoformat()}.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save evidence pack", default, "ZIP files (*.zip)"
        )
        if not path:
            return
        out = Path(path)
        if out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        try:
            payload, meta = await download_evidence_pack(
                date_from=d0.isoformat(),
                date_to=d1.isoformat(),
                item=self._pack_item.text().strip(),
                payee=self._pack_payee.text().strip(),
                ownership=self._pack_own.text().strip(),
                approved_only=self._pack_approved_only.isChecked(),
            )
            out.write_bytes(payload)
            sha = meta.get("sha256") or ""
            self._status.setText(f"Saved {out.name} · sha={sha[:12]}…")
            await self._load_exports()
            QMessageBox.information(
                self,
                "Evidence pack",
                f"Saved to:\n{out}\n\nRows: {meta.get('rows')}\nSHA-256: {sha}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Evidence pack failed", str(exc))
