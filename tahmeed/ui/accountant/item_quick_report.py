"""Item Account QuickReport — verified/master transactions for one catalog item.

Column set matches Master Expenses. Filter bar: Dates preset, From/To,
description kinds (sub-items), and Sort By.
"""

from __future__ import annotations

import asyncio
import calendar
from datetime import date
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from tahmeed.models.transaction import Transaction
from tahmeed.services.category_service import item_key
from tahmeed.ui.accountant.date_filters import (
    add_from_to_editors, read_from_to, sync_from_to,
)
from tahmeed.ui.cashier.register_delegates import format_register_date
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"
_HDR_BG = "#F1F5F9"
_STRIPE = "#F1F5F9"
_BLUE_L = "#E8F4FD"
_ROW_H = 28
_PAGE_SIZE = 100

# Master Expenses columns (no running Balance on this tab)
_COLS = [
    ("S/NO", 52, "center"),
    ("DATE", 72, "left"),
    ("ITEM", 110, "left"),
    ("DESCRIPTION", 200, "left"),
    ("TRUCK NO", 95, "left"),
    ("MEMO", 120, "left"),
    ("REF_FLOAT", 110, "left"),
    ("TZS", 110, "right"),
    ("USD", 100, "right"),
    ("RECEIPT", 100, "center"),
    ("OWNERSHIP", 90, "left"),
    ("APPROVED BY", 100, "left"),
    ("CASHIER", 100, "left"),
]
_COL_DEFAULTS = [c[1] for c in _COLS]

_RECEIPT_LABELS = {
    "received": "Received",
    "pending": "Pending",
    "missing": "No Receipt",
    "no_receipt": "No Receipt",
}

_SORT_OPTS = [
    ("Default", "date", True),
    ("Date (newest)", "date", False),
    ("Date (oldest)", "date", True),
    ("Amount (high–low)", "amount", False),
    ("Amount (low–high)", "amount", True),
    ("Description", "description", True),
]

_TABLE_SS = (
    f"QTableWidget {{"
    f"  background: {_WHITE}; gridline-color: {_BORDER};"
    f"  font-size: 11px; font-family:'Segoe UI';"
    f"  color: {_T1}; border: 1px solid {_BORDER};"
    f"}}"
    f"QTableWidget::item {{ padding: 2px 8px; border: none; }}"
    f"QTableWidget::item:selected {{ background: {_BLUE_L}; color: {_T1}; }}"
    f"QHeaderView::section {{"
    f"  background: {_HDR_BG}; color: {_T2};"
    f"  font-size: 10px; font-weight: 600; font-family:'Segoe UI';"
    f"  border: none; border-bottom: 1px solid {_BORDER};"
    f"  border-right: 1px solid {_BORDER};"
    f"  padding: 0 8px; min-height: 28px;"
    f"}}"
    f"QHeaderView::section:hover {{ background: #E2E8F0; }}"
    f"QScrollBar:vertical {{ background: {_BG}; width: 8px; margin: 0; }}"
    f"QScrollBar::handle:vertical {{ background: #D1D5DB; border-radius: 4px; min-height: 24px; }}"
    f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ width: 0; height: 0; }}"
)

_INPUT_SS = (
    f"QComboBox, QDateEdit {{"
    f"  border: 1px solid {_BORDER}; border-radius: 5px;"
    f"  background: {_WHITE}; color: {_T1}; font-size: 12px;"
    "  font-family:'Segoe UI'; padding: 0 8px;"
    "  min-height: 30px; max-height: 30px; }}"
    f"QComboBox:focus, QDateEdit:focus {{ border-color: #0077C5; }}"
    f"QComboBox::drop-down {{ border: none; width: 22px; }}"
)


def _lbl(text: str = "", size: int = 12, weight: int = 400, color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color: {color}; font-size: {size}px; font-weight: {weight};"
        " font-family:'Segoe UI'; background: transparent;"
    )
    return w


def _cell_font(*, mono: bool = False, bold: bool = False) -> QFont:
    f = QFont("Cascadia Code" if mono else "Segoe UI")
    if mono:
        f.setStyleHint(QFont.Monospace)
    f.setPixelSize(11)
    f.setBold(bold)
    return f


