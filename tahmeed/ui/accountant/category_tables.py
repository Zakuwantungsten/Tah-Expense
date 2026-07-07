"""AccountantDashboard — Category Sub-Tables.

One reusable, read-only view per cashier-fed category (LATRA, C28, Mileage,
Council Fees, …). Each shows the *verified* transactions whose
``category_name`` matches, using the exact same column set as the cashier's
DailyRegister (excel_grid.py):

    S/NO · DATE · ITEM · DESCRIPTION · TRUCK NO. · MEMO · NOTES
         · TZS · RECEIPT · OWNERSHIP · APR BY

Design matches the Master Expenses / Verify grid: slate zebra stripes,
muted grey headers, colored receipt pill, red negatives.

The widget reuses the existing Master Expenses service queries
(``get_master_transactions`` etc.) with a fixed ``category`` filter — no data
duplication, the table is just a scoped query over the transactions
collection.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List, Optional

import qtawesome as qta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QComboBox, QPushButton, QMessageBox, QFileDialog,
    QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QFont, QColor, QBrush

from tahmeed.app_state import app_state
from tahmeed.models.transaction import Transaction

# ── Design tokens (match accountant dashboard palette) ──────────────────────────
_WHITE      = "#FFFFFF"
_BG         = "#F4F6F8"
_BORDER     = "#E5E7EB"
_BLUE       = "#0077C5"
_BLUE_L     = "#E8F4FD"   # row selection highlight (matches Master / Verify)
_STRIPE     = "#F1F5F9"   # slate zebra stripe (matches Master / Verify)
_GREEN      = "#16A34A"
_GREEN_L    = "#DCFCE7"
_AMBER      = "#D97706"
_AMBER_L    = "#FEF3C7"
_RED        = "#DC2626"
_RED_L      = "#FEE2E2"
_T1         = "#111827"
_T2         = "#6B7280"
_TM         = "#9CA3AF"
_HDR_BG     = "#F1F5F9"

_PAGE_SIZES = [25, 50, 100]
_ROW_H      = 28
_HDR_H      = 28

# (label, pixel width, right-aligned?, mono?)
_COLS = [
    ("S/NO",        52,  "center", False),
    ("DATE",        100, "left",   False),
    ("ITEM",        110, "left",   False),
    ("DESCRIPTION", 260, "left",   False),
    ("TRUCK NO.",   95,  "left",   False),
    ("MEMO",        140, "left",   False),
    ("NOTES",       56,  "center", False),
    ("TZS",         120, "right",  True),
    ("RECEIPT",     110, "center", False),
    ("OWNERSHIP",   95,  "left",   False),
    ("APR BY",      90,  "left",   False),
]

_MONTHS = [
    (0, "All Months"),
    (1, "Jan"), (2, "Feb"), (3, "Mar"), (4, "Apr"), (5, "May"), (6, "Jun"),
    (7, "Jul"), (8, "Aug"), (9, "Sep"), (10, "Oct"), (11, "Nov"), (12, "Dec"),
]

_RECEIPT_MAP = {
    "received": ("Received",   _GREEN, _GREEN_L),
    "pending":  ("Pending",    _AMBER, _AMBER_L),
    "missing":  ("No Receipt", _RED,   _RED_L),
}

# ── Category registry: sidebar key → (title / category_name, mdi icon) ──────────
#  The title doubles as the category_name filter (matches how categories are
#  stored on transactions and shown in the sidebar / verify inbox).
CATEGORY_DEFS: Dict[str, tuple] = {
    "mileage":         ("Mileage",                "mdi.road-variant"),
    "latra":           ("LATRA",                  "mdi.card-account-details-outline"),
    "c28":             ("C28",                    "mdi.file-document-outline"),
    "c40":             ("C40",                    "mdi.file-document-outline"),
    "carbon_permit":   ("Carbon & Permit",        "mdi.leaf"),
    "diesel_cash":     ("Diesel Cash",            "mdi.gas-station-outline"),
    "council_fees":    ("Council Fees",           "mdi.city-variant"),
    "return_weigh":    ("Return & Weighbridge",   "mdi.scale"),
    "parking_petroda": ("Parking Petroda",        "mdi.parking"),
    "backload":        ("Backload Facilitation",  "mdi.truck-delivery"),
    "rope_sealing":    ("Rope & Sealing",         "mdi.link-variant"),
    "radiation":       ("Radiation Taxes",        "mdi.radioactive"),
    "health_fee":      ("Health Fee",             "mdi.hospital-box"),
    "halmashauri":     ("Halmashauri Parking",    "mdi.parking"),
}

_TABLE_SS = (
    f"QTableWidget {{"
    f"  background: {_WHITE};"
    f"  gridline-color: {_BORDER};"
    f"  font-size: 11px; font-family:'Segoe UI';"
    f"  color: {_T1}; border: none;"
    f"}}"
    f"QTableWidget::item {{ padding: 2px 8px; border: none; }}"
    f"QTableWidget::item:selected {{ background: {_BLUE_L}; color: {_T1}; }}"
    f"QHeaderView::section {{"
    f"  background: {_HDR_BG}; color: {_T2};"
    f"  font-size: 10px; font-weight: 600; font-family:'Segoe UI';"
    f"  border: none; border-bottom: 1px solid {_BORDER};"
    f"  border-right: 1px solid {_BORDER}; padding: 0 8px; min-height: {_HDR_H}px;"
    f"}}"
    f"QHeaderView::section:hover {{ background: #E2E8F0; }}"
    f"QScrollBar:horizontal {{ background: {_BG}; height: 8px; margin: 0; }}"
    f"QScrollBar::handle:horizontal {{ background: #D1D5DB; border-radius: 4px; min-width: 24px; }}"
    f"QScrollBar:vertical {{ background: {_BG}; width: 8px; margin: 0; }}"
    f"QScrollBar::handle:vertical {{ background: #D1D5DB; border-radius: 4px; min-height: 24px; }}"
    f"QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}"
)


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _lbl(text: str = "", size: int = 13, weight: int = 400, color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color: {color}; font-size: {size}px; font-weight: {weight};"
        " font-family:'Segoe UI'; background: transparent;"
    )
    return w


def _input_ss() -> str:
    return (
        f"QLineEdit, QComboBox {{"
        f"  border: 1px solid {_BORDER}; border-radius: 5px;"
        f"  background: {_WHITE}; color: {_T1}; font-size: 12px;"
        "  font-family:'Segoe UI'; padding: 0 8px;"
        "  min-height: 32px; max-height: 32px; }}"
        f"QLineEdit:focus, QComboBox:focus {{ border-color: {_BLUE}; }}"
        "QComboBox::drop-down { border: none; width: 20px; }"
    )


def _btn(text: str, icon_name: str = "", primary: bool = False) -> QPushButton:
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
            "QPushButton:disabled { background: #93C5FD; }"
        )
    else:
        b.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px; font-size: 12px;"
            " font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
    return b


def _set_cell(table: QTableWidget, row: int, col: int, text: str,
              align: str = "left", bg: str = _WHITE, color: str = _T1,
              mono: bool = False) -> None:
    item = QTableWidgetItem(text)
    flag = {"left": Qt.AlignLeft, "right": Qt.AlignRight, "center": Qt.AlignHCenter}[align]
    item.setTextAlignment(int(flag) | int(Qt.AlignVCenter))
    item.setBackground(QBrush(QColor(bg)))
    item.setForeground(QBrush(QColor(color)))
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    if mono:
        f = QFont("Cascadia Code", 11)
        f.setStyleHint(QFont.Monospace)
        item.setFont(f)
    table.setItem(row, col, item)


def _receipt_badge(status: str, bg: str) -> QWidget:
    text, fg, badge_bg = _RECEIPT_MAP.get(status, ("—", _TM, "#F3F4F6"))
    container = QWidget()
    container.setStyleSheet(f"background: {bg};")
    hl = QHBoxLayout(container)
    hl.setContentsMargins(3, 3, 3, 3)
    hl.setAlignment(Qt.AlignCenter)
    if text == "—":
        pill = QLabel("—")
        pill.setStyleSheet(f"color: {_TM}; background: transparent; font-size: 12px;")
    else:
        pill = QLabel(text)
        pill.setAlignment(Qt.AlignCenter)
        pill.setStyleSheet(
            f"color: {fg}; background: {badge_bg}; border-radius: 9px;"
            " padding: 2px 8px; font-size: 10px; font-weight: 600;"
            " font-family:'Segoe UI';"
        )
    hl.addWidget(pill)
    return container


def _fmt_short(amount: float) -> str:
    a = abs(amount)
    if a >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{amount / 1_000:.0f}K"
    return f"{amount:,.0f}"


# ── CategoryTableWidget ──────────────────────────────────────────────────────────

class CategoryTableWidget(QWidget):
    """Read-only verified-transactions table scoped to a single category."""

    def __init__(self, category_name: str, title: str, icon_name: str,
                 description_filter: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._category = category_name
        self._title = title
        self._icon = icon_name
        self._description_filter = description_filter

        self._year = app_state.fiscal_year
        self._month = 0
        self._page = 0
        self._total = 0
        self._loading = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(lambda: asyncio.ensure_future(self._reload()))

        self._build()

    # ── Build ───────────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.setStyleSheet(f"background: {_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Title bar ──────────────────────────────────────────────────────
        title_bar = QFrame()
        title_bar.setFixedHeight(52)
        title_bar.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-bottom: 1px solid {_BORDER}; }}"
        )
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(20, 0, 20, 0)
        tb.setSpacing(12)

        try:
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(22, 22)
            icon_lbl.setPixmap(qta.icon(self._icon, color=_BLUE).pixmap(22, 22))
            icon_lbl.setStyleSheet("background: transparent;")
            tb.addWidget(icon_lbl)
        except Exception:
            pass

        tb.addWidget(_lbl(self._title, size=16, weight=700))
        self._count_lbl = _lbl("", size=12, color=_T2)
        tb.addWidget(self._count_lbl)
        tb.addStretch()

        tb.addWidget(_lbl("FY", size=12, color=_T2))
        self._fy_cb = QComboBox()
        current_yr = datetime.now().year
        for yr in range(current_yr - 3, current_yr + 2):
            self._fy_cb.addItem(str(yr), yr)
        idx = self._fy_cb.findData(self._year)
        self._fy_cb.setCurrentIndex(idx if idx >= 0 else self._fy_cb.count() - 2)
        self._fy_cb.setFixedWidth(80)
        self._fy_cb.setStyleSheet(_input_ss())
        self._fy_cb.currentIndexChanged.connect(self._on_year_changed)
        tb.addWidget(self._fy_cb)

        refresh_btn = QPushButton()
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; }"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
        try:
            refresh_btn.setIcon(qta.icon("mdi.refresh", color=_T2))
            refresh_btn.setIconSize(QSize(18, 18))
        except Exception:
            refresh_btn.setText("↻")
        refresh_btn.clicked.connect(self.refresh)
        tb.addWidget(refresh_btn)

        root.addWidget(title_bar)

        # ── Filter bar ─────────────────────────────────────────────────────
        filter_bar = QFrame()
        filter_bar.setFixedHeight(52)
        filter_bar.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-bottom: 1px solid {_BORDER}; }}"
        )
        fl = QHBoxLayout(filter_bar)
        fl.setContentsMargins(16, 0, 16, 0)
        fl.setSpacing(10)

        try:
            si = QLabel()
            si.setFixedSize(16, 16)
            si.setPixmap(qta.icon("mdi.magnify", color=_TM).pixmap(16, 16))
            si.setStyleSheet("background: transparent;")
            fl.addWidget(si)
        except Exception:
            pass

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search description or truck…")
        self._search.setFixedWidth(240)
        self._search.setStyleSheet(_input_ss())
        self._search.textChanged.connect(self._on_filter_changed)
        fl.addWidget(self._search)

        self._month_cb = QComboBox()
        for idx_m, label in _MONTHS:
            self._month_cb.addItem(label, idx_m)
        self._month_cb.setFixedWidth(120)
        self._month_cb.setStyleSheet(_input_ss())
        self._month_cb.currentIndexChanged.connect(self._on_month_changed)
        fl.addWidget(self._month_cb)

        self._rcpt_cb = QComboBox()
        for label, val in [("All Receipts", "all"), ("Received", "received"),
                           ("Pending", "pending"), ("No Receipt", "missing")]:
            self._rcpt_cb.addItem(label, val)
        self._rcpt_cb.setFixedWidth(128)
        self._rcpt_cb.setStyleSheet(_input_ss())
        self._rcpt_cb.currentIndexChanged.connect(self._on_filter_changed)
        fl.addWidget(self._rcpt_cb)

        fl.addStretch()

        export_btn = _btn("Export Excel", "mdi.microsoft-excel")
        export_btn.clicked.connect(self._on_export)
        fl.addWidget(export_btn)

        root.addWidget(filter_bar)

        # ── Table ──────────────────────────────────────────────────────────
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLS])
        self._table.setStyleSheet(_TABLE_SS)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setShowGrid(True)
        hdr = self._table.horizontalHeader()
        hdr.setSectionsMovable(False)
        hdr.setStretchLastSection(True)
        for i, (_, width, _a, _m) in enumerate(_COLS):
            self._table.setColumnWidth(i, width)
            hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        root.addWidget(self._table, 1)

        # ── Footer totals ──────────────────────────────────────────────────
        footer = QFrame()
        footer.setFixedHeight(40)
        footer.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-top: 2px solid {_BORDER}; }}"
        )
        fol = QHBoxLayout(footer)
        fol.setContentsMargins(16, 0, 16, 0)
        fol.setSpacing(20)
        fol.addWidget(_lbl("TOTAL (filtered view)", size=11, weight=700, color=_T2))
        self._tzs_lbl = _lbl("TZS  —", size=13, weight=700)
        self._tzs_lbl.setStyleSheet(
            f"color: {_T1}; font-size: 13px; font-weight: 700;"
            " font-family: 'Cascadia Code', 'Consolas', monospace; background: transparent;"
        )
        fol.addWidget(self._tzs_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {_BORDER};")
        fol.addWidget(sep)

        self._usd_lbl = _lbl("USD  —", size=13, weight=700)
        self._usd_lbl.setStyleSheet(
            f"color: {_T1}; font-size: 13px; font-weight: 700;"
            " font-family: 'Cascadia Code', 'Consolas', monospace; background: transparent;"
        )
        fol.addWidget(self._usd_lbl)
        fol.addStretch()
        self._footer_count = _lbl("", size=11, color=_T2)
        fol.addWidget(self._footer_count)
        root.addWidget(footer)

        # ── Pagination ─────────────────────────────────────────────────────
        pager = QFrame()
        pager.setFixedHeight(44)
        pager.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-top: 1px solid {_BORDER}; }}"
        )
        pl = QHBoxLayout(pager)
        pl.setContentsMargins(16, 0, 16, 0)
        pl.setSpacing(10)

        self._size_cb = QComboBox()
        for sz in _PAGE_SIZES:
            self._size_cb.addItem(f"Show {sz}", sz)
        self._size_cb.setFixedWidth(100)
        self._size_cb.setStyleSheet(_input_ss())
        self._size_cb.currentIndexChanged.connect(self._on_size_changed)
        pl.addWidget(self._size_cb)

        self._page_info = _lbl("—", size=12, color=_T2)
        pl.addWidget(self._page_info)
        pl.addStretch()

        self._prev_btn = _btn("← Prev", "mdi.chevron-left")
        self._prev_btn.setFixedWidth(88)
        self._prev_btn.clicked.connect(self._on_prev)
        pl.addWidget(self._prev_btn)

        self._next_btn = _btn("Next →", "mdi.chevron-right")
        self._next_btn.setFixedWidth(88)
        self._next_btn.clicked.connect(self._on_next)
        pl.addWidget(self._next_btn)
        root.addWidget(pager)

    # ── Public API ───────────────────────────────────────────────────────────
    def refresh(self) -> None:
        self._page = 0
        asyncio.ensure_future(self._reload())

    # ── Filter helpers ─────────────────────────────────────────────────────
    def _search_text(self) -> str:
        return self._search.text().strip()

    def _receipt_filter(self) -> str:
        return self._rcpt_cb.currentData() or "all"

    def _page_size(self) -> int:
        return self._size_cb.currentData() or _PAGE_SIZES[0]

    # ── Event handlers ─────────────────────────────────────────────────────
    def _on_year_changed(self) -> None:
        yr = self._fy_cb.currentData()
        if yr and yr != self._year:
            self._year = yr
            app_state.fiscal_year = yr
            self._page = 0
            asyncio.ensure_future(self._reload())

    def _on_month_changed(self) -> None:
        self._month = self._month_cb.currentData() or 0
        self._page = 0
        asyncio.ensure_future(self._reload())

    def _on_filter_changed(self) -> None:
        self._page = 0
        self._debounce.start()

    def _on_size_changed(self) -> None:
        self._page = 0
        asyncio.ensure_future(self._reload())

    def _on_prev(self) -> None:
        if self._page > 0:
            self._page -= 1
            asyncio.ensure_future(self._reload())

    def _on_next(self) -> None:
        size = self._page_size()
        max_pg = max(0, (self._total - 1) // size) if self._total else 0
        if self._page < max_pg:
            self._page += 1
            asyncio.ensure_future(self._reload())

    # ── Reload ─────────────────────────────────────────────────────────────
    async def _reload(self) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            from tahmeed.services.accountant_service import (
                get_master_transactions, count_master_transactions, get_master_totals,
            )
            size = self._page_size()
            skip = self._page * size
            kw = dict(
                year=self._year, month=self._month,
                search=self._search_text(), truck="",
                category=self._category, receipt=self._receipt_filter(),
                description=self._description_filter,
            )
            txs, total, totals = await asyncio.gather(
                get_master_transactions(
                    **kw, sort_field="date", sort_asc=False, limit=size, skip=skip,
                ),
                count_master_transactions(**kw),
                get_master_totals(**kw),
            )
            self._total = total
            self._populate(txs, skip)
            self._tzs_lbl.setText(f"TZS  {totals['tzs']:,.0f}")
            self._tzs_lbl.setStyleSheet(
                f"color: {_RED if totals['tzs'] < 0 else _T1};"
                " font-size: 13px; font-weight: 700;"
                " font-family: 'Cascadia Code', 'Consolas', monospace; background: transparent;"
            )
            usd = totals["usd"]
            self._usd_lbl.setText(f"USD  ${usd:,.2f}" if usd else "USD  —")
            self._footer_count.setText(f"{total:,} records")
            self._count_lbl.setText(f"{total:,} records")
            self._update_pager(total, size)
        except Exception as exc:
            self._table.setRowCount(0)
            self._page_info.setText(f"Failed to load: {exc}")
        finally:
            self._loading = False

    def _populate(self, txs: List[Transaction], skip: int) -> None:
        self._table.setRowCount(len(txs))
        for i, tx in enumerate(txs):
            row_bg = _STRIPE if i % 2 else _WHITE
            _set_cell(self._table, i, 0, str(skip + i + 1), "center", row_bg)
            _set_cell(self._table, i, 1,
                      tx.date.strftime("%d %b %Y") if tx.date else "—", "left", row_bg)
            _set_cell(self._table, i, 2, tx.item or "—", "left", row_bg)
            _set_cell(self._table, i, 3, tx.description or "—", "left", row_bg)
            _set_cell(self._table, i, 4, tx.truck_number or "—", "left", row_bg)
            _set_cell(self._table, i, 5, tx.memo or "—", "left", row_bg)
            _set_cell(self._table, i, 6, "✓" if tx.notes_flag else "—", "center",
                      row_bg, color=_BLUE if tx.notes_flag else _TM)

            # TZS
            if tx.currency == "TZS":
                tzs_txt = f"{tx.amount:,.0f}"
                tzs_col = _RED if tx.amount < 0 else _T1
            else:
                tzs_txt, tzs_col = "—", _TM
            _set_cell(self._table, i, 7, tzs_txt, "right", row_bg, color=tzs_col, mono=True)

            # Receipt badge (cell widget paints the row stripe behind it)
            self._table.setCellWidget(i, 8, _receipt_badge(tx.receipt_status or "pending", row_bg))

            _set_cell(self._table, i, 9, tx.ownership or "—", "left", row_bg)
            _set_cell(self._table, i, 10, tx.approver or "—", "left", row_bg)
            self._table.setRowHeight(i, _ROW_H)

    def _update_pager(self, total: int, size: int) -> None:
        max_pg = max(0, (total - 1) // size) if total else 0
        self._prev_btn.setEnabled(self._page > 0)
        self._next_btn.setEnabled(self._page < max_pg)
        start = self._page * size + 1 if total else 0
        end = min((self._page + 1) * size, total)
        self._page_info.setText(
            f"Showing {start:,}–{end:,} of {total:,}  ·  Page {self._page + 1} of {max_pg + 1}"
        )

    # ── Excel export ───────────────────────────────────────────────────────
    def _on_export(self) -> None:
        asyncio.ensure_future(self._do_export())

    async def _do_export(self) -> None:
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Side, Border
        except ImportError:
            QMessageBox.critical(
                self, "Missing Dependency",
                "openpyxl is required for Excel export.\n\nRun: pip install openpyxl",
            )
            return

        from tahmeed.services.accountant_service import get_master_transactions
        kw = dict(
            year=self._year, month=self._month,
            search=self._search_text(), truck="",
            category=self._category, receipt=self._receipt_filter(),
            description=self._description_filter,
        )
        try:
            txs = await get_master_transactions(
                **kw, sort_field="date", sort_asc=False, limit=10_000, skip=0,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Failed to fetch data: {exc}")
            return

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = self._title[:28]

        ws.merge_cells("A1:K1")
        ws["A1"] = "TAHMEED COACH TZ LTD"
        ws["A1"].font = Font(name="Segoe UI", bold=True, size=14, color="1B2B4B")
        ws["A1"].alignment = Alignment(horizontal="center")
        month_label = dict(_MONTHS).get(self._month, "All Months")
        ws.merge_cells("A2:K2")
        ws["A2"] = f"{self._title} — FY {self._year}  |  {month_label}"
        ws["A2"].font = Font(name="Segoe UI", bold=True, size=11, color="374151")
        ws["A2"].alignment = Alignment(horizontal="center")
        ws.append([])

        headers = [c[0] for c in _COLS]
        ws.append(headers)
        hdr_row = ws.max_row
        grey = PatternFill("solid", fgColor="F1F5F9")
        for cell in ws[hdr_row]:
            cell.font = Font(name="Segoe UI", bold=True, size=10, color="6B7280")
            cell.fill = grey
            cell.alignment = Alignment(horizontal="center", vertical="center")

        stripe = PatternFill("solid", fgColor="F1F5F9")
        white = PatternFill("solid", fgColor="FFFFFF")
        mono = Font(name="Cascadia Code", size=10)
        red = Font(name="Cascadia Code", size=10, color="DC2626")
        tzs_total = usd_total = 0.0
        for i, tx in enumerate(txs):
            if tx.currency == "TZS":
                tzs_val, usd_val = tx.amount, None
                tzs_total += tx.amount
            else:
                tzs_val, usd_val = None, tx.amount
                usd_total += tx.amount
            receipt_str = {"received": "Received", "pending": "Pending",
                           "missing": "No Receipt"}.get(tx.receipt_status or "", "")
            ws.append([
                i + 1,
                tx.date.strftime("%d-%b-%Y") if tx.date else "",
                tx.item or "", tx.description or "", tx.truck_number or "",
                tx.memo or "", "Yes" if tx.notes_flag else "",
                tzs_val, receipt_str, tx.ownership or "", tx.approver or "",
            ])
            r = ws.max_row
            fill = stripe if i % 2 else white
            for cell in ws[r]:
                cell.fill = fill
                cell.alignment = Alignment(vertical="center")
            c = ws.cell(r, 8)
            if tzs_val is not None:
                c.font = red if tzs_val < 0 else mono
                c.number_format = "#,##0"
                c.alignment = Alignment(horizontal="right", vertical="center")

        ws.append([])
        ws.append(["", "", "", "TOTAL", "", "", "", tzs_total or "", "", "", ""])
        total_r = ws.max_row
        ws.cell(total_r, 4).font = Font(name="Segoe UI", bold=True, size=11)
        if tzs_total:
            c = ws.cell(total_r, 8)
            c.font = Font(name="Cascadia Code", bold=True, size=11,
                          color="DC2626" if tzs_total < 0 else "111827")
            c.number_format = "#,##0"
            c.alignment = Alignment(horizontal="right", vertical="center")

        widths = [6, 12, 14, 34, 12, 20, 7, 15, 13, 13, 12]
        for idx, w in enumerate(widths, 1):
            ws.column_dimensions[ws.cell(1, idx).column_letter].width = w
        ws.freeze_panes = ws.cell(hdr_row + 1, 1)

        month_tag = dict(_MONTHS).get(self._month, "All").replace(" ", "")
        safe_title = self._title.replace(" ", "_").replace("&", "and").replace("/", "-")
        default = f"{safe_title}_FY{self._year}_{month_tag}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {self._title}", default, "Excel Files (*.xlsx)"
        )
        if path:
            try:
                wb.save(path)
                QMessageBox.information(
                    self, "Export Complete",
                    f"Exported {len(txs):,} records to:\n{path}",
                )
            except Exception as exc:
                QMessageBox.critical(self, "Save Error", f"Could not save file:\n{exc}")
