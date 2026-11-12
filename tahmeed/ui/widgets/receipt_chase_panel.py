"""Receipts desk — Main day overview, requests, inbox, link, evidence pack."""

from __future__ import annotations

import asyncio
import calendar
import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any

import qtawesome as qta
from PySide6.QtCore import QDate, Qt, QSize, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication
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
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.ui.accountant.date_filters import style_calendar_popup
from tahmeed.ui.widgets.combo_styles import with_combo_popup
from tahmeed.ui.widgets.popup_styles import apply_light_menu, with_light_popups

from tahmeed.services.receipt_chase_service import (
    approve_files,
    download_consolidate_pack,
    download_evidence_pack,
    get_compliance,
    link_files,
    list_chase,
    list_evidence_exports,
    list_inbox,
    list_receipt_day_entries,
    list_receipt_day_summaries,
    list_receipt_entries,
    list_receipt_library,
    list_requests,
    mark_reminded,
    reject_files,
    search_link_candidates,
    upload_parked_files,
)
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.excel_column_filter import (
    SORT_ASC,
    ExcelFilterHeaderView,
    cascade_column_values,
)
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading
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
# Tab pages: QWidget background alone makes OS-dark QToolTip black-on-black.
_PAGE_CARD_SS = with_light_popups(f"QWidget {{ background: {_CARD}; }}")
_CARD_SS = (
    f"QFrame#kpiCard {{ background:{_CARD}; border:1px solid {_BORDER}; "
    f"border-radius:8px; }}"
)
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

_REQUEST_PRESETS: tuple[tuple[str, str], ...] = (
    ("Today", "today"),
    ("Open", "open"),
    ("Overdue", "overdue"),
    ("Reminded+", "heavy_reminders"),
    ("Received", "received"),
    ("Waived", "waived"),
    ("All", "all"),
)

# Main day-summary table (Saved-style)
# S/NO | Date | Entries | Needed | Partial | Collected | Waived | No receipt | Multi-file | Shared | Open
_MAIN_COL_DEFAULTS = [44, 118, 70, 70, 70, 80, 70, 90, 80, 70, 72]
_MAIN_COL_KEY = "receipt_main_days_v2"
_MAIN_COL_DATE = 1
_MAIN_COL_OPEN = 10
_MAIN_PAGE_SIZE = 40
_MAIN_YEAR_LOOKBACK = 12
# Match Master Expenses / feed tables (compact ledger density).
_ROW_H = 24
_ROW_BTN_H = 20
_MAIN_ANY_DATE = QDate(2000, 1, 1)
_MAIN_MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MAIN_EXPORT_HEADERS = [
    "Date",
    "Entries",
    "Needed",
    "Partial",
    "Collected",
    "Waived",
    "No receipt",
    "Multi-file",
    "Shared",
]
_MAIN_FILTER_FIELD_SS = with_combo_popup(
    f"QComboBox,QDateEdit,QLineEdit{{border:1px solid {_BORDER};border-radius:4px;"
    f"padding:0 8px;background:#FFFFFF;font:12px 'Segoe UI';color:{_TEXT};"
    "min-height:28px;max-height:28px;}}"
    f"QComboBox:focus,QDateEdit:focus,QLineEdit:focus{{border-color:{_ACCENT};}}"
)

# Day-drill entries: Truck → Description → Item → Payee → …
# S/NO | Truck | Description | Item | Payee | Ownership | Amount | Status | Open
_ENTRY_HEADERS = [
    "S/NO",
    "Truck",
    "Description",
    "Item",
    "Payee",
    "Ownership",
    "Amount",
    "Status",
    "",
]
_ENTRY_COL_DEFAULTS = [44, 90, 180, 130, 120, 100, 110, 90, 72]
_ENTRY_COL_KEY = "receipt_main_day_entries_v2"
_ENTRY_COL_OPEN = 8
_ENTRY_FILTERABLE = set(range(1, _ENTRY_COL_OPEN))
_ENTRY_SORT_KINDS = {1: "truck", 6: "number"}

# View all: Date + same entry columns
_VIEW_ALL_HEADERS = [
    "S/NO",
    "Date",
    "Truck",
    "Description",
    "Item",
    "Payee",
    "Ownership",
    "Amount",
    "Status",
    "",
]
_VIEW_ALL_COL_DEFAULTS = [44, 110, 90, 170, 120, 110, 100, 110, 90, 72]
_VIEW_ALL_COL_KEY = "receipt_view_all_entries_v2"
_VIEW_ALL_COL_OPEN = 9
_VIEW_ALL_FILTERABLE = set(range(1, _VIEW_ALL_COL_OPEN))
_VIEW_ALL_SORT_KINDS = {1: "date", 2: "truck", 7: "number"}
_VIEW_ALL_LIMIT = 500

_VIEW_ALL_STATUS_PRESETS: tuple[tuple[str, str], ...] = (
    ("All", "all"),
    ("Needed", "needed"),
    ("Received", "received"),
    ("Partial", "partial"),
    ("Waived", "waived"),
    ("No receipt", "no_receipt"),
)

_STATUS_FG: dict[str, str] = {
    "needed": _WARN,
    "received": _OK,
    "partial": _ACCENT,
    "waived": _MUTED,
    "no_receipt": _MUTED,
    "pending": _MUTED,
}


def _parse_iso_day(raw: Any) -> date | None:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _fmt_main_day(day: date) -> str:
    return day.strftime("%d %b %Y")


def _filter_main_day_rows(
    rows: list[dict[str, Any]], query: str
) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return list(rows)
    matched: list[dict[str, Any]] = []
    for row in rows:
        day = _parse_iso_day(row.get("date"))
        hay = " ".join(
            [
                day.strftime("%d %b %Y") if day else "",
                day.isoformat() if day else str(row.get("date") or ""),
                str(row.get("entries_count") or 0),
                str(row.get("needed_count") or 0),
                str(row.get("partial_count") or 0),
                str(row.get("collected_count") or 0),
                str(row.get("waived_count") or 0),
                str(row.get("no_receipt_count") or 0),
                str(row.get("multi_file_count") or 0),
                str(row.get("shared_file_count") or 0),
            ]
        ).lower()
        if q in hay:
            matched.append(row)
    return matched


def _main_filter_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(
        f"font-size:11px;color:{_MUTED};font-weight:500;background:transparent;"
    )
    return lab


def _main_filter_field(label: str, widget: QWidget) -> QVBoxLayout:
    vl = QVBoxLayout()
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(2)
    vl.addWidget(_main_filter_label(label))
    vl.addWidget(widget)
    return vl


def _main_optional_date_edit() -> QDateEdit:
    """Date edit whose special minimum value reads Any (no bound)."""
    de = QDateEdit()
    de.setCalendarPopup(True)
    de.setDisplayFormat("dd/MM/yyyy")
    de.setFixedHeight(28)
    de.setMinimumWidth(110)
    de.setMaximumWidth(120)
    de.setMinimumDate(_MAIN_ANY_DATE)
    de.setSpecialValueText("Any")
    de.setDate(_MAIN_ANY_DATE)
    de.setStyleSheet(_MAIN_FILTER_FIELD_SS)
    style_calendar_popup(de)
    return de


def _main_optional_date(de: QDateEdit) -> date | None:
    qd = de.date()
    if qd == _MAIN_ANY_DATE:
        return None
    return date(qd.year(), qd.month(), qd.day())