def _set_cell(
    table: QTableWidget,
    row: int,
    col: int,
    text: str,
    align: str,
    row_bg: str,
    *,
    color: str = _T1,
    mono: bool = False,
) -> None:
    it = QTableWidgetItem(text)
    flag = Qt.AlignVCenter
    if align == "center":
        flag |= Qt.AlignHCenter
    elif align == "right":
        flag |= Qt.AlignRight
    else:
        flag |= Qt.AlignLeft
    it.setTextAlignment(flag)
    it.setBackground(QBrush(QColor(row_bg)))
    it.setForeground(QBrush(QColor(color)))
    it.setFont(_cell_font(mono=mono))
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    table.setItem(row, col, it)


def _short_name(name: str) -> str:
    parts = (name or "").strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1][0]}."
    return name or "—"


def _ref_float_display(tx: Transaction) -> str:
    text = (getattr(tx, "ref_float", None) or "").strip()
    if text:
        return text.upper()
    if tx.notes_flag:
        return "REFUND TO FLOAT"
    return ""


def _receipt_label(status: str) -> str:
    key = (status or "pending").strip().lower().replace(" ", "_")
    if key in ("no", "n/a", "none"):
        key = "no_receipt"
    return _RECEIPT_LABELS.get(key, "—")


def _used_amount(value: float) -> float:
    """Amount used on this tab — always positive (negatives are float/use signs)."""
    return abs(float(value or 0.0))


