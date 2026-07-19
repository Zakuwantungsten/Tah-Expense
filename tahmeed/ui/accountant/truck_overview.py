"""Truck Overview — cross-source truck-centric expense view.

This page lets the accountant select a truck and review related rows pulled
from verified cashier transactions, diesel imports, USD sheet-ledgers, and
selected imported feeds such as Toll Plaza and Zambia Parking.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import qtawesome as qta

from PySide6.QtCore import Qt, QTimer, QSize, QDate
from PySide6.QtGui import QColor, QTextDocument, QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.truck_service import search_fleet
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit
from tahmeed.ui.accountant.separate_expenses import _make_table, _cell, _finish_table_row

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_BLUE_L = "#E8F4FD"
_GREEN = "#16A34A"
_AMBER = "#D97706"
_RED = "#DC2626"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"
_HDR_BG = "#F1F5F9"

_PAGE_SIZES = [25, 50, 100]
_ROW_H = 32
_MIN_FILTER_DATE = QDate(2000, 1, 1)

_SOURCE_OPTIONS = [
    ("All Sources", "all"),
    ("Master Expenses", "master"),
    ("Diesel Cash", "diesel_cash"),
    ("Diesel Imports", "diesel_imports"),
    ("Afritrack", "afritrack"),
    ("Toll Plaza", "toll_plaza"),
    ("Parking Congo", "parking_congo"),
    ("Zambia Parking", "zambia_parking"),
    ("Congo Expenses", "congo_expenses"),
    ("Ahmed Kimvi", "ahmed_kimvi"),
    ("RahnTech", "rahntech"),
    ("COMESA", "comesa"),
    ("Third Party Covers", "third_party"),
    ("SM Burhani", "sm_burhani"),
]

# Widths are preferred defaults; DESCRIPTION and STATION stretch to fill.
_COLS = [
    ("DATE", 90, Qt.AlignLeft),
    ("SOURCE", 120, Qt.AlignLeft),
    ("DESCRIPTION", 200, Qt.AlignLeft),
    ("REFERENCE", 120, Qt.AlignLeft),
    ("TRUCK FIELD", 90, Qt.AlignLeft),
    ("TZS", 110, Qt.AlignRight),
    ("USD", 100, Qt.AlignRight),
    ("ZMW", 100, Qt.AlignRight),
    ("LTRS", 70, Qt.AlignRight),
    ("RATE", 80, Qt.AlignRight),
    ("STATION / OWNER", 130, Qt.AlignLeft),
    ("RECEIPT", 85, Qt.AlignCenter),
]
_STRETCH_COLS = {2, 10}  # DESCRIPTION, STATION / OWNER
_CTRL_H = 32


def _lbl(text: str = "", size: int = 13, weight: int = 400, color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color: {color}; font-size: {size}px; font-weight: {weight};"
        " font-family:'Segoe UI'; background: transparent;"
    )
    return w


def _input_ss() -> str:
    return (
        f"QLineEdit, QComboBox, QDateEdit {{"
        f" border: 1px solid {_BORDER}; border-radius: 5px;"
        f" background: {_WHITE}; color: {_T1}; font-size: 12px;"
        " font-family:'Segoe UI'; padding: 0 8px;"
        f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
        f"QLineEdit:focus, QComboBox:focus, QDateEdit:focus {{ border-color: {_BLUE}; }}"
        "QComboBox::drop-down { border: none; width: 20px; }"
        "QDateEdit::drop-down { border: none; width: 20px; }"
    )


def _normalize_currency(currency: str) -> str:
    cur = (currency or "").strip().upper()
    if cur in ("TZS", "TSH", "TZ"):
        return "TZS"
    if cur == "USD":
        return "USD"
    if cur in ("ZMW", "ZMB", "ZK"):
        return "ZMW"
    return cur


def _amount_columns(currency: str, amount) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (tzs, usd, zmw) with the amount only in its currency column."""
    if amount is None or amount == "":
        return None, None, None
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return None, None, None
    cur = _normalize_currency(currency)
    if cur == "TZS":
        return val, None, None
    if cur == "USD":
        return None, val, None
    if cur == "ZMW":
        return None, None, val
    return None, None, None


def _fmt_currency_cell(currency: str, value) -> str:
    if value is None:
        return "—"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "—"
    decimals = 2 if currency == "USD" else 0
    return f"{val:,.{decimals}f}"


def _btn(text: str, icon_name: str = "", primary: bool = True) -> QPushButton:
    b = QPushButton(f"  {text}" if icon_name else text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(32)
    if icon_name:
        try:
            b.setIcon(qta.icon(icon_name, color="#FFFFFF" if primary else _T2))
            b.setIconSize(QSize(15, 15))
        except Exception:
            pass
    if primary:
        b.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 12px; font-weight: 600;"
            " font-family:'Segoe UI'; padding: 0 12px; }}"
            "QPushButton:hover { background: #005EA3; }"
        )
    else:
        b.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
    return b


def _fmt_amount(currency: str, value) -> str:
    if value is None:
        return "—"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "—"
    prefix = f"{currency} " if currency else ""
    decimals = 2 if currency == "USD" else 0
    return f"{prefix}{val:,.{decimals}f}"


def _fmt_num(value, decimals: int = 0) -> str:
    if value in (None, "", 0, 0.0):
        return "—"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


