"""AccountantDashboard — Verification Inbox (QB-style redesign)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from bson import ObjectId
import qtawesome as qta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QLineEdit, QComboBox, QPushButton, QTextEdit,
    QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QColor, QFont

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User

# ── Design tokens ──────────────────────────────────────────────────────────────
_WHITE     = "#FFFFFF"
_BG        = "#F4F6F8"
_BORDER    = "#E5E7EB"
_BLUE      = "#0077C5"
_BLUE_L    = "#E8F4FD"
_GREEN     = "#16A34A"
_AMBER     = "#D97706"
_AMBER_L   = "#FEF3C7"
_RED       = "#DC2626"
_T1        = "#111827"
_T2        = "#6B7280"
_TM        = "#9CA3AF"
_QB_HDR_BG = "#EFF6FF"
_QB_HDR_FG = "#1E3A5F"
_QB_SEL_BG = "#DBEAFE"
_QB_SEL_FG = "#1E3A5F"

_AUTO_THRESHOLD = 0.70
_PAGE_SIZES     = [25, 50, 100]
_DATE_OPTS      = ["All Dates", "Today", "Last 7 Days", "Last 30 Days", "This Month"]

_RECEIPT_DISPLAY: Dict[str, str] = {
    "pending":  "Pending",
    "received": "No Receipt",
    "missing":  "Missing",
}

# Table columns
_HEADERS = [
    "", "S/N", "DATE", "CASHIER", "ITEM", "DESCRIPTION",
    "TRUCK", "AMOUNT", "MEO", "NOTES", "RECEIPT", "APP BY",
]
_COL_CHK   = 0
_COL_SN    = 1
_COL_DATE  = 2
_COL_CASH  = 3
_COL_ITEM  = 4
_COL_DESC  = 5
_COL_TRUCK = 6
_COL_AMT   = 7
_COL_MEO   = 8
_COL_NOTES = 9
_COL_RCPT  = 10
_COL_APP   = 11
_NCOLS     = 12

# Fixed widths per column (0 = stretch)
_COL_WIDTHS = [32, 44, 80, 95, 90, 0, 90, 115, 90, 130, 80, 80]


def _resolve_date_filter(label: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    if label == "Today":
        return today, today.replace(hour=23, minute=59, second=59)
    if label == "Last 7 Days":
        return today - timedelta(days=7), today.replace(hour=23, minute=59, second=59)
    if label == "Last 30 Days":
        return today - timedelta(days=30), today.replace(hour=23, minute=59, second=59)
    if label == "This Month":
        return today.replace(day=1), today.replace(hour=23, minute=59, second=59)
    return None, None


# ── Primitive helpers ──────────────────────────────────────────────────────────

def _lbl(text: str = "", size: int = 13, weight: int = 400,
         color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI',sans-serif;background:transparent;"
    )
    return w


def _btn(text: str, icon: str = "", primary: bool = True,
         danger: bool = False, height: int = 34) -> QPushButton:
    b = QPushButton(f"  {text}" if icon else text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    if icon:
        try:
            b.setIcon(qta.icon(icon, color="#FFFFFF" if (primary or danger) else _T1))
            b.setIconSize(QSize(16, 16))
        except Exception:
            pass
    if danger:
        ss = (f"QPushButton{{background:{_RED};color:#FFF;border:none;border-radius:5px;"
              "font-size:12px;font-weight:600;font-family:'Segoe UI',sans-serif;padding:0 14px;}}"
              "QPushButton:hover{background:#B91C1C;}"
              "QPushButton:disabled{background:#FCA5A5;}")
    elif primary:
        ss = (f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
              "font-size:12px;font-weight:600;font-family:'Segoe UI',sans-serif;padding:0 14px;}}"
              "QPushButton:hover{background:#005EA3;}"
              "QPushButton:disabled{background:#93C5FD;}")
    else:
        ss = (f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
              f"border-radius:5px;font-size:12px;font-family:'Segoe UI',sans-serif;padding:0 14px;}}"
              f"QPushButton:hover{{background:{_BG};}}"
              f"QPushButton:disabled{{color:{_TM};}}")
    b.setStyleSheet(ss)
    return b


def _input_ss() -> str:
    return (
        f"QLineEdit,QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
        f"background:{_WHITE};color:{_T1};font-size:12px;"
        "font-family:'Segoe UI',sans-serif;padding:0 8px;"
        "min-height:32px;max-height:32px;}}"
        f"QLineEdit:focus,QComboBox:focus{{border-color:{_BLUE};}}"
        "QComboBox::drop-down{border:none;width:20px;}"
    )


def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{_BORDER};")
    return f


def _table_style() -> str:
    return (
        f"QTableWidget{{background:{_WHITE};gridline-color:{_BORDER};"
        "border:none;font-size:12px;font-family:'Segoe UI',sans-serif;}}"
        f"QTableWidget::item{{padding:0 6px;color:{_T1};}}"
        f"QTableWidget::item:selected{{background:{_QB_SEL_BG};color:{_QB_SEL_FG};}}"
        f"QTableWidget::item:alternate{{background:#F9FAFB;}}"
        f"QHeaderView::section{{background:{_QB_HDR_BG};color:{_QB_HDR_FG};"
        "font-size:11px;font-weight:700;font-family:'Segoe UI',sans-serif;"
        f"border:none;border-right:1px solid {_BORDER};"
        f"border-bottom:2px solid {_BLUE};padding:0 6px;height:32px;}}"
        "QScrollBar:vertical{width:8px;background:transparent;}"
        "QScrollBar::handle:vertical{background:#D1D5DB;border-radius:4px;}"
        "QScrollBar:horizontal{height:8px;background:transparent;}"
        "QScrollBar::handle:horizontal{background:#D1D5DB;border-radius:4px;}"
    )


def _cell(text: str,
          align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
          mono: bool = False,
          color: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    if mono:
        item.setFont(QFont("Cascadia Code", 11))
    if color:
        item.setForeground(QColor(color))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _fmt_date(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%d %b %y")
    return str(dt) if dt else "—"


def _short_name(name: str) -> str:
    parts = (name or "").strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1][0]}."
    return name or "—"


# ── Filter toolbar ─────────────────────────────────────────────────────────────

class _FilterBar(QFrame):
    filter_changed   = Signal()
    approve_selected = Signal()
    reject_selected  = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setStyleSheet(
            f"QFrame{{background:{_WHITE};border-bottom:1px solid {_BORDER};}}"
        )
        self._build()

    def _build(self) -> None:
        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(10)

        try:
            icon = QLabel()
            icon.setFixedSize(16, 16)
            icon.setPixmap(qta.icon("mdi.magnify", color=_TM).pixmap(16, 16))
            icon.setStyleSheet("background:transparent;")
            hl.addWidget(icon)
        except Exception:
            pass

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search description, item, truck…")
        self._search.setFixedWidth(230)
        self._search.setStyleSheet(_input_ss())
        self._search.textChanged.connect(self.filter_changed)
        hl.addWidget(self._search)

        self._truck_cb = QComboBox()
        self._truck_cb.addItem("All Trucks")
        self._truck_cb.setFixedWidth(130)
        self._truck_cb.setStyleSheet(_input_ss())
        self._truck_cb.currentTextChanged.connect(self.filter_changed)
        hl.addWidget(self._truck_cb)

        self._cashier_cb = QComboBox()
        self._cashier_cb.addItem("All Cashiers", None)
        self._cashier_cb.setFixedWidth(130)
        self._cashier_cb.setStyleSheet(_input_ss())
        self._cashier_cb.currentTextChanged.connect(self.filter_changed)
        hl.addWidget(self._cashier_cb)

        self._date_cb = QComboBox()
        for opt in _DATE_OPTS:
            self._date_cb.addItem(opt)
        self._date_cb.setFixedWidth(120)
        self._date_cb.setStyleSheet(_input_ss())
        self._date_cb.currentTextChanged.connect(self.filter_changed)
        hl.addWidget(self._date_cb)

        hl.addStretch()

        self._approve_btn = _btn("Approve Selected", "mdi.check-all")
        self._approve_btn.setEnabled(False)
        self._approve_btn.clicked.connect(self.approve_selected)
        hl.addWidget(self._approve_btn)

        self._reject_btn = _btn("Reject Selected", "mdi.close-circle-multiple-outline",
                                danger=True)
        self._reject_btn.setEnabled(False)
        self._reject_btn.clicked.connect(self.reject_selected)
        hl.addWidget(self._reject_btn)

    def populate_trucks(self, trucks: List[str]) -> None:
        cur = self._truck_cb.currentText()
        self._truck_cb.blockSignals(True)
        self._truck_cb.clear()
        self._truck_cb.addItem("All Trucks")
        for t in trucks:
            self._truck_cb.addItem(t)
        idx = self._truck_cb.findText(cur)
        self._truck_cb.setCurrentIndex(max(0, idx))
        self._truck_cb.blockSignals(False)

    def populate_cashiers(self, items: List[Tuple[str, Optional[ObjectId]]]) -> None:
        cur = self._cashier_cb.currentText()
        self._cashier_cb.blockSignals(True)
        self._cashier_cb.clear()
        self._cashier_cb.addItem("All Cashiers", None)
        for name, cid in items:
            self._cashier_cb.addItem(name, cid)
        idx = self._cashier_cb.findText(cur)
        self._cashier_cb.setCurrentIndex(max(0, idx))
        self._cashier_cb.blockSignals(False)

    def set_bulk_enabled(self, enabled: bool) -> None:
        self._approve_btn.setEnabled(enabled)
        self._reject_btn.setEnabled(enabled)

    def search_text(self) -> str:
        return self._search.text().strip()

    def truck_filter(self) -> str:
        t = self._truck_cb.currentText()
        return "" if t == "All Trucks" else t

    def cashier_id_filter(self) -> Optional[ObjectId]:
        return self._cashier_cb.currentData()

    def date_filter(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        return _resolve_date_filter(self._date_cb.currentText())


# ── Pagination bar ─────────────────────────────────────────────────────────────

class _PaginationBar(QFrame):
    page_changed = Signal(int)
    size_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setStyleSheet(
            f"QFrame{{background:{_WHITE};border-top:1px solid {_BORDER};}}"
        )
        self._page = 0
        self._total = 0
        self._size = _PAGE_SIZES[0]
        self._build()

    def _build(self) -> None:
        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(10)

        self._size_cb = QComboBox()
        for sz in _PAGE_SIZES:
            self._size_cb.addItem(f"Show {sz}", sz)
        self._size_cb.setFixedWidth(100)
        self._size_cb.setStyleSheet(_input_ss())
        self._size_cb.currentIndexChanged.connect(self._on_size)
        hl.addWidget(self._size_cb)

        self._info = _lbl("—", size=12, color=_T2)
        hl.addWidget(self._info)
        hl.addStretch()

        self._prev = _btn("← Prev", "", primary=False)
        self._prev.setFixedWidth(90)
        self._prev.clicked.connect(self._go_prev)
        hl.addWidget(self._prev)

        self._next = _btn("Next →", "", primary=False)
        self._next.setFixedWidth(90)
        self._next.clicked.connect(self._go_next)
        hl.addWidget(self._next)

    def update_state(self, page: int, total: int, size: int) -> None:
        self._page, self._total, self._size = page, total, size
        max_p = max(0, (total - 1) // size) if total else 0
        self._prev.setEnabled(page > 0)
        self._next.setEnabled(page < max_p)
        s = page * size + 1 if total else 0
        e = min((page + 1) * size, total)
        self._info.setText(
            f"Showing {s}–{e} of {total}  ·  Page {page + 1} of {max_p + 1}"
        )

    def current_size(self) -> int:
        return self._size_cb.currentData() or _PAGE_SIZES[0]

    def _on_size(self) -> None:
        self._page = 0
        self.size_changed.emit(self._size_cb.currentData())

    def _go_prev(self) -> None:
        if self._page > 0:
            self.page_changed.emit(self._page - 1)

    def _go_next(self) -> None:
        max_p = max(0, (self._total - 1) // self._size) if self._total else 0
        if self._page < max_p:
            self.page_changed.emit(self._page + 1)


# ── Action panel ───────────────────────────────────────────────────────────────

class _ActionPanel(QFrame):
    """Pinned review panel that slides in below the table when a row is clicked."""
    approved  = Signal(object)       # tx._id
    rejected  = Signal(object, str)  # tx._id, reason
    saved_cat = Signal(object, str)  # tx._id, category
    dismissed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tx: Optional[Transaction] = None
        self.setObjectName("actionPanel")
        self.setStyleSheet(
            "QFrame#actionPanel{"
            f"background:#F0F7FF;border-top:2px solid {_BLUE};}}"
        )
        self._build()
        self.hide()

    def _build(self) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 10, 20, 10)
        vl.setSpacing(8)

        # Selected transaction info
        self._info_lbl = _lbl("", size=12, weight=600, color=_QB_HDR_FG)
        vl.addWidget(self._info_lbl)

        # Controls row
        ctrl = QWidget()
        ctrl.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(ctrl)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(16)

        # Category
        cat_w = QWidget(); cat_w.setStyleSheet("background:transparent;")
        cvl = QVBoxLayout(cat_w); cvl.setContentsMargins(0, 0, 0, 0); cvl.setSpacing(3)
        cvl.addWidget(_lbl("CATEGORY", size=9, weight=600, color=_T2))
        self._cat_combo = QComboBox()
        self._cat_combo.setFixedWidth(220)
        self._cat_combo.setStyleSheet(_input_ss())
        cvl.addWidget(self._cat_combo)
        hl.addWidget(cat_w)

        # Notes
        notes_w = QWidget(); notes_w.setStyleSheet("background:transparent;")
        nvl = QVBoxLayout(notes_w); nvl.setContentsMargins(0, 0, 0, 0); nvl.setSpacing(3)
        nvl.addWidget(_lbl("NOTES FOR CASHIER  (required to reject)",
                           size=9, weight=600, color=_T2))
        self._notes = QTextEdit()
        self._notes.setFixedHeight(54)
        self._notes.setPlaceholderText("Leave a reason if rejecting this entry…")
        self._notes.setStyleSheet(
            f"QTextEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            "font-family:'Segoe UI',sans-serif;padding:5px;}}"
            f"QTextEdit:focus{{border-color:{_BLUE};}}"
        )
        nvl.addWidget(self._notes)
        hl.addWidget(notes_w, 1)

        # Buttons
        btn_w = QWidget(); btn_w.setStyleSheet("background:transparent;")
        bvl = QVBoxLayout(btn_w); bvl.setContentsMargins(0, 0, 0, 0); bvl.setSpacing(5)
        bvl.addStretch()

        save_btn = _btn("Save & Next", "mdi.content-save-outline", primary=False, height=30)
        save_btn.clicked.connect(self._on_save)
        bvl.addWidget(save_btn)

        approve_btn = _btn("Approve", "mdi.check-circle-outline", height=30)
        approve_btn.clicked.connect(self._on_approve)
        bvl.addWidget(approve_btn)

        reject_btn = _btn("Reject & Return", "mdi.close-circle-outline", danger=True, height=30)
        reject_btn.clicked.connect(self._on_reject)
        bvl.addWidget(reject_btn)

        dismiss_btn = _btn("✕", "", primary=False, height=30)
        dismiss_btn.setFixedWidth(34)
        dismiss_btn.setToolTip("Close panel")
        dismiss_btn.clicked.connect(self.dismissed)
        bvl.addWidget(dismiss_btn)

        hl.addWidget(btn_w)
        vl.addWidget(ctrl)

    def load(self, tx: Transaction, cashier_name: str, categories: List[str]) -> None:
        self._tx = tx
        currency = tx.currency or "TZS"
        self._info_lbl.setText(
            f"{tx.truck_number or '—'}  ·  {_fmt_date(tx.date)}  ·  "
            f"{tx.description or '—'}  ·  {currency} {tx.amount:,.0f}"
            f"  ·  Cashier: {cashier_name or '—'}"
        )
        self._cat_combo.blockSignals(True)
        self._cat_combo.clear()
        for cat in categories:
            self._cat_combo.addItem(cat)
        if tx.category_name:
            idx = self._cat_combo.findText(tx.category_name)
            if idx >= 0:
                self._cat_combo.setCurrentIndex(idx)
            else:
                self._cat_combo.insertItem(0, tx.category_name)
                self._cat_combo.setCurrentIndex(0)
        self._cat_combo.blockSignals(False)
        self._notes.setPlainText(tx.rejection_reason or "")

    def _on_approve(self) -> None:
        if not self._tx:
            return
        cat = self._cat_combo.currentText()
        if cat and cat != self._tx.category_name:
            self.saved_cat.emit(self._tx._id, cat)
        self.approved.emit(self._tx._id)

    def _on_reject(self) -> None:
        if not self._tx:
            return
        note = self._notes.toPlainText().strip()
        if not note:
            QMessageBox.warning(
                self, "Note Required",
                "Please enter a reason before rejecting this entry.\n"
                "The cashier needs to know what to fix.",
            )
            return
        cat = self._cat_combo.currentText()
        if cat and cat != self._tx.category_name:
            self.saved_cat.emit(self._tx._id, cat)
        self.rejected.emit(self._tx._id, note)

    def _on_save(self) -> None:
        if not self._tx:
            return
        cat = self._cat_combo.currentText()
        if cat and cat != self._tx.category_name:
            self.saved_cat.emit(self._tx._id, cat)
        self.dismissed.emit()


# ── Main widget ────────────────────────────────────────────────────────────────

class VerifyInboxWidget(QWidget):
    badge_updated = Signal(int)

    def __init__(self, user: User, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._categories: List[str] = []
        self._transactions: List[Transaction] = []
        self._cashier_names: Dict = {}
        self._page = 0
        self._total = 0
        self._filters_loaded = False
        self._loading = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._reload)
        self._build()

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        title_bar = QFrame()
        title_bar.setFixedHeight(52)
        title_bar.setStyleSheet(
            f"QFrame{{background:{_WHITE};border-bottom:1px solid {_BORDER};}}"
        )
        tbl = QHBoxLayout(title_bar)
        tbl.setContentsMargins(20, 0, 20, 0)
        tbl.setSpacing(12)
        try:
            il = QLabel()
            il.setFixedSize(22, 22)
            il.setPixmap(qta.icon("mdi.inbox-arrow-down", color=_BLUE).pixmap(22, 22))
            il.setStyleSheet("background:transparent;")
            tbl.addWidget(il)
        except Exception:
            pass
        tbl.addWidget(_lbl("Verify Inbox", size=16, weight=700))
        self._pending_badge = QLabel("…")
        self._pending_badge.setAlignment(Qt.AlignCenter)
        self._pending_badge.setStyleSheet(
            "color:#FFFFFF;background:#D97706;border-radius:10px;"
            "padding:2px 8px;font-size:10px;font-weight:600;"
            "font-family:'Segoe UI',sans-serif;"
        )
        self._pending_badge.setMinimumWidth(36)
        tbl.addWidget(self._pending_badge)
        tbl.addStretch()
        refresh_btn = QPushButton()
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;border-radius:4px;}"
            f"QPushButton:hover{{background:{_BG};}}"
        )
        try:
            refresh_btn.setIcon(qta.icon("mdi.refresh", color=_T2))
            refresh_btn.setIconSize(QSize(18, 18))
        except Exception:
            refresh_btn.setText("↻")
        refresh_btn.clicked.connect(self.refresh)
        tbl.addWidget(refresh_btn)
        root.addWidget(title_bar)

        # Filter bar
        self._filter_bar = _FilterBar()
        self._filter_bar.filter_changed.connect(self._on_filter_changed)
        self._filter_bar.approve_selected.connect(self._on_bulk_approve)
        self._filter_bar.reject_selected.connect(self._on_bulk_reject)
        root.addWidget(self._filter_bar)

        # Content area
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(20, 16, 20, 12)
        cl.setSpacing(8)

        # QB-style table card
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};border-radius:6px;}}"
        )
        card_vl = QVBoxLayout(card)
        card_vl.setContentsMargins(0, 0, 0, 0)
        card_vl.setSpacing(0)
        self._table = self._build_table()
        card_vl.addWidget(self._table)
        cl.addWidget(card, 1)

        # Action panel
        self._panel = _ActionPanel()
        self._panel.approved.connect(self._on_approved)
        self._panel.rejected.connect(self._on_rejected)
        self._panel.saved_cat.connect(self._on_save_category)
        self._panel.dismissed.connect(self._panel.hide)
        cl.addWidget(self._panel)

        root.addWidget(content, 1)

        # Pagination
        self._pagination = _PaginationBar()
        self._pagination.page_changed.connect(self._on_page_changed)
        self._pagination.size_changed.connect(self._on_size_changed)
        root.addWidget(self._pagination)

    def _build_table(self) -> QTableWidget:
        t = QTableWidget(0, _NCOLS)
        t.setHorizontalHeaderLabels(_HEADERS)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(34)
        t.setShowGrid(True)
        t.setStyleSheet(
            _table_style() + f"QTableWidget{{alternate-background-color:#F9FAFB;}}"
        )
        hdr = t.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setMinimumSectionSize(28)
        for c, w in enumerate(_COL_WIDTHS):
            if w == 0:
                hdr.setSectionResizeMode(c, QHeaderView.Stretch)
            else:
                hdr.setSectionResizeMode(c, QHeaderView.Fixed)
                t.setColumnWidth(c, w)
        t.itemClicked.connect(self._on_item_clicked)
        t.itemChanged.connect(self._on_checkbox_changed)
        hdr.sectionClicked.connect(self._on_header_click)
        return t

    # ── Public API ──────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        asyncio.ensure_future(self._reload())

    # ── Event handlers ──────────────────────────────────────────────────────────

    def _on_filter_changed(self) -> None:
        self._page = 0
        self._debounce.start()

    def _on_page_changed(self, page: int) -> None:
        self._page = page
        asyncio.ensure_future(self._reload())

    def _on_size_changed(self, size: int) -> None:
        self._page = 0
        asyncio.ensure_future(self._reload())

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == _COL_CHK:
            return  # checkbox only — don't open panel
        row = item.row()
        if row < len(self._transactions):
            tx = self._transactions[row]
            cashier = self._cashier_names.get(tx.cashier_id, "") if tx.cashier_id else ""
            self._panel.load(tx, cashier, self._categories)
            self._panel.show()

    def _on_checkbox_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == _COL_CHK:
            self._update_bulk_buttons()

    def _on_header_click(self, col: int) -> None:
        if col != _COL_CHK:
            return
        any_unchecked = any(
            self._table.item(r, _COL_CHK) and
            self._table.item(r, _COL_CHK).checkState() == Qt.Unchecked
            for r in range(self._table.rowCount())
        )
        state = Qt.Checked if any_unchecked else Qt.Unchecked
        self._table.blockSignals(True)
        for r in range(self._table.rowCount()):
            it = self._table.item(r, _COL_CHK)
            if it:
                it.setCheckState(state)
        self._table.blockSignals(False)
        self._update_bulk_buttons()

    def _update_bulk_buttons(self) -> None:
        any_checked = any(
            self._table.item(r, _COL_CHK) and
            self._table.item(r, _COL_CHK).checkState() == Qt.Checked
            for r in range(self._table.rowCount())
        )
        self._filter_bar.set_bulk_enabled(any_checked)

    # ── Data load ───────────────────────────────────────────────────────────────

    async def _reload(self) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            from tahmeed.services.accountant_service import (
                get_unverified_filtered, count_unverified_filtered,
                get_unverified_trucks, get_unverified_cashier_ids,
                get_cashier_names, get_pending_count,
            )
            from tahmeed.services.category_service import get_all_categories

            size = self._pagination.current_size()
            skip = self._page * size
            search  = self._filter_bar.search_text()
            truck   = self._filter_bar.truck_filter()
            cid     = self._filter_bar.cashier_id_filter()
            df, dt  = self._filter_bar.date_filter()

            if not self._filters_loaded:
                cats, trucks, cashier_ids = await asyncio.gather(
                    get_all_categories(),
                    get_unverified_trucks(),
                    get_unverified_cashier_ids(),
                )
                self._categories = [c.name for c in cats]
                cname_map = await get_cashier_names(cashier_ids)
                self._filter_bar.populate_trucks(trucks)
                clist = sorted(
                    [(cname_map.get(ci, str(ci)), ci) for ci in cashier_ids],
                    key=lambda x: x[0],
                )
                self._filter_bar.populate_cashiers(clist)
                self._filters_loaded = True
            else:
                cname_map = {}

            txs, total, pending = await asyncio.gather(
                get_unverified_filtered(
                    search=search, truck=truck, cashier_id=cid,
                    date_from=df, date_to=dt, limit=size, skip=skip,
                ),
                count_unverified_filtered(
                    search=search, truck=truck, cashier_id=cid,
                    date_from=df, date_to=dt,
                ),
                get_pending_count(),
            )

            page_cids = list({tx.cashier_id for tx in txs if tx.cashier_id})
            if page_cids:
                page_names = await get_cashier_names(page_cids)
                cname_map.update(page_names)

            self._cashier_names = cname_map
            self._total = total
            self._fill_table(txs, cname_map, skip)
            self._pagination.update_state(self._page, total, size)
            self._pending_badge.setText(str(pending))

        except Exception as exc:
            self._show_empty(f"Failed to load: {exc}")
        finally:
            self._loading = False

    # ── Table fill ──────────────────────────────────────────────────────────────

    def _fill_table(self, txs: List[Transaction], cnames: Dict, skip: int) -> None:
        self._transactions = txs
        self._panel.hide()

        t = self._table
        t.clearSpans()
        t.blockSignals(True)
        t.setRowCount(0)

        if not txs:
            t.blockSignals(False)
            self._show_empty("No pending transactions match the current filters.")
            self._update_bulk_buttons()
            return

        for i, tx in enumerate(txs):
            r = t.rowCount()
            t.insertRow(r)

            # Col 0: Checkbox
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            chk.setCheckState(Qt.Unchecked)
            chk.setTextAlignment(Qt.AlignCenter)
            t.setItem(r, _COL_CHK, chk)

            # Col 1: S/N
            sn = QTableWidgetItem(str(skip + i + 1))
            sn.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            sn.setForeground(QColor(_T2))
            sn.setFlags(sn.flags() & ~Qt.ItemIsEditable)
            t.setItem(r, _COL_SN, sn)

            # Col 2: Date
            t.setItem(r, _COL_DATE, _cell(_fmt_date(tx.date), color=_T2))

            # Col 3: Cashier
            cashier = cnames.get(tx.cashier_id, "") if tx.cashier_id else ""
            t.setItem(r, _COL_CASH, _cell(_short_name(cashier)))

            # Col 4: Item
            t.setItem(r, _COL_ITEM, _cell(tx.item or "—"))

            # Col 5: Description
            t.setItem(r, _COL_DESC, _cell(tx.description or "—"))

            # Col 6: Truck
            t.setItem(r, _COL_TRUCK, _cell(tx.truck_number or "—", color=_T2))

            # Col 7: Amount  (monospace, right-aligned)
            currency = tx.currency or "TZS"
            amt = QTableWidgetItem(f"{currency} {tx.amount:,.0f}")
            amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            amt.setFont(QFont("Cascadia Code", 11))
            amt.setFlags(amt.flags() & ~Qt.ItemIsEditable)
            t.setItem(r, _COL_AMT, amt)

            # Col 8: Memo
            t.setItem(r, _COL_MEO, _cell(tx.memo or "—", color=_T2))

            # Col 9: Notes — "Refund to my float" or blank
            notes_text = "Refund to my float" if tx.notes_flag else ""
            n_item = _cell(notes_text)
            if tx.notes_flag:
                n_item.setForeground(QColor(_AMBER))
            t.setItem(r, _COL_NOTES, n_item)

            # Col 10: Receipt
            rs = (tx.receipt_status or "pending").lower()
            rcpt_text = _RECEIPT_DISPLAY.get(rs, "Pending")
            r_item = _cell(rcpt_text)
            if rs == "missing":
                r_item.setForeground(QColor(_RED))
            elif rs == "received":
                r_item.setForeground(QColor(_T2))
            else:
                r_item.setForeground(QColor(_AMBER))
            t.setItem(r, _COL_RCPT, r_item)

            # Col 11: App By
            t.setItem(r, _COL_APP, _cell(tx.approver or "—", color=_T2))

        t.blockSignals(False)
        self._update_bulk_buttons()

    def _show_empty(self, msg: str) -> None:
        t = self._table
        t.clearSpans()
        t.blockSignals(True)
        t.setRowCount(1)
        for c in range(_NCOLS):
            t.setItem(0, c, QTableWidgetItem(""))
        item = QTableWidgetItem(msg)
        item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        item.setForeground(QColor(_TM))
        item.setFlags(Qt.ItemIsEnabled)
        t.setItem(0, 0, item)
        t.setSpan(0, 0, 1, _NCOLS)
        t.blockSignals(False)

    # ── Action callbacks ────────────────────────────────────────────────────────

    def _on_approved(self, tx_id: ObjectId) -> None:
        asyncio.ensure_future(self._do_approve(tx_id))

    def _on_rejected(self, tx_id: ObjectId, reason: str) -> None:
        asyncio.ensure_future(self._do_reject(tx_id, reason))

    def _on_save_category(self, tx_id: ObjectId, category: str) -> None:
        asyncio.ensure_future(self._do_save_category(tx_id, category))

    def _on_bulk_approve(self) -> None:
        checked = [
            r for r in range(self._table.rowCount())
            if self._table.item(r, _COL_CHK) and
               self._table.item(r, _COL_CHK).checkState() == Qt.Checked
        ]
        if not checked:
            return
        tx_rows = [(r, self._transactions[r]) for r in checked if r < len(self._transactions)]
        auto = [(r, tx) for r, tx in tx_rows if tx.category_confidence >= _AUTO_THRESHOLD]
        low  = [(r, tx) for r, tx in tx_rows if tx.category_confidence < _AUTO_THRESHOLD]

        if not auto:
            QMessageBox.warning(
                self, "Cannot Bulk Approve",
                f"All {len(low)} selected transaction(s) have low category confidence "
                f"(< {int(_AUTO_THRESHOLD * 100)}%) and require individual review.\n"
                "Click each row to review and approve individually.",
            )
            return

        msg = f"Approve {len(auto)} auto-matched transaction(s)?"
        if low:
            msg += (
                f"\n\n⚠ {len(low)} transaction(s) with low confidence will be skipped "
                "and require individual review."
            )
        if QMessageBox.question(self, "Bulk Approve", msg,
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            tx_ids = [tx._id for _, tx in auto if tx._id]
            asyncio.ensure_future(self._do_bulk_approve(tx_ids))

    def _on_bulk_reject(self) -> None:
        QMessageBox.information(
            self, "Bulk Reject",
            "Bulk rejection requires individual notes per transaction.\n"
            "Click each row to review, fill in the note, then press Reject & Return.",
        )

    # ── Async service calls ─────────────────────────────────────────────────────

    async def _do_approve(self, tx_id: ObjectId) -> None:
        from tahmeed.services.accountant_service import approve_transaction, get_pending_count
        try:
            await approve_transaction(tx_id, self._user._id)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            self._panel.hide()
            asyncio.ensure_future(self._reload())
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to approve: {exc}")

    async def _do_reject(self, tx_id: ObjectId, reason: str) -> None:
        from tahmeed.services.accountant_service import reject_transaction, get_pending_count
        try:
            await reject_transaction(tx_id, reason)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            self._panel.hide()
            asyncio.ensure_future(self._reload())
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to reject: {exc}")

    async def _do_save_category(self, tx_id: ObjectId, category: str) -> None:
        from tahmeed.services.accountant_service import update_transaction_category
        try:
            await update_transaction_category(tx_id, category)
        except Exception:
            pass

    async def _do_bulk_approve(self, tx_ids: List[ObjectId]) -> None:
        from tahmeed.services.accountant_service import (
            bulk_approve_transactions, get_pending_count,
        )
        try:
            n = await bulk_approve_transactions(tx_ids, self._user._id)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            asyncio.ensure_future(self._reload())
            QMessageBox.information(self, "Done", f"Approved {n} transaction(s) successfully.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Bulk approve failed: {exc}")