class ItemQuickReportView(QWidget):
    """Read-only master-column table for one item's verified transactions."""

    # company year/label suffix, scope line under Account QuickReport
    header_context_changed = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._category = ""
        self._page = 0
        self._total = 0
        self._loading = False
        self._scroll_loading = False
        self._loaded = 0
        self._syncing_dates = False
        self._filter_debounce = QTimer(self)
        self._filter_debounce.setSingleShot(True)
        self._filter_debounce.setInterval(300)
        self._filter_debounce.timeout.connect(self._on_filter_commit)
        self._build()

    def _build(self) -> None:
        self.setStyleSheet(f"background: {_WHITE};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_filter_bar())

        self._account_bar = QFrame()
        self._account_bar.setFixedHeight(28)
        self._account_bar.setStyleSheet(
            f"QFrame {{ background: {_HDR_BG}; border: 1px solid {_BORDER};"
            f" border-bottom: none; }}"
        )
        abl = QHBoxLayout(self._account_bar)
        abl.setContentsMargins(10, 0, 10, 0)
        self._account_bar_lbl = _lbl("", size=12, weight=700, color=_T1)
        abl.addWidget(self._account_bar_lbl)
        abl.addStretch()
        root.addWidget(self._account_bar)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLS])
        self._table.setStyleSheet(_TABLE_SS)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setShowGrid(True)
        self._table.setAlternatingRowColors(False)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        hdr = self._table.horizontalHeader()
        hdr.setSectionsMovable(False)
        hdr.setStretchLastSection(True)
        for i, (_, width, _) in enumerate(_COLS):
            self._table.setColumnWidth(i, width)
            hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._table, "item_quick_report", _COL_DEFAULTS,
        )
        self._table.verticalScrollBar().valueChanged.connect(self._on_scroll)
        root.addWidget(self._table, 1)

        footer = QFrame()
        footer.setFixedHeight(40)
        footer.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-top: 2px solid {_BORDER}; }}"
        )
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(12, 0, 12, 0)
        fl.setSpacing(16)

        fl.addWidget(_lbl("TOTAL", size=11, weight=700, color=_T2))
        self._tzs_lbl = _lbl("TZS  —", size=13, weight=700)
        self._tzs_lbl.setStyleSheet(
            f"color: {_T1}; font-size: 13px; font-weight: 700;"
            " font-family:'Cascadia Code','Consolas',monospace;"
            " background: transparent;"
        )
        fl.addWidget(self._tzs_lbl)
        self._usd_lbl = _lbl("USD  —", size=12, weight=600, color=_T1)
        fl.addWidget(self._usd_lbl)
        self._count_lbl = _lbl("", size=11, color=_T2)
        fl.addWidget(self._count_lbl)
        fl.addStretch()

        self._page_info = _lbl("—", size=11, color=_T2)
        fl.addWidget(self._page_info)

        root.addWidget(footer)
        self._loading_overlay = LoadingOverlay(self, "Loading report…")

    def _build_filter_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(48)
        bar.setStyleSheet(
            f"QFrame {{ background: {_BG}; border: 1px solid {_BORDER};"
            f" border-bottom: none; }}"
        )
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(10, 0, 10, 0)
        hl.setSpacing(8)

        hl.addWidget(_lbl("Dates", size=11, color=_T2))
        self._dates_cb = QComboBox()
        self._dates_cb.setFixedWidth(130)
        self._dates_cb.setStyleSheet(_INPUT_SS)
        self._dates_cb.currentIndexChanged.connect(self._on_dates_preset)
        hl.addWidget(self._dates_cb)

        self._from_date, self._to_date = add_from_to_editors(
            hl, self._on_from_to_changed, input_ss=_INPUT_SS, lbl_factory=_lbl,
            optional=True, width=118,
        )

        hl.addWidget(_lbl("Description", size=11, color=_T2))
        self._desc_cb = QComboBox()
        self._desc_cb.setFixedWidth(180)
        self._desc_cb.setStyleSheet(_INPUT_SS)
        self._desc_cb.addItem("All", "")
        self._desc_cb.currentIndexChanged.connect(self._on_filter_changed)
        hl.addWidget(self._desc_cb)

        hl.addWidget(_lbl("Sort By", size=11, color=_T2))
        self._sort_cb = QComboBox()
        self._sort_cb.setFixedWidth(150)
        self._sort_cb.setStyleSheet(_INPUT_SS)
        for label, field, asc in _SORT_OPTS:
            self._sort_cb.addItem(label, (field, asc))
        self._sort_cb.currentIndexChanged.connect(self._on_filter_changed)
        hl.addWidget(self._sort_cb)

        hl.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.setFixedHeight(30)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:hover {{ background: {_WHITE}; color: {_T1}; }}"
        )
        clear_btn.clicked.connect(self._clear_filters)
        hl.addWidget(clear_btn)

        self._rebuild_dates_options([])
        return bar

    @staticmethod
    def _pager_btn_ss() -> str:
        return (
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 4px;"
            " font-size: 11px; font-family:'Segoe UI'; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
            f"QPushButton:disabled {{ color: {_TM}; }}"
        )

    def clear(self) -> None:
        self._category = ""
        self._page = 0
        self._total = 0
        self._loaded = 0
        self._table.setRowCount(0)
        self._account_bar_lbl.setText("")
        self._tzs_lbl.setText("TZS  —")
        self._usd_lbl.setText("USD  —")
        self._count_lbl.setText("")
        self._page_info.setText("—")
        self._reset_filters_ui()

    def load(self, category_name: str) -> None:
        self._category = (category_name or "").strip()
        self._page = 0
        self._account_bar_lbl.setText(self._category)
        self._reset_filters_ui()
        asyncio.ensure_future(self._prepare_and_reload())

    def _reset_filters_ui(self) -> None:
        self._syncing_dates = True
        try:
            self._dates_cb.blockSignals(True)
            self._desc_cb.blockSignals(True)
            self._sort_cb.blockSignals(True)
            self._rebuild_dates_options([])
            self._dates_cb.setCurrentIndex(0)
            sync_from_to(self._from_date, self._to_date, 0, 0, optional=True)
            self._desc_cb.clear()
            self._desc_cb.addItem("All", "")
            self._sort_cb.setCurrentIndex(0)
        finally:
            self._dates_cb.blockSignals(False)
            self._desc_cb.blockSignals(False)
            self._sort_cb.blockSignals(False)
            self._syncing_dates = False

    def _rebuild_dates_options(self, years: List[int]) -> None:
        current = self._dates_cb.currentData()
        self._dates_cb.blockSignals(True)
        self._dates_cb.clear()
        self._dates_cb.addItem("All", ("all", None))
        self._dates_cb.addItem("This Month", ("this_month", None))
        self._dates_cb.addItem("This Year", ("this_year", None))
        self._dates_cb.addItem("Last Year", ("last_year", None))
        for yr in years:
            self._dates_cb.addItem(str(yr), ("year", yr))
        self._dates_cb.addItem("Custom", ("custom", None))
        idx = 0
        if current is not None:
            for i in range(self._dates_cb.count()):
                if self._dates_cb.itemData(i) == current:
                    idx = i
                    break
        self._dates_cb.setCurrentIndex(idx)
        self._dates_cb.blockSignals(False)

    async def _prepare_and_reload(self) -> None:
        if not self._category:
            return
        try:
            from tahmeed.services.accountant_service import get_master_available_years
            from tahmeed.services.subtable_service import get_subtables

            years, subs = await asyncio.gather(
                get_master_available_years(),
                get_subtables(item_key(self._category)),
            )
            # Drop headroom-only padding years with no real need — keep unique sorted desc
            years = sorted({int(y) for y in years}, reverse=True)
            self._rebuild_dates_options(years)

            self._desc_cb.blockSignals(True)
            self._desc_cb.clear()
            self._desc_cb.addItem("All", "")
            for sub in subs:
                if not getattr(sub, "active", True):
                    continue
                match = (sub.match or sub.name or "").strip()
                if match:
                    self._desc_cb.addItem(sub.name, match)
            self._desc_cb.blockSignals(False)
        except Exception:
            pass
        await self._reload()

    def _on_filter_changed(self) -> None:
        self._page = 0
        self._filter_debounce.start()

    def _on_filter_commit(self) -> None:
        asyncio.ensure_future(self._reload())

    def _on_scroll(self, _value: int) -> None:
        bar = self._table.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if bar.value() >= bar.maximum() - 24:
            asyncio.ensure_future(self._load_more())

    def _fill_if_needed(self) -> None:
        bar = self._table.verticalScrollBar()
        if (
            not self._loading
            and not self._scroll_loading
            and self._loaded < self._total
            and bar.maximum() <= 0
        ):
            asyncio.ensure_future(self._load_more())

    def _on_dates_preset(self) -> None:
        if self._syncing_dates:
            return
        data = self._dates_cb.currentData()
        if not data:
            return
        kind, year = data
        today = date.today()
        self._syncing_dates = True
        try:
            if kind == "all":
                sync_from_to(self._from_date, self._to_date, 0, 0, optional=True)
            elif kind == "this_month":
                sync_from_to(
                    self._from_date, self._to_date, today.year, today.month,
                    optional=True,
                )
            elif kind == "this_year":
                sync_from_to(
                    self._from_date, self._to_date, today.year, 0, optional=True,
                )
            elif kind == "last_year":
                sync_from_to(
                    self._from_date, self._to_date, today.year - 1, 0, optional=True,
                )
            elif kind == "year" and year:
                sync_from_to(self._from_date, self._to_date, int(year), 0, optional=True)
            elif kind == "custom":
                pass
        finally:
            self._syncing_dates = False
        self._on_filter_changed()

    def _on_from_to_changed(self) -> None:
        if self._syncing_dates:
            return
        # Manual From/To → mark Dates as Custom
        self._syncing_dates = True
        try:
            for i in range(self._dates_cb.count()):
                data = self._dates_cb.itemData(i)
                if data and data[0] == "custom":
                    self._dates_cb.blockSignals(True)
                    self._dates_cb.setCurrentIndex(i)
                    self._dates_cb.blockSignals(False)
                    break
        finally:
            self._syncing_dates = False
        self._on_filter_changed()

    def _clear_filters(self) -> None:
        self._page = 0
        self._syncing_dates = True
        try:
            self._dates_cb.blockSignals(True)
            self._desc_cb.blockSignals(True)
            self._sort_cb.blockSignals(True)
            self._dates_cb.setCurrentIndex(0)
            sync_from_to(self._from_date, self._to_date, 0, 0, optional=True)
            self._desc_cb.setCurrentIndex(0)
            self._sort_cb.setCurrentIndex(0)
        finally:
            self._dates_cb.blockSignals(False)
            self._desc_cb.blockSignals(False)
            self._sort_cb.blockSignals(False)
            self._syncing_dates = False
        asyncio.ensure_future(self._reload())

    def _query_kwargs(self) -> dict:
        date_from, date_to = read_from_to(
            self._from_date, self._to_date, optional=True,
        )
        description = self._desc_cb.currentData() or ""
        sort = self._sort_cb.currentData() or ("date", True)
        sort_field, sort_asc = sort
        return {
            "description": description,
            "date_from": date_from,
            "date_to": date_to,
            "sort_field": sort_field,
            "sort_asc": bool(sort_asc),
        }

    def _header_context(self) -> Tuple[str, str]:
        """Return (year_label, scope_label) for the report header."""
        date_from, date_to = read_from_to(
            self._from_date, self._to_date, optional=True,
        )
        desc = self._desc_cb.currentText() or "All"
        desc_scope = "All Transactions" if desc == "All" else desc

        if date_from is None and date_to is None:
            return str(date.today().year), desc_scope

        if date_from and date_to:
            if (
                date_from.year == date_to.year
                and date_from.month == 1 and date_from.day == 1
                and date_to.month == 12 and date_to.day == 31
            ):
                return str(date_from.year), desc_scope
            if (
                date_from.year == date_to.year
                and date_from.month == date_to.month
                and date_from.day == 1
                and date_to.day == calendar.monthrange(date_to.year, date_to.month)[1]
            ):
                month_name = date_from.strftime("%b %Y")
                return str(date_from.year), f"{desc_scope}  ·  {month_name}"
            if date_from.year == date_to.year:
                return (
                    str(date_from.year),
                    f"{desc_scope}  ·  {date_from.strftime('%d %b')}–{date_to.strftime('%d %b %Y')}",
                )
            return (
                f"{date_from.year}–{date_to.year}",
                f"{desc_scope}  ·  {date_from.strftime('%d %b %Y')}–{date_to.strftime('%d %b %Y')}",
            )

        if date_from:
            return str(date_from.year), f"{desc_scope}  ·  From {date_from.strftime('%d %b %Y')}"
        assert date_to is not None
        return str(date_to.year), f"{desc_scope}  ·  To {date_to.strftime('%d %b %Y')}"

    async def _reload(self) -> None:
        if self._loading or not self._category:
            return
        self._loading = True
        self._page = 0
        self._loaded = 0
        self._loading_overlay.show_loading("Loading report…")
        try:
            from tahmeed.services.accountant_service import (
                get_category_report_transactions,
                get_category_report_totals,
                get_cashier_names,
            )

            kw = self._query_kwargs()
            year_lbl, scope_lbl = self._header_context()
            self.header_context_changed.emit(year_lbl, scope_lbl)
            self._account_bar_lbl.setText(self._category)

            txs, totals = await asyncio.gather(
                get_category_report_transactions(
                    self._category,
                    description=kw["description"],
                    date_from=kw["date_from"],
                    date_to=kw["date_to"],
                    sort_field=kw["sort_field"],
                    sort_asc=kw["sort_asc"],
                    limit=_PAGE_SIZE,
                    skip=0,
                ),
                get_category_report_totals(
                    self._category,
                    description=kw["description"],
                    date_from=kw["date_from"],
                    date_to=kw["date_to"],
                ),
            )
            self._total = int(totals.get("count") or 0)
            self._loaded = len(txs)
            cashier_ids = [tx.cashier_id for tx in txs if tx.cashier_id]
            names = await get_cashier_names(cashier_ids) if cashier_ids else {}
            self._populate(txs, 0, names, append=False)
            self._update_footer(totals)
            self._fill_if_needed()
        except Exception as exc:
            self._table.setRowCount(0)
            self._loaded = 0
            self._page_info.setText(f"Failed to load: {exc}")
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()
            self._update_footer_status()

    async def _load_more(self) -> None:
        if self._scroll_loading or self._loading or not self._category:
            return
        if self._loaded >= self._total:
            return
        self._scroll_loading = True
        self._update_footer_status()
        try:
            from tahmeed.services.accountant_service import (
                get_category_report_transactions,
                get_cashier_names,
            )

            kw = self._query_kwargs()
            skip = self._loaded
            txs = await get_category_report_transactions(
                self._category,
                description=kw["description"],
                date_from=kw["date_from"],
                date_to=kw["date_to"],
                sort_field=kw["sort_field"],
                sort_asc=kw["sort_asc"],
                limit=_PAGE_SIZE,
                skip=skip,
            )
            if not txs:
                return
            cashier_ids = [tx.cashier_id for tx in txs if tx.cashier_id]
            names = await get_cashier_names(cashier_ids) if cashier_ids else {}
            self._populate(txs, skip, names, append=True)
            self._loaded += len(txs)
            self._fill_if_needed()
        except Exception as exc:
            self._page_info.setText(f"Failed to load more: {exc}")
        finally:
            self._scroll_loading = False
            self._update_footer_status()

    def _update_footer(self, totals: dict) -> None:
        tzs = _used_amount(totals.get("tzs") or 0.0)
        usd = _used_amount(totals.get("usd") or 0.0)
        count = int(totals.get("count") or 0)
        self._total = count
        self._tzs_lbl.setText(f"TZS  {tzs:,.0f}")
        self._tzs_lbl.setStyleSheet(
            f"color: {_T1}; font-size: 13px; font-weight: 700;"
            " font-family:'Cascadia Code','Consolas',monospace;"
            " background: transparent;"
        )
        self._usd_lbl.setText(f"USD  ${usd:,.2f}" if usd else "USD  —")
        self._usd_lbl.setStyleSheet(
            f"color: {_T1}; font-size: 12px; font-weight: 600;"
            " font-family:'Cascadia Code','Consolas',monospace;"
            " background: transparent;"
        )
        self._count_lbl.setText(f"{count:,} entries")
        self._update_footer_status()

    def _update_footer_status(self) -> None:
        loaded = self._loaded
        total = self._total
        if self._loading or self._scroll_loading:
            suffix = "  ·  Loading…"
        elif loaded >= total and total:
            suffix = ""
        elif total:
            suffix = "  ·  Scroll for more"
        else:
            suffix = ""
        self._page_info.setText(f"Showing {loaded:,} of {total:,}{suffix}")

    def _populate(
        self,
        txs: List[Transaction],
        skip: int,
        cashier_names: Dict,
        *,
        append: bool = False,
    ) -> None:
        """Fill (or append) rows — black text; amounts shown as positive used."""
        start = self._table.rowCount() if append else 0
        if not append:
            self._table.setRowCount(len(txs))
        else:
            self._table.setRowCount(start + len(txs))

        for i, tx in enumerate(txs):
            r = start + i
            row_bg = _STRIPE if r % 2 else _WHITE
            tzs_raw, usd_raw = tx.money_parts()
            tzs = _used_amount(tzs_raw) if tzs_raw else 0.0
            usd = _used_amount(usd_raw) if usd_raw else 0.0
            ref = _ref_float_display(tx)
            rcpt_txt = _receipt_label(tx.receipt_status or "pending")
            cashier = (
                _short_name(cashier_names.get(tx.cashier_id, ""))
                if tx.cashier_id else "—"
            )
            item_str = tx.item or tx.category_name or "—"
            date_txt = format_register_date(tx.date) if tx.date else "—"

            _set_cell(self._table, r, 0, str(skip + i + 1), "center", row_bg)
            _set_cell(self._table, r, 1, date_txt or "—", "left", row_bg)
            _set_cell(self._table, r, 2, item_str, "left", row_bg)
            _set_cell(self._table, r, 3, tx.description or "—", "left", row_bg)
            _set_cell(self._table, r, 4, tx.truck_number or "—", "left", row_bg)
            _set_cell(self._table, r, 5, tx.memo or "—", "left", row_bg)
            _set_cell(self._table, r, 6, ref or "—", "left", row_bg)
            _set_cell(
                self._table, r, 7,
                f"{tzs:,.0f}" if tzs_raw else "—", "right", row_bg, mono=True,
            )
            _set_cell(
                self._table, r, 8,
                f"{usd:,.2f}" if usd_raw else "—", "right", row_bg, mono=True,
            )
            _set_cell(self._table, r, 9, rcpt_txt, "center", row_bg)
            _set_cell(self._table, r, 10, tx.ownership or "—", "left", row_bg)
            _set_cell(self._table, r, 11, tx.approver or "—", "left", row_bg)
            _set_cell(self._table, r, 12, cashier, "left", row_bg)
            self._table.setRowHeight(r, _ROW_H)