class _SummaryCard(QFrame):
    def __init__(self, label: str, value: str = "—", accent: str = _BLUE, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(92)
        self.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 10px; }}"
        )
        self._accent = accent
        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        accent_bar = QFrame()
        accent_bar.setFixedHeight(5)
        accent_bar.setStyleSheet(
            f"background: {accent}; border: none; border-top-left-radius: 10px; border-top-right-radius: 10px;"
        )
        vl.addWidget(accent_bar)
        body = QVBoxLayout()
        body.setContentsMargins(16, 12, 16, 14)
        body.setSpacing(5)
        self._label = _lbl(label.upper(), size=10, weight=600, color=_T2)
        self._value = _lbl(value, size=20, weight=700, color=accent)
        body.addWidget(self._label)
        body.addWidget(self._value)
        body.addStretch()
        vl.addLayout(body)

    def set_value(self, value: str, color: Optional[str] = None) -> None:
        self._value.setText(value)
        self._value.setStyleSheet(
            f"color: {color or self._accent}; font-size: 20px; font-weight: 700;"
            " font-family:'Segoe UI'; background: transparent;"
        )


class _PaginationBar(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page = 0
        self._total = 0
        self._size = _PAGE_SIZES[0]
        self.setFixedHeight(44)
        self.setStyleSheet(f"QFrame {{ background: {_WHITE}; border-top: 1px solid {_BORDER}; }}")

        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(10)
        self._size_cb = QComboBox()
        for size in _PAGE_SIZES:
            self._size_cb.addItem(f"Show {size}", size)
        self._size_cb.setFixedWidth(100)
        self._size_cb.setStyleSheet(_input_ss())
        hl.addWidget(self._size_cb)

        self._info = _lbl("Select a truck to load records", size=12, color=_T2)
        hl.addWidget(self._info)
        hl.addStretch()

        self._prev = _btn("← Prev", primary=False)
        self._next = _btn("Next →", primary=False)
        self._prev.setFixedWidth(88)
        self._next.setFixedWidth(88)
        hl.addWidget(self._prev)
        hl.addWidget(self._next)

    def bind(self, on_prev, on_next, on_size) -> None:
        self._prev.clicked.connect(on_prev)
        self._next.clicked.connect(on_next)
        self._size_cb.currentIndexChanged.connect(on_size)

    def page_size(self) -> int:
        return self._size_cb.currentData() or _PAGE_SIZES[0]

    def update_state(self, page: int, total: int, size: int) -> None:
        self._page, self._total, self._size = page, total, size
        max_page = max(0, (total - 1) // size) if total else 0
        self._prev.setEnabled(page > 0)
        self._next.setEnabled(page < max_page)
        start = page * size + 1 if total else 0
        end = min((page + 1) * size, total)
        self._info.setText(
            f"Showing {start:,}–{end:,} of {total:,}  ·  Page {page + 1} of {max_page + 1}"
            if total else "No matching records"
        )


class TruckOverviewWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page = 0
        self._total = 0
        self._active_truck = ""
        self._loading = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(lambda: asyncio.ensure_future(self._reload()))

        self._build()

    def _build(self) -> None:
        self.setStyleSheet(f"background: {_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        title_bar = QFrame()
        title_bar.setFixedHeight(52)
        title_bar.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-bottom: 1px solid {_BORDER}; }}"
        )
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(16, 0, 16, 0)
        tb.setSpacing(10)
        try:
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(22, 22)
            icon_lbl.setPixmap(qta.icon("mdi.truck-fast-outline", color=_BLUE).pixmap(22, 22))
            tb.addWidget(icon_lbl)
        except Exception:
            pass
        tb.addWidget(_lbl("Truck Overview", size=16, weight=700))
        self._subtitle = _lbl("Select a truck to gather cross-source expenses and fuel.", size=12, color=_T2)
        tb.addWidget(self._subtitle)
        tb.addStretch()
        root.addWidget(title_bar)

        root.addWidget(self._build_toolbar())
        root.addLayout(self._build_summary_cards())

        self._status = _lbl("No truck selected yet.", size=11, color=_TM)
        root.addWidget(self._status)

        self._table = _make_table([c[0] for c in _COLS])
        self._table.setShowGrid(True)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        hdr = self._table.horizontalHeader()
        hdr.setSectionsMovable(False)
        hdr.setStretchLastSection(False)
        for idx, (_, width, _align) in enumerate(_COLS):
            self._table.setColumnWidth(idx, width)
            if idx in _STRETCH_COLS:
                hdr.setSectionResizeMode(idx, QHeaderView.Stretch)
            else:
                hdr.setSectionResizeMode(idx, QHeaderView.Interactive)
        root.addWidget(self._table, 1)

        self._pager = _PaginationBar()
        self._pager.bind(self._on_prev, self._on_next, self._on_page_size_changed)
        root.addWidget(self._pager)

    def _build_toolbar(self) -> QFrame:
        toolbar = QFrame()
        toolbar.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 6px; }}"
        )
        toolbar_v = QVBoxLayout(toolbar)
        toolbar_v.setContentsMargins(12, 10, 12, 10)
        toolbar_v.setSpacing(10)

        # --- Row 1: all filters on one row; scroll horizontally when narrow ---
        filter_scroll = QScrollArea()
        filter_scroll.setWidgetResizable(True)
        filter_scroll.setFrameShape(QFrame.NoFrame)
        filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        filter_scroll.setFixedHeight(_CTRL_H + 14)
        filter_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            f"QScrollBar:horizontal {{ height: 8px; background: {_BG}; }}"
            f"QScrollBar::handle:horizontal {{ background: {_BORDER}; border-radius: 4px; min-width: 24px; }}"
        )

        filter_inner = QWidget()
        filter_inner.setStyleSheet("background: transparent;")
        filter_row = QHBoxLayout(filter_inner)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(10)

        self._truck_edit = TruckLineEdit(search_fleet)
        self._truck_edit.setPlaceholderText("Search truck or trailer…")
        self._truck_edit.setFixedWidth(180)
        self._truck_edit.setFixedHeight(_CTRL_H)
        self._truck_edit.setStyleSheet(_input_ss())
        self._truck_edit.returnPressed.connect(self._on_load_clicked)
        filter_row.addWidget(self._truck_edit)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search description, station, reference…")
        self._search.setFixedWidth(260)
        self._search.setFixedHeight(_CTRL_H)
        self._search.setStyleSheet(_input_ss())
        self._search.textEdited.connect(lambda _t: self._on_filter_changed())
        filter_row.addWidget(self._search)

        self._source_cb = QComboBox()
        for label, key in _SOURCE_OPTIONS:
            self._source_cb.addItem(label, key)
        self._source_cb.setFixedWidth(160)
        self._source_cb.setFixedHeight(_CTRL_H)
        self._source_cb.setStyleSheet(_input_ss())
        self._source_cb.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._source_cb)

        self._from_date = QDateEdit()
        self._from_date.setCalendarPopup(True)
        self._from_date.setDisplayFormat("dd MMM yyyy")
        self._from_date.setMinimumDate(_MIN_FILTER_DATE)
        self._from_date.setSpecialValueText("From")
        self._from_date.setDate(_MIN_FILTER_DATE)
        self._from_date.setFixedWidth(130)
        self._from_date.setFixedHeight(_CTRL_H)
        self._from_date.setStyleSheet(_input_ss())
        self._from_date.dateChanged.connect(lambda _d: self._on_filter_changed())
        filter_row.addWidget(self._from_date)

        self._to_date = QDateEdit()
        self._to_date.setCalendarPopup(True)
        self._to_date.setDisplayFormat("dd MMM yyyy")
        self._to_date.setMinimumDate(_MIN_FILTER_DATE)
        self._to_date.setSpecialValueText("To")
        self._to_date.setDate(_MIN_FILTER_DATE)
        self._to_date.setFixedWidth(130)
        self._to_date.setFixedHeight(_CTRL_H)
        self._to_date.setStyleSheet(_input_ss())
        self._to_date.dateChanged.connect(lambda _d: self._on_filter_changed())
        filter_row.addWidget(self._to_date)

        load_btn = _btn("Load", "mdi.magnify")
        load_btn.clicked.connect(self._on_load_clicked)
        filter_row.addWidget(load_btn)

        filter_row.addStretch()
        # Keep preferred width so the row overflows instead of compressing.
        filter_inner.setMinimumWidth(980)
        filter_inner.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
        filter_scroll.setWidget(filter_inner)
        toolbar_v.addWidget(filter_scroll)

        # --- separator ---
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {_BORDER}; border: none;")
        toolbar_v.addWidget(sep)

        # --- Row 2: actions ---
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        refresh_btn = _btn("Refresh", "mdi.refresh", primary=False)
        refresh_btn.clicked.connect(self.refresh)
        action_row.addWidget(refresh_btn)

        clear_btn = _btn("Clear Results", "mdi.filter-remove-outline", primary=False)
        clear_btn.clicked.connect(self._clear_results)
        action_row.addWidget(clear_btn)

        action_row.addStretch()

        export_excel_btn = _btn("Export Excel", "mdi.microsoft-excel", primary=False)
        export_excel_btn.clicked.connect(self._export_excel)
        action_row.addWidget(export_excel_btn)

        export_pdf_btn = _btn("Export PDF", "mdi.file-pdf-box", primary=False)
        export_pdf_btn.clicked.connect(self._export_pdf)
        action_row.addWidget(export_pdf_btn)

        toolbar_v.addLayout(action_row)
        return toolbar

    def _build_summary_cards(self) -> QHBoxLayout:
        summary = QHBoxLayout()
        summary.setSpacing(12)
        self._records_card = _SummaryCard("Records", accent=_BLUE)
        self._sources_card = _SummaryCard("Sources", accent=_AMBER)
        self._tzs_card = _SummaryCard("TZS Total", accent=_BLUE)
        self._usd_card = _SummaryCard("USD Total", accent=_GREEN)
        self._zmw_card = _SummaryCard("ZMW Total", accent=_AMBER)
        self._liters_card = _SummaryCard("Fuel Liters", accent=_RED)
        for card in (
            self._records_card,
            self._sources_card,
            self._tzs_card,
            self._usd_card,
            self._zmw_card,
            self._liters_card,
        ):
            summary.addWidget(card)
        return summary

    def refresh(self) -> None:
        if self._active_truck:
            asyncio.ensure_future(self._reload())

    def _selected_truck(self) -> str:
        return self._truck_edit.text().strip().upper()

    def _selected_source(self) -> str:
        return self._source_cb.currentData() or "all"

    def _search_text(self) -> str:
        return self._search.text().strip()

    def _date_filters(self) -> tuple[Optional[datetime], Optional[datetime]]:
        date_from = None
        date_to = None
        if self._from_date.date() > _MIN_FILTER_DATE:
            d = self._from_date.date()
            date_from = datetime(d.year(), d.month(), d.day(), 0, 0, 0)
        if self._to_date.date() > _MIN_FILTER_DATE:
            d = self._to_date.date()
            date_to = datetime(d.year(), d.month(), d.day(), 23, 59, 59)
        return date_from, date_to

    def _has_valid_date_range(self, *, warn: bool = False) -> bool:
        date_from, date_to = self._date_filters()
        valid = not (date_from and date_to and date_from > date_to)
        if not valid and warn:
            QMessageBox.warning(self, "Invalid Date Range", "'From' date cannot be later than 'To' date.")
        return valid

    def _on_load_clicked(self) -> None:
        truck = self._selected_truck()
        self._active_truck = truck
        self._page = 0
        asyncio.ensure_future(self._reload())

    def _on_filter_changed(self) -> None:
        if not self._active_truck:
            return
        self._page = 0
        self._debounce.start()

    def _on_page_size_changed(self) -> None:
        if not self._active_truck:
            return
        self._page = 0
        asyncio.ensure_future(self._reload())

    def _on_prev(self) -> None:
        if self._page > 0:
            self._page -= 1
            asyncio.ensure_future(self._reload())

    def _on_next(self) -> None:
        size = self._pager.page_size()
        max_page = max(0, (self._total - 1) // size) if self._total else 0
        if self._page < max_page:
            self._page += 1
            asyncio.ensure_future(self._reload())

    async def _reload(self) -> None:
        if self._loading:
            return
        if not self._has_valid_date_range(warn=True):
            return
        truck = self._active_truck or self._selected_truck()
        if not truck:
            self._status.setText("Enter a truck number to load the overview.")
            self._table.setRowCount(0)
            return

        self._loading = True
        self._status.setText(f"Loading data for {truck}…")
        try:
            from tahmeed.services.accountant_service import (
                get_truck_overview_records,
                count_truck_overview_records,
                get_truck_overview_summary,
            )

            size = self._pager.page_size()
            skip = self._page * size
            date_from, date_to = self._date_filters()
            records, total, summary = await asyncio.gather(
                get_truck_overview_records(
                    truck=truck,
                    search=self._search_text(),
                    source=self._selected_source(),
                    date_from=date_from,
                    date_to=date_to,
                    limit=size,
                    skip=skip,
                ),
                count_truck_overview_records(
                    truck=truck,
                    search=self._search_text(),
                    source=self._selected_source(),
                    date_from=date_from,
                    date_to=date_to,
                ),
                get_truck_overview_summary(
                    truck=truck,
                    search=self._search_text(),
                    source=self._selected_source(),
                    date_from=date_from,
                    date_to=date_to,
                ),
            )
            self._active_truck = truck
            self._total = total
            self._subtitle.setText(f"Cross-source view for {truck}")
            self._records_card.set_value(f"{summary['record_count']:,}")
            self._sources_card.set_value(f"{summary['source_count']:,}", color=_AMBER)
            self._tzs_card.set_value(_fmt_amount("TZS", summary["tzs_total"]))
            self._usd_card.set_value(_fmt_amount("USD", summary["usd_total"]), color=_GREEN)
            self._zmw_card.set_value(_fmt_amount("ZMW", summary["zmw_total"]), color=_AMBER)
            self._liters_card.set_value(_fmt_num(summary["liters_total"], 0), color=_RED)
            self._fill_table(records)
            self._pager.update_state(self._page, total, size)
            self._status.setText(
                f"Loaded {total:,} cross-source row(s) for {truck}. "
                "Zambia entries are summarized under ZMW."
            )
        except Exception as exc:
            self._table.setRowCount(0)
            self._status.setText(f"Failed to load truck overview: {exc}")
        finally:
            self._loading = False

    def _fill_table(self, rows: list) -> None:
        self._table.setRowCount(0)
        for idx, row in enumerate(rows):
            r = self._table.rowCount()
            self._table.insertRow(r)

            amount = row.get("amount")
            tzs_amt, usd_amt, zmw_amt = _amount_columns(row.get("currency", ""), amount)
            amount_color = _RED if isinstance(amount, (int, float)) and amount < 0 else _T1
            source_color = _BLUE if row.get("source_group") in ("master", "diesel_cash") else _T2
            receipt = row.get("receipt_status") or "—"
            receipt_color = (
                _GREEN if receipt == "received" else
                _AMBER if receipt == "pending" else
                _RED if receipt == "missing" else
                _TM
            )

            date_value = row.get("date")
            date_txt = date_value.strftime("%d %b %Y") if hasattr(date_value, "strftime") and date_value.year > 1 else "—"
            self._table.setItem(r, 0, _cell(date_txt))
            self._table.setItem(r, 1, _cell(row.get("source", "—"), color=source_color))
            self._table.setItem(r, 2, _cell(row.get("description", "—")))
            self._table.setItem(r, 3, _cell(row.get("reference", "—")))
            self._table.setItem(r, 4, _cell(row.get("truck_value", "—")))
            self._table.setItem(
                r, 5,
                _cell(
                    _fmt_currency_cell("TZS", tzs_amt),
                    align=Qt.AlignRight | Qt.AlignVCenter,
                    mono=True,
                    color=amount_color if tzs_amt is not None else _TM,
                ),
            )
            self._table.setItem(
                r, 6,
                _cell(
                    _fmt_currency_cell("USD", usd_amt),
                    align=Qt.AlignRight | Qt.AlignVCenter,
                    mono=True,
                    color=(_GREEN if isinstance(usd_amt, float) and usd_amt >= 0 else amount_color)
                    if usd_amt is not None else _TM,
                ),
            )
            self._table.setItem(
                r, 7,
                _cell(
                    _fmt_currency_cell("ZMW", zmw_amt),
                    align=Qt.AlignRight | Qt.AlignVCenter,
                    mono=True,
                    color=amount_color if zmw_amt is not None else _TM,
                ),
            )
            self._table.setItem(
                r, 8,
                _cell(_fmt_num(row.get("liters"), 0), align=Qt.AlignRight | Qt.AlignVCenter, mono=True)
            )
            self._table.setItem(
                r, 9,
                _cell(_fmt_num(row.get("rate"), 2), align=Qt.AlignRight | Qt.AlignVCenter, mono=True)
            )
            self._table.setItem(r, 10, _cell(row.get("station", "—")))
            self._table.setItem(r, 11, _cell(receipt, align=Qt.AlignCenter | Qt.AlignVCenter, color=receipt_color))
            _finish_table_row(self._table, r)

            # Slightly tint ZMW rows so Zambia-related entries stand out.
            if _normalize_currency(row.get("currency") or "") == "ZMW":
                for c in range(self._table.columnCount()):
                    item = self._table.item(r, c)
                    if item is not None:
                        item.setBackground(QColor(_BLUE_L))

    def _export_excel(self) -> None:
        self._start_background_task(self._do_export_excel(), "Excel export")

    async def _do_export_excel(self) -> None:
        truck = self._active_truck or self._selected_truck()
        if not truck:
            QMessageBox.warning(self, "Export", "Select a truck first.")
            return
        if not self._has_valid_date_range(warn=True):
            return
        try:
            import openpyxl
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        except ImportError:
            QMessageBox.critical(
                self, "Missing Dependency",
                "openpyxl is required for Excel export.\n\nRun: pip install openpyxl",
            )
            return

        from tahmeed.services.accountant_service import (
            get_truck_overview_records,
            get_truck_overview_summary,
        )

        try:
            date_from, date_to = self._date_filters()
            rows = await get_truck_overview_records(
                truck=truck,
                search=self._search_text(),
                source=self._selected_source(),
                date_from=date_from,
                date_to=date_to,
                limit=100000,
                skip=0,
            )
            summary = await get_truck_overview_summary(
                truck=truck,
                search=self._search_text(),
                source=self._selected_source(),
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Failed to prepare export data:\n{exc}")
            return

        if not rows:
            QMessageBox.information(self, "Export", "No records match the current truck and filters.")
            return

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Truck Overview"

        # Match results.xlsx style: green section banner, navy headers,
        # thin #CCCCCC borders, alternating #EEF2FF rows, Calibri.
        n_cols = len(_COLS)
        last_col = openpyxl.utils.get_column_letter(n_cols)

        title_fill = PatternFill("solid", fgColor="1F6B2E")
        header_fill = PatternFill("solid", fgColor="2C5282")
        alt_fill = PatternFill("solid", fgColor="EEF2FF")
        white_fill = PatternFill("solid", fgColor="FFFFFF")
        zmw_fill = PatternFill("solid", fgColor="E8F4FD")
        summary_label_fill = PatternFill("solid", fgColor="2C5282")
        summary_value_fill = PatternFill("solid", fgColor="FFFFFF")
        receipt_ok_fill = PatternFill("solid", fgColor="C6EFCE")
        receipt_pending_fill = PatternFill("solid", fgColor="FFEB9C")
        receipt_missing_fill = PatternFill("solid", fgColor="FFCCCC")

        thin = Side(style="thin", color="CCCCCC")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        title_font = Font(name="Calibri", bold=True, size=12, color="FFFFFF")
        meta_font = Font(name="Calibri", italic=True, size=10, color="475569")
        hdr_font = Font(name="Calibri", bold=True, size=10, color="FFFFFF")
        cell_font = Font(name="Calibri", size=11, color="000000")
        summary_label_font = Font(name="Calibri", bold=True, size=10, color="FFFFFF")
        summary_value_font = Font(name="Calibri", bold=True, size=11, color="000000")
        amount_font = Font(name="Calibri", size=11, color="000000")
        red_font = Font(name="Calibri", bold=True, size=11, color="9C0006")
        green_font = Font(name="Calibri", bold=True, size=11, color="276221")
        receipt_ok_font = Font(name="Calibri", bold=True, size=11, color="276221")
        receipt_pending_font = Font(name="Calibri", bold=True, size=11, color="9C6500")
        receipt_missing_font = Font(name="Calibri", bold=True, size=11, color="9C0006")

        def _style_merged_row(row: int, fill=None, font=None, align=None, apply_border: bool = False) -> None:
            for col in range(1, n_cols + 1):
                cell = ws.cell(row, col)
                if fill is not None:
                    cell.fill = fill
                if font is not None:
                    cell.font = font
                if align is not None:
                    cell.alignment = align
                if apply_border:
                    cell.border = border

        record_count = summary.get("record_count", len(rows))
        ws.merge_cells(f"A1:{last_col}1")
        ws["A1"] = f"TRUCK OVERVIEW  -  {truck}   ({record_count:,} records)"
        ws["A1"].font = title_font
        ws["A1"].fill = title_fill
        ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
        _style_merged_row(1, fill=title_fill, font=title_font,
                          align=Alignment(horizontal="left", vertical="center"))
        ws.row_dimensions[1].height = 18

        ws.merge_cells(f"A2:{last_col}2")
        ws["A2"] = (
            f"Source: {self._source_cb.currentText()}  |  "
            f"Search: {self._search_text() or 'All'}  |  "
            f"Date Range: {self._format_date_range_label()}  |  "
            f"Exported: {datetime.now().strftime('%d %b %Y %H:%M')}"
        )
        ws["A2"].font = meta_font
        ws["A2"].alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[2].height = 16

        # Summary strip (bordered, same navy/white language as results.xlsx headers)
        summary_pairs = [
            ("Records", summary["record_count"]),
            ("Sources", summary["source_count"]),
            ("TZS Total", summary["tzs_total"]),
            ("USD Total", summary["usd_total"]),
            ("ZMW Total", summary["zmw_total"]),
            ("Fuel Liters", summary["liters_total"]),
        ]
        summary_row = 4
        for idx, (label, value) in enumerate(summary_pairs):
            col = 1 + idx
            if col > n_cols:
                break
            label_cell = ws.cell(summary_row, col, label)
            label_cell.fill = summary_label_fill
            label_cell.font = summary_label_font
            label_cell.border = border
            label_cell.alignment = Alignment(horizontal="center", vertical="center")

            value_cell = ws.cell(summary_row + 1, col, value)
            value_cell.fill = summary_value_fill
            value_cell.font = summary_value_font
            value_cell.border = border
            value_cell.alignment = Alignment(horizontal="center", vertical="center")
            if isinstance(value, float):
                value_cell.number_format = '#,##0.00'
            elif isinstance(value, int):
                value_cell.number_format = '#,##0'

        headers = [c[0] for c in _COLS]
        table_row = 7
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(table_row, col, header)
            cell.fill = header_fill
            cell.border = border
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[table_row].height = 18

        for i, row in enumerate(rows, start=1):
            date_value = row.get("date")
            date_txt = (
                date_value.strftime("%d/%m/%Y")
                if hasattr(date_value, "strftime") and date_value.year > 1
                else ""
            )
            receipt = (row.get("receipt_status") or "").strip().lower()
            receipt_txt = receipt.title() if receipt else ""
            tzs_amt, usd_amt, zmw_amt = _amount_columns(row.get("currency", ""), row.get("amount"))
            values = [
                date_txt,
                row.get("source", ""),
                row.get("description", ""),
                row.get("reference", ""),
                row.get("truck_value", ""),
                tzs_amt,
                usd_amt,
                zmw_amt,
                row.get("liters"),
                row.get("rate"),
                row.get("station", ""),
                receipt_txt,
            ]
            ws.append(values)
            excel_row = ws.max_row
            is_zmw = _normalize_currency(row.get("currency") or "") == "ZMW"
            fill = zmw_fill if is_zmw else (alt_fill if i % 2 == 0 else white_fill)
            for col in range(1, len(values) + 1):
                cell = ws.cell(excel_row, col)
                cell.fill = fill
                cell.border = border
                cell.font = cell_font
                cell.alignment = Alignment(vertical="center", wrap_text=col in (2, 3, 4, 11))

            for col in (6, 7, 8, 9, 10):
                ws.cell(excel_row, col).font = amount_font
                ws.cell(excel_row, col).alignment = Alignment(horizontal="right", vertical="center")
            ws.cell(excel_row, 6).number_format = '#,##0'
            ws.cell(excel_row, 7).number_format = '#,##0.00'
            ws.cell(excel_row, 8).number_format = '#,##0'
            ws.cell(excel_row, 9).number_format = '#,##0.00'
            ws.cell(excel_row, 10).number_format = '#,##0.00'

            amount = row.get("amount")
            cur = _normalize_currency(row.get("currency") or "")
            if isinstance(amount, (int, float)):
                amount_col = { "TZS": 6, "USD": 7, "ZMW": 8 }.get(cur)
                if amount_col:
                    if amount < 0:
                        ws.cell(excel_row, amount_col).font = red_font
                    elif cur == "USD":
                        ws.cell(excel_row, amount_col).font = green_font

            # Receipt status chips (same Valid/Expired treatment as results.xlsx)
            receipt_cell = ws.cell(excel_row, 12)
            receipt_cell.alignment = Alignment(horizontal="center", vertical="center")
            if receipt == "received":
                receipt_cell.fill = receipt_ok_fill
                receipt_cell.font = receipt_ok_font
            elif receipt == "pending":
                receipt_cell.fill = receipt_pending_fill
                receipt_cell.font = receipt_pending_font
            elif receipt == "missing":
                receipt_cell.fill = receipt_missing_fill
                receipt_cell.font = receipt_missing_font

        widths = [12, 18, 36, 22, 14, 14, 12, 12, 10, 10, 20, 12]
        for idx, width in enumerate(widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = width
        ws.freeze_panes = f"A{table_row + 1}"
        ws.auto_filter.ref = f"A{table_row}:{last_col}{ws.max_row}"

        source_tag = self._selected_source()
        default_name = f"Truck_Overview_{truck}_{source_tag}.xlsx".replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Truck Overview", default_name, "Excel Files (*.xlsx)"
        )
        if not path:
            return
        if not path.endswith(".xlsx"):
            path += ".xlsx"
        try:
            wb.save(path)
            QMessageBox.information(self, "Export Complete", f"Excel report saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Could not save file:\n{exc}")

    def _export_pdf(self) -> None:
        self._start_background_task(self._do_export_pdf(), "PDF export")

    async def _do_export_pdf(self) -> None:
        truck = self._active_truck or self._selected_truck()
        if not truck:
            QMessageBox.warning(self, "Export", "Select a truck first.")
            return
        if not self._has_valid_date_range(warn=True):
            return

        from tahmeed.services.accountant_service import (
            get_truck_overview_records,
            get_truck_overview_summary,
        )

        try:
            date_from, date_to = self._date_filters()
            rows = await get_truck_overview_records(
                truck=truck,
                search=self._search_text(),
                source=self._selected_source(),
                date_from=date_from,
                date_to=date_to,
                limit=5000,
                skip=0,
            )
            summary = await get_truck_overview_summary(
                truck=truck,
                search=self._search_text(),
                source=self._selected_source(),
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Failed to prepare export data:\n{exc}")
            return

        if not rows:
            QMessageBox.information(self, "Export", "No records match the current truck and filters.")
            return

        source_tag = self._selected_source()
        default_name = f"Truck_Overview_{truck}_{source_tag}.pdf".replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Truck Overview PDF", default_name, "PDF Files (*.pdf)"
        )
        if not path:
            return
        if not path.endswith(".pdf"):
            path += ".pdf"

        def esc(value) -> str:
            text = "" if value is None else str(value)
            return (
                text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

        body_rows = []
        for row in rows:
            date_value = row.get("date")
            date_txt = date_value.strftime("%d %b %Y") if hasattr(date_value, "strftime") and date_value.year > 1 else ""
            amount_value = row.get("amount")
            tzs_amt, usd_amt, zmw_amt = _amount_columns(row.get("currency", ""), amount_value)
            cur = _normalize_currency(row.get("currency") or "")
            amount_class = ""
            if isinstance(amount_value, (int, float)) and amount_value < 0:
                amount_class = "amount-neg"
            elif isinstance(amount_value, (int, float)) and cur == "USD":
                amount_class = "amount-pos"

            def _amt_td(value, currency: str) -> str:
                if value is None:
                    return "<td style='text-align:right;'>—</td>"
                cls = amount_class if _normalize_currency(row.get("currency") or "") == currency else ""
                return (
                    f"<td class='{cls}' style='text-align:right;'>"
                    f"{esc(_fmt_currency_cell(currency, value))}</td>"
                )

            row_class = " class='zmw-row'" if cur == "ZMW" else ""
            body_rows.append(
                f"<tr{row_class}>"
                f"<td>{esc(date_txt)}</td>"
                f"<td>{esc(row.get('source', ''))}</td>"
                f"<td>{esc(row.get('description', ''))}</td>"
                f"<td>{esc(row.get('reference', ''))}</td>"
                f"<td>{esc(row.get('truck_value', ''))}</td>"
                f"{_amt_td(tzs_amt, 'TZS')}"
                f"{_amt_td(usd_amt, 'USD')}"
                f"{_amt_td(zmw_amt, 'ZMW')}"
                f"<td style='text-align:right;'>{esc(_fmt_num(row.get('liters'), 0))}</td>"
                f"<td style='text-align:right;'>{esc(_fmt_num(row.get('rate'), 2))}</td>"
                f"<td>{esc(row.get('station', ''))}</td>"
                f"<td style='text-align:center;'>{esc((row.get('receipt_status') or '').title())}</td>"
                "</tr>"
            )

        summary_cards = [
            ("Records", f"{summary['record_count']:,}"),
            ("Sources", f"{summary['source_count']:,}"),
            ("TZS Total", esc(_fmt_amount('TZS', summary['tzs_total']))),
            ("USD Total", esc(_fmt_amount('USD', summary['usd_total']))),
            ("ZMW Total", esc(_fmt_amount('ZMW', summary['zmw_total']))),
            ("Fuel Liters", esc(_fmt_num(summary['liters_total'], 0))),
        ]
        summary_html = "".join(
            "<div class='summary-card'>"
            f"<div class='summary-label'>{label}</div>"
            f"<div class='summary-value'>{value}</div>"
            "</div>"
            for label, value in summary_cards
        )

        html = f"""
        <html>
        <head>
          <style>
            @page {{ margin: 18px 20px; }}
            body {{ font-family: Segoe UI, Arial, sans-serif; font-size: 9px; color: #111827; }}
            .report-shell {{ border: 1px solid #CBD5E1; }}
            .report-head {{ background: #1B2B4B; color: #FFFFFF; padding: 14px 18px; }}
            .company {{ font-size: 16px; font-weight: 700; }}
            .report-title {{ font-size: 11px; font-weight: 600; margin-top: 3px; }}
            .report-meta {{ background: #EFF6FF; padding: 10px 18px; border-top: 1px solid #CBD5E1; border-bottom: 1px solid #CBD5E1; color: #334155; }}
            .summary-grid {{ width: 100%; border-spacing: 10px; margin: 10px 8px 2px 8px; }}
            .summary-card {{ display: inline-block; width: 30%; min-width: 150px; margin: 4px 6px; padding: 10px 12px; border: 1px solid #D1D5DB; background: #FFFFFF; border-radius: 6px; }}
            .summary-label {{ font-size: 8px; font-weight: 700; color: #64748B; text-transform: uppercase; }}
            .summary-value {{ font-size: 13px; font-weight: 700; color: #0F172A; margin-top: 3px; }}
            .table-wrap {{ padding: 10px 12px 14px 12px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ border: 1px solid #CBD5E1; padding: 5px 6px; vertical-align: top; }}
            th {{ background: #0077C5; color: #FFFFFF; font-weight: 700; text-align: center; }}
            tbody tr:nth-child(even) td {{ background: #F8FAFC; }}
            .zmw-row td {{ background: #E8F4FD !important; }}
            .muted {{ color: #64748B; }}
            .amount-neg {{ color: #DC2626; font-weight: 700; }}
            .amount-pos {{ color: #16A34A; font-weight: 700; }}
            .footer-note {{ padding: 0 12px 14px 12px; color: #64748B; font-size: 8px; }}
          </style>
        </head>
        <body>
          <div class="report-shell">
            <div class="report-head">
              <div class="company">TAHMEED COACH TZ LTD</div>
              <div class="report-title">Truck Overview Expense Report - {esc(truck)}</div>
            </div>
            <div class="report-meta">
              <strong>Source:</strong> {esc(self._source_cb.currentText())}
              &nbsp;&nbsp;|&nbsp;&nbsp;
              <strong>Search:</strong> {esc(self._search_text() or 'All')}
              &nbsp;&nbsp;|&nbsp;&nbsp;
              <strong>Date Range:</strong> {esc(self._format_date_range_label())}
              &nbsp;&nbsp;|&nbsp;&nbsp;
              <strong>Exported:</strong> {esc(datetime.now().strftime('%d %b %Y %H:%M'))}
            </div>
            <div class="summary-grid">
              {summary_html}
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>{''.join(f'<th>{esc(h[0])}</th>' for h in _COLS)}</tr>
                </thead>
                <tbody>
                  {''.join(body_rows)}
                </tbody>
              </table>
            </div>
            <div class="footer-note">
              Zambia-related rows are highlighted in blue. This report consolidates truck expenses and fuel across all connected accountant sources.
            </div>
          </div>
        </body>
        </html>
        """

        try:
            doc = QTextDocument()
            doc.setHtml(html)
            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setPageOrientation(QPageLayout.Landscape)
            printer.setPageSize(QPageSize(QPageSize.A4))
            printer.setOutputFileName(path)
            doc.print_(printer)
            QMessageBox.information(self, "Export Complete", f"PDF report saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Could not create PDF:\n{exc}")

    def _start_background_task(self, coro, action: str) -> None:
        task = asyncio.ensure_future(coro)
        task.add_done_callback(lambda t: self._handle_task_result(t, action))

    def _handle_task_result(self, task: asyncio.Task, action: str) -> None:
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except Exception as err:
            QMessageBox.critical(self, action, f"{action} failed:\n{err}")
            return
        if exc is not None:
            QMessageBox.critical(self, action, f"{action} failed:\n{exc}")

    def _format_date_range_label(self) -> str:
        date_from, date_to = self._date_filters()
        if date_from and date_to:
            return f"{date_from.strftime('%d %b %Y')} to {date_to.strftime('%d %b %Y')}"
        if date_from:
            return f"From {date_from.strftime('%d %b %Y')}"
        if date_to:
            return f"Up to {date_to.strftime('%d %b %Y')}"
        return "All Dates"

    def _clear_results(self) -> None:
        self._debounce.stop()
        self._active_truck = ""
        self._page = 0
        self._total = 0
        self._truck_edit.clear()
        self._source_cb.setCurrentIndex(0)
        self._from_date.setDate(_MIN_FILTER_DATE)
        self._to_date.setDate(_MIN_FILTER_DATE)
        self._search.clear()
        self._subtitle.setText("Select a truck to gather cross-source expenses and fuel.")
        self._status.setText("No truck selected yet.")
        self._table.setRowCount(0)
        self._pager.update_state(0, 0, self._pager.page_size())
        self._records_card.set_value("—")
        self._sources_card.set_value("—", color=_AMBER)
        self._tzs_card.set_value("—")
        self._usd_card.set_value("—", color=_GREEN)
        self._zmw_card.set_value("—", color=_AMBER)
        self._liters_card.set_value("—", color=_RED)