def _main_export_row(row: dict[str, Any]) -> list[Any]:
    day = _parse_iso_day(row.get("date"))
    date_label = _fmt_main_day(day) if day else str(row.get("date") or "")
    return [
        date_label,
        int(row.get("entries_count") or 0),
        int(row.get("needed_count") or 0),
        int(row.get("partial_count") or 0),
        int(row.get("collected_count") or 0),
        int(row.get("waived_count") or 0),
        int(row.get("no_receipt_count") or 0),
        int(row.get("multi_file_count") or 0),
        int(row.get("shared_file_count") or 0),
    ]


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


def _row_action_btn(text: str, icon: str) -> QPushButton:
    """Compact ghost button for table cells (fits ``_ROW_H``)."""
    btn = QPushButton(text)
    btn.setIcon(qta.icon(icon, color="#374151"))
    btn.setIconSize(QSize(12, 12))
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(_ROW_BTN_H)
    btn.setStyleSheet(
        with_light_popups(
            f"QPushButton{{background:{_CARD};color:{_TEXT};border:1px solid {_BORDER};"
            "border-radius:3px;padding:0 6px;font:600 11px 'Segoe UI';}}"
            "QPushButton:hover{background:#F8FAFC;}"
        )
    )
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

    # Emitted when a Main day row is opened (Phase 4 wires the entry drill).
    day_opened = Signal(object)  # date

    def __init__(self, parent=None, *, can_export: bool = False) -> None:
        super().__init__(parent)
        self.setStyleSheet(with_light_popups(_PAGE_SS + _INPUT_SS))
        self._can_export = bool(can_export)
        self._preset = "open"
        self._request_rows: list[dict[str, Any]] = []
        self._chase_rows: list[dict[str, Any]] = []
        self._inbox_rows: list[dict[str, Any]] = []
        self._library_rows: list[dict[str, Any]] = []
        self._tx_rows: list[dict[str, Any]] = []
        self._export_rows: list[dict[str, Any]] = []
        self._main_day_rows: list[dict[str, Any]] = []
        self._main_visible: list[dict[str, Any]] = []
        self._main_filter = ""
        self._main_has_more = False
        self._main_total = 0
        self._main_scroll_loading = False
        self._main_period_syncing = False
        self._main_drill_day: date | None = None
        self._main_entry_rows: list[dict[str, Any]] = []
        self._main_entry_visible: list[dict[str, Any]] = []
        self._main_entry_filters: dict[int, set[str]] = {}
        self._main_entry_sort: tuple[int | None, bool] = (None, True)
        self._view_all_rows: list[dict[str, Any]] = []
        self._view_all_visible: list[dict[str, Any]] = []
        self._view_all_status = "all"
        self._view_all_filters: dict[int, set[str]] = {}
        self._view_all_sort: tuple[int | None, bool] = (None, True)
        self._build()
        self.day_opened.connect(self._on_day_opened)
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
            "Main: one row per register day — Needed, Partial, Collected, Waived, "
            "No receipt, Multi-file, Shared. View all and chase tools stay on other tabs."
            + (
                " Evidence pack + TRA/KRA consolidate under Receipt storage."
                if self._can_export
                else ""
            )
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
        self._tabs.addTab(self._build_main_tab(), "Main")
        self._tabs.addTab(self._build_view_all_tab(), "View all")
        self._tabs.addTab(self._build_requests_tab(), "Requests")
        self._tabs.addTab(self._build_chase_tab(), "Chase")
        self._tabs.addTab(self._build_inbox_tab(), "Inbox")
        self._tabs.addTab(self._build_link_tab(), "Link")
        self._tabs.addTab(self._build_library_tab(), "Library")
        if self._can_export:
            self._tabs.addTab(self._build_export_tab(), "Evidence pack")
        self._tabs.setCurrentIndex(0)
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
        vh = table.verticalHeader()
        vh.setVisible(False)
        vh.setMinimumSectionSize(18)
        vh.setDefaultSectionSize(_ROW_H)
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
        table.setRowHeight(row_idx, _ROW_H)

    def _build_main_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(_PAGE_CARD_SS)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        self._main_stack = QStackedWidget()
        self._main_stack.setStyleSheet("background:transparent;")

        list_page = QWidget()
        list_lay = QVBoxLayout(list_page)
        list_lay.setContentsMargins(0, 0, 0, 0)
        list_lay.setSpacing(8)

        # Browse-style compact filter strip (labels above controls)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        toolbar.setAlignment(Qt.AlignBottom)

        self._main_year = QComboBox()
        self._main_year.setFixedHeight(28)
        self._main_year.setMinimumWidth(78)
        self._main_year.setMaximumWidth(96)
        self._main_year.setStyleSheet(_MAIN_FILTER_FIELD_SS)
        self._fill_main_year_options()
        self._main_year.currentIndexChanged.connect(self._on_main_period_changed)
        toolbar.addLayout(_main_filter_field("Year", self._main_year))

        self._main_month = QComboBox()
        self._main_month.setFixedHeight(28)
        self._main_month.setMinimumWidth(110)
        self._main_month.setMaximumWidth(130)
        self._main_month.setStyleSheet(_MAIN_FILTER_FIELD_SS)
        self._fill_main_month_options()
        self._main_month.currentIndexChanged.connect(self._on_main_period_changed)
        toolbar.addLayout(_main_filter_field("Month", self._main_month))

        self._main_from = _main_optional_date_edit()
        toolbar.addLayout(_main_filter_field("Date From", self._main_from))
        self._main_to = _main_optional_date_edit()
        toolbar.addLayout(_main_filter_field("Date To", self._main_to))

        toolbar.addStretch(1)

        self._main_search = QLineEdit()
        self._main_search.setPlaceholderText("Search keyword…")
        self._main_search.setClearButtonEnabled(True)
        self._main_search.setFixedHeight(28)
        self._main_search.setMinimumWidth(150)
        self._main_search.setMaximumWidth(220)
        self._main_search.setStyleSheet(_MAIN_FILTER_FIELD_SS)
        self._main_search.returnPressed.connect(self._on_main_find)
        self._main_search.textChanged.connect(self._on_main_filter_changed)
        toolbar.addLayout(_main_filter_field("Search", self._main_search))

        find_btn = _primary_btn("Find", "mdi.magnify")
        find_btn.setFixedHeight(28)
        find_btn.clicked.connect(self._on_main_find)
        reset_btn = _ghost_btn("Reset", "mdi.refresh")
        reset_btn.setFixedHeight(28)
        reset_btn.clicked.connect(self._on_main_reset)
        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(2)
        btn_col.addWidget(_main_filter_label(""))
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(4)
        btn_row.addWidget(find_btn)
        btn_row.addWidget(reset_btn)
        btn_col.addLayout(btn_row)
        toolbar.addLayout(btn_col)

        export_btn = _ghost_btn("Export", "mdi.download-outline")
        export_btn.setFixedHeight(28)
        export_menu = apply_light_menu(QMenu(export_btn))
        for label, fmt in (
            ("Excel (.xlsx)", "xlsx"),
            ("CSV (.csv)", "csv"),
            ("PDF (.pdf)", "pdf"),
        ):
            act = QAction(label, self)
            act.triggered.connect(
                lambda *_a, f=fmt: asyncio.ensure_future(self._export_main_days(f))
            )
            export_menu.addAction(act)
        export_btn.setMenu(export_menu)
        export_col = QVBoxLayout()
        export_col.setContentsMargins(0, 0, 0, 0)
        export_col.setSpacing(2)
        export_col.addWidget(_main_filter_label(""))
        export_col.addWidget(export_btn)
        toolbar.addLayout(export_col)

        hint_col = QVBoxLayout()
        hint_col.setContentsMargins(0, 0, 0, 0)
        hint_col.setSpacing(2)
        hint_col.addWidget(_main_filter_label(""))
        self._main_page_hint = QLabel("")
        self._main_page_hint.setStyleSheet(
            f"color:{_MUTED}; font:11px 'Segoe UI'; background:transparent;"
        )
        hint_col.addWidget(self._main_page_hint)
        toolbar.addLayout(hint_col)

        list_lay.addLayout(toolbar)
        list_lay.addSpacing(8)

        self._main_empty = QLabel(
            "No register days with receipt-scoped entries yet.\n"
            "Days appear here once expenses are saved."
        )
        self._main_empty.setAlignment(Qt.AlignCenter)
        self._main_empty.setStyleSheet(
            f"color:{_MUTED}; font:13px 'Segoe UI'; background:transparent; padding:32px;"
        )
        self._main_empty.setWordWrap(True)
        list_lay.addWidget(self._main_empty)

        self._main_days = QTableWidget(0, len(_MAIN_COL_DEFAULTS))
        self._main_days.setHorizontalHeaderLabels(
            [
                "S/NO",
                "Date",
                "Entries",
                "Needed",
                "Partial",
                "Collected",
                "Waived",
                "No receipt",
                "Multi-file",
                "Shared",
                "",
            ]
        )
        self._style_table(self._main_days)
        self._main_days.setSelectionMode(QAbstractItemView.SingleSelection)
        hdr = self._main_days.horizontalHeader()
        hdr.setStretchLastSection(False)
        for col, width in enumerate(_MAIN_COL_DEFAULTS):
            self._main_days.setColumnWidth(col, width)
            if col in (0, _MAIN_COL_OPEN):
                hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            else:
                hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._main_days,
            _MAIN_COL_KEY,
            _MAIN_COL_DEFAULTS,
            auto_fit_if_unset=True,
        )
        self._main_days.cellDoubleClicked.connect(self._on_main_day_double_clicked)
        self._main_days.verticalScrollBar().valueChanged.connect(
            self._on_main_days_scroll
        )
        list_lay.addWidget(self._main_days, 1)
        self._main_stack.addWidget(list_page)

        drill_page = QWidget()
        drill_lay = QVBoxLayout(drill_page)
        drill_lay.setContentsMargins(0, 0, 0, 0)
        drill_lay.setSpacing(8)
        drill_bar = QHBoxLayout()
        self._main_back_btn = _ghost_btn("Back", "mdi.arrow-left")
        self._main_back_btn.clicked.connect(self._leave_main_drill)
        drill_bar.addWidget(self._main_back_btn)
        self._main_drill_title = QLabel("Day entries")
        self._main_drill_title.setStyleSheet(
            f"color:{_TEXT}; font:600 14px 'Segoe UI'; background:transparent;"
        )
        drill_bar.addWidget(self._main_drill_title, 1)
        drill_hint = QLabel(
            "Truck → Description → Payee · header ▾ filters/sort · Open receipt"
        )
        drill_hint.setStyleSheet(
            f"color:{_MUTED}; font:11px 'Segoe UI'; background:transparent;"
        )
        drill_bar.addWidget(drill_hint)
        self._main_entry_open_btn = _ghost_btn("Open receipt", "mdi.file-eye-outline")
        self._main_entry_open_btn.clicked.connect(self._open_selected_main_entry_receipt)
        drill_bar.addWidget(self._main_entry_open_btn)
        drill_lay.addLayout(drill_bar)

        self._main_entries_empty = QLabel("No entries for this day.")
        self._main_entries_empty.setAlignment(Qt.AlignCenter)
        self._main_entries_empty.setStyleSheet(
            f"color:{_MUTED}; font:13px 'Segoe UI'; background:transparent; padding:24px;"
        )
        self._main_entries_empty.setVisible(False)
        drill_lay.addWidget(self._main_entries_empty)

        self._main_entries = QTableWidget(0, len(_ENTRY_COL_DEFAULTS))
        self._main_entries.setHorizontalHeaderLabels(list(_ENTRY_HEADERS))
        self._style_table(self._main_entries)
        self._main_entries.setSelectionMode(QAbstractItemView.SingleSelection)
        self._install_entry_filter_header(
            self._main_entries,
            filterable=_ENTRY_FILTERABLE,
            sort_kinds=_ENTRY_SORT_KINDS,
            labels=_ENTRY_HEADERS,
            on_filter=self._on_main_entry_filter_changed,
            on_sort=self._on_main_entry_sort,
            value_provider=self._main_entry_filter_values,
        )
        entry_hdr = self._main_entries.horizontalHeader()
        entry_hdr.setStretchLastSection(False)
        entry_hdr.setSortIndicatorShown(True)
        for col, width in enumerate(_ENTRY_COL_DEFAULTS):
            self._main_entries.setColumnWidth(col, width)
            if col in (0, _ENTRY_COL_OPEN):
                entry_hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            else:
                entry_hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._main_entries,
            _ENTRY_COL_KEY,
            _ENTRY_COL_DEFAULTS,
            auto_fit_if_unset=True,
        )
        self._main_entries.cellDoubleClicked.connect(
            self._on_main_entry_double_clicked
        )
        drill_lay.addWidget(self._main_entries, 1)
        self._main_stack.addWidget(drill_page)

        lay.addWidget(self._main_stack, 1)
        return page

    def _build_view_all_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(_PAGE_CARD_SS)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(0)

        toolbar_h = 28
        lab_ss = (
            f"color:{_MUTED}; font:12px 'Segoe UI'; background:transparent;"
        )
        date_ss = (
            f"QDateEdit{{border:1px solid {_BORDER};border-radius:4px;"
            f"padding:0 8px;background:#FFFFFF;font:12px 'Segoe UI';"
            f"color:{_TEXT};min-height:{toolbar_h}px;max-height:{toolbar_h}px;}}"
        )

        # One row — range, status chips, actions (like Requests)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        toolbar.setAlignment(Qt.AlignVCenter)
        from_lab = QLabel("From")
        from_lab.setStyleSheet(lab_ss)
        toolbar.addWidget(from_lab, 0, Qt.AlignVCenter)
        self._view_all_from = QDateEdit()
        self._view_all_from.setCalendarPopup(True)
        self._view_all_from.setDisplayFormat("yyyy-MM-dd")
        self._view_all_from.setFixedHeight(toolbar_h)
        self._view_all_from.setStyleSheet(date_ss)
        today = QDate.currentDate()
        month_start = QDate(today.year(), today.month(), 1)
        self._view_all_from.setDate(month_start)
        toolbar.addWidget(self._view_all_from, 0, Qt.AlignVCenter)
        to_lab = QLabel("To")
        to_lab.setStyleSheet(lab_ss)
        toolbar.addWidget(to_lab, 0, Qt.AlignVCenter)
        self._view_all_to = QDateEdit()
        self._view_all_to.setCalendarPopup(True)
        self._view_all_to.setDisplayFormat("yyyy-MM-dd")
        self._view_all_to.setFixedHeight(toolbar_h)
        self._view_all_to.setStyleSheet(date_ss)
        self._view_all_to.setDate(today)
        toolbar.addWidget(self._view_all_to, 0, Qt.AlignVCenter)
        apply_btn = _primary_btn("Apply", "mdi.filter-outline")
        apply_btn.setFixedHeight(toolbar_h)
        apply_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._load_view_all_entries())
        )
        toolbar.addWidget(apply_btn, 0, Qt.AlignVCenter)

        self._view_all_status_group = QButtonGroup(self)
        self._view_all_status_group.setExclusive(True)
        self._view_all_status_buttons: dict[str, QPushButton] = {}
        for label, key in _VIEW_ALL_STATUS_PRESETS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(toolbar_h)
            btn.setChecked(key == "all")
            btn.setStyleSheet(_chip_style(key == "all"))
            btn.clicked.connect(
                lambda *_a, k=key: self._on_view_all_status(k)
            )
            self._view_all_status_group.addButton(btn)
            self._view_all_status_buttons[key] = btn
            toolbar.addWidget(btn, 0, Qt.AlignVCenter)

        toolbar.addStretch(1)
        self._view_all_open_btn = _ghost_btn("Open receipt", "mdi.file-eye-outline")
        self._view_all_open_btn.setFixedHeight(toolbar_h)
        self._view_all_open_btn.clicked.connect(self._open_selected_view_all_receipt)
        toolbar.addWidget(self._view_all_open_btn, 0, Qt.AlignVCenter)
        self._view_all_count = QLabel("")
        self._view_all_count.setStyleSheet(
            f"color:{_MUTED}; font:11px 'Segoe UI'; background:transparent;"
        )
        toolbar.addWidget(self._view_all_count, 0, Qt.AlignVCenter)
        lay.addLayout(toolbar)
        lay.addSpacing(8)

        self._view_all_empty = QLabel(
            "No entries in this date range.\nAdjust From / To or status filter."
        )
        self._view_all_empty.setAlignment(Qt.AlignCenter)
        self._view_all_empty.setWordWrap(True)
        self._view_all_empty.setStyleSheet(
            f"color:{_MUTED}; font:13px 'Segoe UI'; background:transparent; padding:24px;"
        )
        self._view_all_empty.setVisible(False)
        lay.addWidget(self._view_all_empty)

        self._view_all_entries = QTableWidget(0, len(_VIEW_ALL_COL_DEFAULTS))
        self._view_all_entries.setHorizontalHeaderLabels(list(_VIEW_ALL_HEADERS))
        self._style_table(self._view_all_entries)
        self._view_all_entries.setSelectionMode(QAbstractItemView.SingleSelection)
        self._install_entry_filter_header(
            self._view_all_entries,
            filterable=_VIEW_ALL_FILTERABLE,
            sort_kinds=_VIEW_ALL_SORT_KINDS,
            labels=_VIEW_ALL_HEADERS,
            on_filter=self._on_view_all_filter_changed,
            on_sort=self._on_view_all_sort,
            value_provider=self._view_all_filter_values,
        )
        hdr = self._view_all_entries.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSortIndicatorShown(True)
        for col, width in enumerate(_VIEW_ALL_COL_DEFAULTS):
            self._view_all_entries.setColumnWidth(col, width)
            if col in (0, _VIEW_ALL_COL_OPEN):
                hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            else:
                hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._view_all_entries,
            _VIEW_ALL_COL_KEY,
            _VIEW_ALL_COL_DEFAULTS,
            auto_fit_if_unset=True,
        )
        self._view_all_entries.cellDoubleClicked.connect(
            self._on_view_all_entry_double_clicked
        )
        lay.addWidget(self._view_all_entries, 1)
        return page

    def _install_entry_filter_header(
        self,
        table: QTableWidget,
        *,
        filterable: set[int],
        sort_kinds: dict[int, str],
        labels: list[str],
        on_filter,
        on_sort,
        value_provider,
    ) -> None:
        filter_hdr = ExcelFilterHeaderView(
            table,
            filterable_columns=filterable,
            sort_kinds=sort_kinds,
        )
        filter_hdr.set_value_provider(value_provider)
        filter_hdr.set_label_provider(
            lambda c, labs=labels: labs[c] if 0 <= c < len(labs) else ""
        )
        filter_hdr.filter_changed.connect(on_filter)
        filter_hdr.sort_requested.connect(on_sort)
        table.setHorizontalHeader(filter_hdr)

    def _fill_main_year_options(self) -> None:
        self._main_year.blockSignals(True)
        self._main_year.clear()
        self._main_year.addItem("All years", None)
        y = date.today().year
        for _ in range(_MAIN_YEAR_LOOKBACK):
            self._main_year.addItem(str(y), y)
            y -= 1
        self._main_year.setCurrentIndex(0)
        self._main_year.blockSignals(False)

    def _fill_main_month_options(self) -> None:
        self._main_month.blockSignals(True)
        self._main_month.clear()
        self._main_month.addItem("All months", None)
        for m in range(1, 13):
            self._main_month.addItem(_MAIN_MONTH_NAMES[m], m)
        self._main_month.setCurrentIndex(0)
        self._main_month.blockSignals(False)

    def _main_active_date_bounds(self) -> tuple[str, str]:
        d_from = _main_optional_date(self._main_from)
        d_to = _main_optional_date(self._main_to)
        return (
            d_from.isoformat() if d_from else "",
            d_to.isoformat() if d_to else "",
        )

    def _main_has_active_date_filter(self) -> bool:
        date_from, date_to = self._main_active_date_bounds()
        return bool(date_from or date_to)

    def _apply_main_period_to_dates(self) -> None:
        """Sync Date From/To from Year/Month (All/All → Any)."""
        year = self._main_year.currentData()
        month = self._main_month.currentData()
        self._main_period_syncing = True
        try:
            if year is None and month is None:
                self._main_from.setDate(_MAIN_ANY_DATE)
                self._main_to.setDate(_MAIN_ANY_DATE)
                return
            y = int(year) if year is not None else date.today().year
            if month is None:
                self._main_from.setDate(QDate(y, 1, 1))
                self._main_to.setDate(QDate(y, 12, 31))
                return
            m = int(month)
            last = calendar.monthrange(y, m)[1]
            self._main_from.setDate(QDate(y, m, 1))
            self._main_to.setDate(QDate(y, m, last))
        finally:
            self._main_period_syncing = False

    def _on_main_period_changed(self, _index: int = 0) -> None:
        if self._main_period_syncing:
            return
        self._apply_main_period_to_dates()
        asyncio.ensure_future(self._load_main_days(reset=True))

    def _on_main_find(self) -> None:
        self._main_filter = self._main_search.text() or ""
        asyncio.ensure_future(self._load_main_days(reset=True))

    def _on_main_reset(self) -> None:
        self._main_period_syncing = True
        try:
            self._main_year.setCurrentIndex(0)
            self._main_month.setCurrentIndex(0)
            self._main_from.setDate(_MAIN_ANY_DATE)
            self._main_to.setDate(_MAIN_ANY_DATE)
            self._main_search.clear()
        finally:
            self._main_period_syncing = False
        self._main_filter = ""
        asyncio.ensure_future(self._load_main_days(reset=True))

    def _on_main_filter_changed(self, text: str) -> None:
        self._main_filter = text or ""
        self._populate_main_days()

    def _on_main_days_scroll(self, value: int) -> None:
        bar = self._main_days.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if value >= bar.maximum() - 24:
            asyncio.ensure_future(self._load_main_days(reset=False))

    def _append_main_day_row(self, row: dict[str, Any]) -> None:
        r = self._main_days.rowCount()
        self._main_days.insertRow(r)
        day = _parse_iso_day(row.get("date"))
        date_label = _fmt_main_day(day) if day else str(row.get("date") or "—")
        needed = int(row.get("needed_count") or 0)
        partial = int(row.get("partial_count") or 0)
        collected = int(row.get("collected_count") or 0)
        waived = int(row.get("waived_count") or 0)
        no_receipt = int(row.get("no_receipt_count") or 0)
        multi_file = int(row.get("multi_file_count") or 0)
        shared = int(row.get("shared_file_count") or 0)
        paint: dict[int, Any] = {}
        if needed:
            paint[2] = QColor(_WARN)
        if partial:
            paint[3] = QColor(_ACCENT)
        if collected:
            paint[4] = QColor(_OK)
        tips = {
            2: f"{needed} still need paper (Partial counts here too)",
            3: f"{partial} partial-linked (also in Needed)",
            4: f"{collected} collected / received",
            5: f"{waived} waived",
            6: f"{no_receipt} marked no receipt",
            7: f"{multi_file} entries with many files",
            8: f"{shared} entries sharing one file with another row",
        }
        self._fill_row(
            self._main_days,
            r,
            [
                date_label,
                str(int(row.get("entries_count") or 0)),
                str(needed),
                str(partial),
                str(collected),
                str(waived),
                str(no_receipt),
                str(multi_file),
                str(shared),
                "",
            ],
            row,
            paint=paint,
            tips=tips,
        )
        open_btn = _row_action_btn("Open", "mdi.chevron-right")
        open_btn.setFixedWidth(64)
        open_btn.clicked.connect(
            lambda *_a, d=day, raw=row: self._emit_main_day(d, raw)
        )
        self._main_days.setCellWidget(r, _MAIN_COL_OPEN, open_btn)
        self._main_days.setRowHeight(r, _ROW_H)

    def _update_main_page_hint(self) -> None:
        shown = len(self._main_visible)
        total = int(self._main_total or 0)
        loaded = len(self._main_day_rows)
        if total <= 0:
            self._main_page_hint.setText("")
            return
        if self._main_has_more:
            self._main_page_hint.setText(
                f"{shown} shown · {loaded} loaded · {total} total · scroll for more"
            )
        elif shown != loaded:
            self._main_page_hint.setText(f"{shown} of {loaded} loaded · {total} total")
        else:
            self._main_page_hint.setText(f"{shown} day{'s' if shown != 1 else ''}")

    def _populate_main_days(self) -> None:
        self._main_visible = _filter_main_day_rows(
            self._main_day_rows, self._main_filter
        )
        self._main_days.setRowCount(0)
        if not self._main_visible:
            if self._main_day_rows and (self._main_filter or "").strip():
                self._main_empty.setText(
                    "No days match this search.\nClear the search box to see all days."
                )
            elif self._main_has_active_date_filter():
                self._main_empty.setText(
                    "No days in this date range.\nTry All months or adjust From / To."
                )
            else:
                self._main_empty.setText(
                    "No register days with receipt-scoped entries yet.\n"
                    "Days appear here once expenses are saved."
                )
            self._main_empty.setVisible(True)
            self._main_days.setVisible(False)
            self._update_main_page_hint()
            return
        self._main_empty.setVisible(False)
        self._main_days.setVisible(True)
        for row in self._main_visible:
            self._append_main_day_row(row)
        self._autofit_main_day_columns()
        self._update_main_page_hint()

    def _autofit_main_day_columns(self) -> None:
        """Widen Date (and other data cols) so labels are not clipped."""
        if self._main_days.rowCount() <= 0:
            return
        self._main_days._col_width_suspend = True  # type: ignore[attr-defined]
        try:
            for col in range(_MAIN_COL_OPEN):
                if col == 0:
                    self._main_days.setColumnWidth(col, _MAIN_COL_DEFAULTS[0])
                    continue
                self._main_days.resizeColumnToContents(col)
                width = self._main_days.columnWidth(col) + 20
                floor = (
                    _MAIN_COL_DEFAULTS[col]
                    if col < len(_MAIN_COL_DEFAULTS)
                    else 48
                )
                # Date needs room for "11 Nov 2026"
                if col == _MAIN_COL_DATE:
                    floor = max(floor, 118)
                self._main_days.setColumnWidth(col, min(max(width, floor), 220))
            self._main_days.setColumnWidth(
                _MAIN_COL_OPEN, _MAIN_COL_DEFAULTS[_MAIN_COL_OPEN]
            )
            saver = getattr(self._main_days, "_col_width_save", None)
            if callable(saver):
                saver()
        finally:
            self._main_days._col_width_suspend = False  # type: ignore[attr-defined]

    def _selected_main_day(self) -> date | None:
        rows = sorted({idx.row() for idx in self._main_days.selectedIndexes()})
        if not rows:
            return None
        row = rows[0]
        if row < 0 or row >= len(self._main_visible):
            return None
        return _parse_iso_day(self._main_visible[row].get("date"))

    def _emit_main_day(self, day: date | None, raw: dict[str, Any] | None = None) -> None:
        resolved = day or _parse_iso_day((raw or {}).get("date"))
        if resolved is None:
            return
        self.day_opened.emit(resolved)

    def _on_main_day_double_clicked(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._main_visible):
            return
        day = _parse_iso_day(self._main_visible[row].get("date"))
        if day is not None:
            self.day_opened.emit(day)

    def _on_day_opened(self, day: object) -> None:
        if not isinstance(day, date):
            return
        self._main_drill_day = day
        self._main_drill_title.setText(f"Entries · {_fmt_main_day(day)}")
        self._main_stack.setCurrentIndex(1)
        self._tabs.setCurrentIndex(0)
        self._status.setText(f"Opened {_fmt_main_day(day)}")
        asyncio.ensure_future(self._load_main_day_entries(day))

    def _leave_main_drill(self) -> None:
        self._main_stack.setCurrentIndex(0)
        self._main_drill_day = None

    def _fmt_entry_amount(self, row: dict[str, Any]) -> str:
        amount = row.get("amount")
        cur = str(row.get("currency") or "TZS").strip() or "TZS"
        if amount is None:
            return "—"
        try:
            value = float(amount)
        except (TypeError, ValueError):
            return f"{amount} {cur}".strip()
        if cur.upper() == "TZS":
            return f"TZS {value:,.0f}"
        return f"{cur} {value:,.2f}"

    def _status_paint(self, status_key: str) -> QColor | None:
        color = _STATUS_FG.get((status_key or "").strip().lower())
        return QColor(color) if color else None

    def _entry_description_text(self, row: dict[str, Any]) -> str:
        """Description with truck number leading when present."""
        truck = str(row.get("truck_number") or "").strip()
        desc = str(row.get("description") or "").strip()
        if truck and desc:
            if desc.upper().startswith(truck.upper()):
                return desc
            return f"{truck}  {desc}"
        return truck or desc

    def _entry_cell_text(
        self, row: dict[str, Any], col: int, *, include_date: bool
    ) -> str:
        day = _parse_iso_day(row.get("date"))
        date_label = _fmt_main_day(day) if day else str(row.get("date") or "")
        status_label = str(
            row.get("display_status") or row.get("display_status_key") or ""
        )
        if include_date:
            mapping = {
                1: date_label,
                2: str(row.get("truck_number") or ""),
                3: self._entry_description_text(row),
                4: str(row.get("item") or ""),
                5: str(row.get("payee") or ""),
                6: str(row.get("ownership") or ""),
                7: self._fmt_entry_amount(row),
                8: status_label,
            }
        else:
            mapping = {
                1: str(row.get("truck_number") or ""),
                2: self._entry_description_text(row),
                3: str(row.get("item") or ""),
                4: str(row.get("payee") or ""),
                5: str(row.get("ownership") or ""),
                6: self._fmt_entry_amount(row),
                7: status_label,
            }
        return str(mapping.get(col) or "").strip()

    def _sorted_entry_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        include_date: bool,
        sort_col: int | None,
        ascending: bool,
    ) -> list[dict[str, Any]]:
        if sort_col is None:
            return list(rows)
        kind = (
            (_VIEW_ALL_SORT_KINDS if include_date else _ENTRY_SORT_KINDS).get(
                sort_col, "text"
            )
        )

        def sort_key(row: dict[str, Any]):
            text = self._entry_cell_text(row, sort_col, include_date=include_date)
            if kind == "number":
                try:
                    return (0, float(row.get("amount") or 0.0))
                except (TypeError, ValueError):
                    return (1, 0.0)
            if kind == "date":
                day = _parse_iso_day(row.get("date"))
                return (0, day.toordinal() if day else 0)
            if kind == "truck":
                return (0, text.upper())
            return (0, text.lower())

        return sorted(rows, key=sort_key, reverse=not ascending)

    def _populate_entry_table(
        self,
        table: QTableWidget,
        rows: list[dict[str, Any]],
        empty: QLabel,
        *,
        include_date: bool = False,
        active_filters: dict[int, set[str]] | None = None,
        sort_state: tuple[int | None, bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Fill Main drill or View all entry tables; return visible ordered rows."""
        sort_col, ascending = sort_state or (None, True)
        ordered = self._sorted_entry_rows(
            rows,
            include_date=include_date,
            sort_col=sort_col,
            ascending=ascending,
        )
        table.setRowCount(0)
        if not ordered:
            empty.setVisible(True)
            table.setVisible(False)
            return []
        empty.setVisible(False)
        table.setVisible(True)
        open_col = _VIEW_ALL_COL_OPEN if include_date else _ENTRY_COL_OPEN
        status_value_idx = 8 if include_date else 7
        attach_tip_idx = open_col
        filters = active_filters or {}
        visible_rows: list[dict[str, Any]] = []
        for row in ordered:
            hidden = False
            for col, accepted in filters.items():
                if not accepted:
                    continue
                cell = self._entry_cell_text(row, col, include_date=include_date)
                if cell not in accepted:
                    hidden = True
                    break
            if hidden:
                continue
            visible_rows.append(row)
            r = table.rowCount()
            table.insertRow(r)
            status_key = str(row.get("display_status_key") or "")
            status_label = str(row.get("display_status") or status_key or "—")
            paint: dict[int, Any] = {}
            fg = self._status_paint(status_key)
            if fg is not None:
                paint[status_value_idx] = fg
            tips: dict[int, str] = {}
            partial_note = str(row.get("partial_note") or "").strip()
            if status_key == "partial" and partial_note:
                tips[status_value_idx] = f"Partial: {partial_note}"
            elif partial_note:
                tips[status_value_idx] = partial_note
            file_id = str(row.get("primary_file_id") or "").strip()
            filename = str(row.get("primary_filename") or "").strip()
            if file_id:
                tip_bits = [filename or "Receipt file"]
                if status_key == "partial":
                    tip_bits.append("partial link")
                if partial_note:
                    tip_bits.append(partial_note)
                tips[attach_tip_idx] = " · ".join(tip_bits)
            day = _parse_iso_day(row.get("date"))
            date_label = _fmt_main_day(day) if day else str(row.get("date") or "—")
            truck = str(row.get("truck_number") or "")
            values = [
                truck,
                self._entry_description_text(row),
                str(row.get("item") or ""),
                str(row.get("payee") or ""),
                str(row.get("ownership") or ""),
                self._fmt_entry_amount(row),
                status_label,
                "",
            ]
            if include_date:
                values = [date_label, *values]
            self._fill_row(
                table,
                r,
                values,
                row,
                paint=paint,
                tips=tips,
            )
            if file_id:
                open_btn = _row_action_btn("Open", "mdi.file-eye-outline")
                open_btn.setFixedWidth(72)
                if status_key == "partial":
                    open_btn.setToolTip(
                        "Open partial receipt"
                        + (f": {partial_note}" if partial_note else "")
                    )
                else:
                    open_btn.setToolTip(filename or "Open receipt")
                open_btn.clicked.connect(
                    lambda *_a, entry=row: self._open_main_entry_receipt(entry)
                )
                table.setCellWidget(r, open_col, open_btn)
            else:
                dash = QTableWidgetItem("—")
                dash.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                dash.setForeground(QColor(_MUTED))
                dash.setData(Qt.UserRole, row)
                dash.setBackground(_stripe_bg(r))
                dash.setToolTip("No receipt file linked")
                table.setItem(r, open_col, dash)
            table.setRowHeight(r, _ROW_H)
        if sort_col is not None:
            hdr = table.horizontalHeader()
            hdr.setSortIndicator(
                sort_col,
                Qt.AscendingOrder if ascending else Qt.DescendingOrder,
            )
        return visible_rows

    def _populate_main_entries(self) -> None:
        visible = self._populate_entry_table(
            self._main_entries,
            self._main_entry_rows,
            self._main_entries_empty,
            include_date=False,
            active_filters=self._main_entry_filters,
            sort_state=self._main_entry_sort,
        )
        # Keep double-click / Open aligned with currently displayed order.
        self._main_entry_visible = visible
        hdr = self._main_entries.horizontalHeader()
        if isinstance(hdr, ExcelFilterHeaderView):
            hdr.sync_active(self._main_entry_filters)

    def _populate_view_all_entries(self) -> None:
        status_key = (self._view_all_status or "all").strip().lower()
        if not self._view_all_rows:
            if status_key not in {"", "all"}:
                self._view_all_empty.setText(
                    f"No “{status_key.replace('_', ' ')}” entries in this date range.\n"
                    "Try All, or widen From / To."
                )
            else:
                self._view_all_empty.setText(
                    "No entries in this date range.\nAdjust From / To or status filter."
                )
        visible = self._populate_entry_table(
            self._view_all_entries,
            self._view_all_rows,
            self._view_all_empty,
            include_date=True,
            active_filters=self._view_all_filters,
            sort_state=self._view_all_sort,
        )
        self._view_all_visible = visible
        hdr = self._view_all_entries.horizontalHeader()
        if isinstance(hdr, ExcelFilterHeaderView):
            hdr.sync_active(self._view_all_filters)
        n = len(visible)
        total = len(self._view_all_rows)
        cap = _VIEW_ALL_LIMIT
        if total >= cap:
            self._view_all_count.setText(f"Showing {n} of {total} (limit {cap})")
        elif n != total:
            self._view_all_count.setText(f"{n} of {total} entries")
        else:
            self._view_all_count.setText(f"{n} entr{'y' if n == 1 else 'ies'}")

    def _entry_filter_source_rows(
        self, rows: list[dict[str, Any]], *, include_date: bool
    ) -> list[dict]:
        out: list[dict] = []
        filterable = _VIEW_ALL_FILTERABLE if include_date else _ENTRY_FILTERABLE
        for row in rows:
            mapped: dict = {}
            for col in filterable:
                text = self._entry_cell_text(row, col, include_date=include_date)
                if text and text != "—":
                    mapped[col] = text
            if mapped:
                out.append(mapped)
        return out

    def _main_entry_filter_values(self, col: int) -> set:
        return cascade_column_values(
            self._entry_filter_source_rows(
                self._main_entry_rows, include_date=False
            ),
            target_col=col,
            active_filters=self._main_entry_filters,
        )

    def _view_all_filter_values(self, col: int) -> set:
        return cascade_column_values(
            self._entry_filter_source_rows(
                self._view_all_rows, include_date=True
            ),
            target_col=col,
            active_filters=self._view_all_filters,
        )

    def _on_main_entry_filter_changed(self, col: int, accepted) -> None:
        accepted = set(accepted or [])
        if accepted:
            self._main_entry_filters[col] = accepted
        else:
            self._main_entry_filters.pop(col, None)
        self._populate_main_entries()

    def _on_view_all_filter_changed(self, col: int, accepted) -> None:
        accepted = set(accepted or [])
        if accepted:
            self._view_all_filters[col] = accepted
        else:
            self._view_all_filters.pop(col, None)
        self._populate_view_all_entries()

    def _on_main_entry_sort(self, col: int, mode: str) -> None:
        self._main_entry_sort = (col, mode == SORT_ASC)
        self._populate_main_entries()

    def _on_view_all_sort(self, col: int, mode: str) -> None:
        self._view_all_sort = (col, mode == SORT_ASC)
        self._populate_view_all_entries()

    def _selected_entry_from_table(
        self, table: QTableWidget
    ) -> dict[str, Any] | None:
        for item in table.selectedItems():
            row = item.data(Qt.UserRole) or {}
            if isinstance(row, dict) and row.get("id"):
                return row
        return None

    def _selected_main_entry(self) -> dict[str, Any] | None:
        return self._selected_entry_from_table(self._main_entries)

    def _open_main_entry_receipt(self, row: dict[str, Any] | None) -> None:
        if not isinstance(row, dict):
            return
        file_id = str(row.get("primary_file_id") or "").strip()
        if not file_id:
            QMessageBox.information(
                self,
                "Receipts",
                "This entry has no linked receipt file to open.",
            )
            return
        title = str(row.get("primary_filename") or row.get("item") or "Receipt")
        if row.get("partial") or str(row.get("display_status_key") or "") == "partial":
            note = str(row.get("partial_note") or "").strip()
            title = f"{title} (partial" + (f": {note}" if note else "") + ")"
        open_receipt_viewer(self, file_id, title_hint=title)
        self._status.setText(f"Opened {title}")

    def _open_selected_main_entry_receipt(self) -> None:
        row = self._selected_main_entry()
        if not row:
            QMessageBox.information(self, "Receipts", "Select an entry first.")
            return
        self._open_main_entry_receipt(row)

    def _on_main_entry_double_clicked(self, row: int, _col: int) -> None:
        visible = getattr(self, "_main_entry_visible", self._main_entry_rows)
        if row < 0 or row >= len(visible):
            return
        entry = visible[row]
        if str(entry.get("primary_file_id") or "").strip():
            self._open_main_entry_receipt(entry)

    def _on_view_all_status(self, key: str) -> None:
        self._view_all_status = key
        for k, btn in self._view_all_status_buttons.items():
            btn.setStyleSheet(_chip_style(k == key))
            btn.setChecked(k == key)
        asyncio.ensure_future(self._load_view_all_entries())

    def _open_selected_view_all_receipt(self) -> None:
        row = self._selected_entry_from_table(self._view_all_entries)
        if not row:
            QMessageBox.information(self, "Receipts", "Select an entry first.")
            return
        self._open_main_entry_receipt(row)

    def _on_view_all_entry_double_clicked(self, row: int, _col: int) -> None:
        visible = getattr(self, "_view_all_visible", self._view_all_rows)
        if row < 0 or row >= len(visible):
            return
        entry = visible[row]
        if str(entry.get("primary_file_id") or "").strip():
            self._open_main_entry_receipt(entry)

    async def _load_view_all_entries(self) -> None:
        if not hasattr(self, "_view_all_entries"):
            return
        d0 = self._view_all_from.date().toPython()
        d1 = self._view_all_to.date().toPython()
        if d1 < d0:
            QMessageBox.information(
                self, "Receipts", "View all: To date must be on or after From."
            )
            return
        show_widget_loading(self._view_all_entries, "Loading entries…")
        failed = False
        try:
            self._view_all_rows = await list_receipt_entries(
                date_from=d0.isoformat(),
                date_to=d1.isoformat(),
                status=self._view_all_status or "all",
                limit=_VIEW_ALL_LIMIT,
            )
        except Exception as exc:
            failed = True
            QMessageBox.critical(self, "Receipts", f"View all failed:\n{exc}")
            self._view_all_rows = []
        finally:
            hide_widget_loading(self._view_all_entries)
        self._populate_view_all_entries()
        if failed:
            self._status.setText("View all failed")
            return
        self._status.setText(
            f"View all · {d0.isoformat()} → {d1.isoformat()} · "
            f"{len(self._view_all_rows)} row(s)"
        )

    async def _load_main_day_entries(self, day: date) -> None:
        show_widget_loading(self._main_stack, f"Loading {_fmt_main_day(day)}…")
        try:
            self._main_entry_rows = await list_receipt_day_entries(
                day=day.isoformat(),
                limit=500,
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Receipts", f"Day entries failed:\n{exc}"
            )
            self._main_entry_rows = []
        finally:
            hide_widget_loading(self._main_stack)
        if self._main_drill_day != day:
            return
        # Fresh day → clear prior column filters so nothing looks "missing".
        self._main_entry_filters.clear()
        self._main_entry_sort = (None, True)
        self._populate_main_entries()
        self._status.setText(
            f"{_fmt_main_day(day)} · {len(self._main_entry_rows)} entr"
            f"{'y' if len(self._main_entry_rows) == 1 else 'ies'}"
        )

    async def _load_main_days(self, *, reset: bool = True) -> None:
        if not reset:
            if self._main_scroll_loading or not self._main_has_more:
                return
            # Don't page while a search filter is narrowing the local list.
            if (self._main_filter or "").strip():
                return
        else:
            self._main_day_rows = []
            self._main_has_more = True
            self._main_total = 0

        self._main_scroll_loading = True
        date_from, date_to = self._main_active_date_bounds()
        offset = 0 if reset else len(self._main_day_rows)
        appended: list[dict[str, Any]] = []
        if reset:
            show_widget_loading(self._main_stack, "Loading receipt days…")
        try:
            payload = await list_receipt_day_summaries(
                date_from=date_from,
                date_to=date_to,
                limit=_MAIN_PAGE_SIZE,
                offset=offset,
            )
            items = list(payload.get("items") or [])
            self._main_total = int(payload.get("total") or 0)
            self._main_has_more = bool(payload.get("has_more"))
            if reset:
                self._main_day_rows = items
            else:
                # Dedupe by date when appending pages; keep scroll position.
                seen = {str(r.get("date") or "") for r in self._main_day_rows}
                for row in items:
                    key = str(row.get("date") or "")
                    if key and key not in seen:
                        self._main_day_rows.append(row)
                        appended.append(row)
                        seen.add(key)
        except Exception as exc:
            if reset:
                QMessageBox.critical(self, "Receipts", f"Main day list failed:\n{exc}")
                self._main_day_rows = []
                self._main_has_more = False
                self._main_total = 0
                self._status.setText("Main day list failed")
        finally:
            self._main_scroll_loading = False
            if reset:
                hide_widget_loading(self._main_stack)

        if reset:
            self._populate_main_days()
            if (
                self._main_drill_day is not None
                and self._main_stack.currentIndex() == 1
            ):
                await self._load_main_day_entries(self._main_drill_day)
            return

        # Infinite-scroll append: add rows only (avoid scrollbar jump).
        if appended:
            # Re-apply search so appended pages respect the active keyword.
            if (self._main_filter or "").strip():
                self._populate_main_days()
            else:
                self._main_visible.extend(appended)
                self._main_empty.setVisible(False)
                self._main_days.setVisible(True)
                for row in appended:
                    self._append_main_day_row(row)
                self._autofit_main_day_columns()
        self._update_main_page_hint()

    async def _ensure_main_days_fully_loaded(self) -> None:
        """Fetch remaining pages for the active date range (for export).

        Bypasses the scroll-path guard that skips paging while a keyword
        search is active — export must cover the full activated filter set.
        """
        if not self._main_has_more:
            return
        date_from, date_to = self._main_active_date_bounds()
        guard = 0
        while self._main_has_more and guard < 500:
            guard += 1
            offset = len(self._main_day_rows)
            payload = await list_receipt_day_summaries(
                date_from=date_from,
                date_to=date_to,
                limit=_MAIN_PAGE_SIZE,
                offset=offset,
            )
            items = list(payload.get("items") or [])
            self._main_total = int(payload.get("total") or 0)
            self._main_has_more = bool(payload.get("has_more"))
            if not items:
                self._main_has_more = False
                break
            seen = {str(r.get("date") or "") for r in self._main_day_rows}
            added = 0
            for row in items:
                key = str(row.get("date") or "")
                if key and key not in seen:
                    self._main_day_rows.append(row)
                    seen.add(key)
                    added += 1
            if added == 0:
                self._main_has_more = False
                break
        # Keep on-screen list in sync with anything newly fetched.
        self._populate_main_days()

    async def _export_main_days(self, fmt: str) -> None:
        fmt = (fmt or "xlsx").lower().strip()
        if fmt not in ("xlsx", "csv", "pdf"):
            fmt = "xlsx"
        try:
            await self._ensure_main_days_fully_loaded()
        except Exception as exc:
            QMessageBox.critical(self, "Export", f"Could not load all filtered days:\n{exc}")
            return
        rows = _filter_main_day_rows(self._main_day_rows, self._main_filter)
        if not rows:
            QMessageBox.information(self, "Export", "Nothing to export for the active filters.")
            return
        filters = {
            "xlsx": ("Excel Workbook (*.xlsx)", ".xlsx"),
            "csv": ("CSV File (*.csv)", ".csv"),
            "pdf": ("PDF Report (*.pdf)", ".pdf"),
        }
        file_filter, ext = filters[fmt]
        default_name = f"receipt_days_{date.today().isoformat()}{ext}"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export day list as {ext.lstrip('.').upper()}", default_name, file_filter
        )
        if not path:
            return
        if not path.lower().endswith(ext):
            path = f"{path}{ext}"
        table_rows = [_main_export_row(r) for r in rows]
        try:
            if fmt == "csv":
                self._write_main_days_csv(path, table_rows)
            elif fmt == "pdf":
                self._write_main_days_pdf(path, table_rows)
            else:
                self._write_main_days_xlsx(path, table_rows)
        except Exception as exc:
            QMessageBox.critical(self, "Export", f"Export failed:\n{exc}")
            return
        self._status.setText(f"Exported {len(table_rows)} day(s) → {path}")

    @staticmethod
    def _write_main_days_csv(path: str, rows: list[list[Any]]) -> None:
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(_MAIN_EXPORT_HEADERS)
            writer.writerows(rows)

    @staticmethod
    def _write_main_days_xlsx(path: str, rows: list[list[Any]]) -> None:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Receipt days"
        ws.append(list(_MAIN_EXPORT_HEADERS))
        for rec in rows:
            ws.append(rec)
        wb.save(path)

    @staticmethod
    def _write_main_days_pdf(path: str, rows: list[list[Any]]) -> None:
        import fitz

        # A4 landscape
        page_w, page_h = 841.89, 595.28
        margin = 28.0
        col_w = [
            90,
            55,
            55,
            55,
            60,
            55,
            70,
            65,
            55,
        ]
        doc = fitz.open()
        page = doc.new_page(width=page_w, height=page_h)
        y = margin
        font = "helv"
        page.insert_text(
            (margin, y + 12),
            "Receipt days",
            fontsize=14,
            fontname=font,
            color=(0.07, 0.09, 0.15),
        )
        y += 28
        x = margin
        for i, header in enumerate(_MAIN_EXPORT_HEADERS):
            page.insert_text(
                (x, y + 10),
                header,
                fontsize=8,
                fontname=font,
                color=(0.42, 0.45, 0.50),
            )
            x += col_w[i]
        y += 16
        page.draw_line(
            (margin, y),
            (page_w - margin, y),
            color=(0.90, 0.91, 0.92),
            width=0.6,
        )
        y += 8
        row_h = 14.0
        for rec in rows:
            if y + row_h > page_h - margin:
                page = doc.new_page(width=page_w, height=page_h)
                y = margin
            x = margin
            for i, cell in enumerate(rec):
                page.insert_text(
                    (x, y + 10),
                    str(cell),
                    fontsize=8,
                    fontname=font,
                    color=(0.07, 0.09, 0.15),
                )
                x += col_w[i]
            y += row_h
        doc.save(path)
        doc.close()

    def _build_requests_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(_PAGE_CARD_SS)
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
        page.setStyleSheet(_PAGE_CARD_SS)
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
        page.setStyleSheet(_PAGE_CARD_SS)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(0)
        toolbar_h = 28
        self._inbox_status = "parked"

        # One row — status chips + actions (28px)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        toolbar.setAlignment(Qt.AlignVCenter)
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
            btn.setFixedHeight(toolbar_h)
            btn.setChecked(key == "parked")
            btn.setStyleSheet(_chip_style(key == "parked"))
            btn.clicked.connect(lambda _=False, k=key: self._on_inbox_status(k))
            self._inbox_chip_group.addButton(btn)
            self._inbox_chip_buttons[key] = btn
            toolbar.addWidget(btn, 0, Qt.AlignVCenter)
        toolbar.addStretch(1)
        drop_btn = _ghost_btn("Park files…", "mdi.file-upload-outline")
        drop_btn.setFixedHeight(toolbar_h)
        drop_btn.clicked.connect(lambda: asyncio.ensure_future(self._park_files()))
        view_btn = _ghost_btn("View", "mdi.eye-outline")
        view_btn.setFixedHeight(toolbar_h)
        view_btn.clicked.connect(self._view_selected_file)
        approve_btn = _primary_btn("Approve", "mdi.check-bold")
        approve_btn.setFixedHeight(toolbar_h)
        approve_btn.clicked.connect(lambda: asyncio.ensure_future(self._approve_selected()))
        reject_btn = _ghost_btn("Reject…", "mdi.close-circle-outline")
        reject_btn.setFixedHeight(toolbar_h)
        reject_btn.clicked.connect(lambda: asyncio.ensure_future(self._reject_selected()))
        link_go = _ghost_btn("Link selected…", "mdi.link-variant")
        link_go.setFixedHeight(toolbar_h)
        link_go.clicked.connect(self._go_link_with_selection)
        for btn in (drop_btn, view_btn, approve_btn, reject_btn, link_go):
            toolbar.addWidget(btn, 0, Qt.AlignVCenter)
        lay.addLayout(toolbar)
        lay.addSpacing(8)

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
        page.setStyleSheet(_PAGE_CARD_SS)
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
        page.setStyleSheet(_PAGE_CARD_SS)
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
        page.setStyleSheet(_PAGE_CARD_SS)
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
            self._load_main_days(),
            self._load_view_all_entries(),
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
