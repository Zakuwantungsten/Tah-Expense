"""AccountantDashboard — Verification Inbox (Task 4)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from bson import ObjectId
import qtawesome as qta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QScrollArea, QLineEdit, QComboBox, QPushButton,
    QCheckBox, QTextEdit, QSizePolicy, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User

# ── Design tokens ─────────────────────────────────────────────────────────────
_WHITE   = "#FFFFFF"
_BG      = "#F4F6F8"
_BORDER  = "#E5E7EB"
_BLUE    = "#0077C5"
_BLUE_L  = "#E8F4FD"
_GREEN   = "#16A34A"
_GREEN_L = "#DCFCE7"
_AMBER   = "#D97706"
_AMBER_L = "#FEF3C7"
_RED     = "#DC2626"
_RED_L   = "#FEE2E2"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"

_AUTO_THRESHOLD = 0.70  # confidence >= this is auto-matched (bulk-approve eligible)

_PAGE_SIZES = [25, 50, 100]

_DATE_OPTIONS: List[Tuple[str, Optional[datetime], Optional[datetime]]] = [
    ("All Dates", None, None),
    ("Today",     None, None),
    ("Last 7 Days", None, None),
    ("Last 30 Days", None, None),
    ("This Month", None, None),
]


def _resolve_date_filter(label: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    if label == "Today":
        return today, today.replace(hour=23, minute=59, second=59)
    if label == "Last 7 Days":
        return today - timedelta(days=7), today.replace(hour=23, minute=59, second=59)
    if label == "Last 30 Days":
        return today - timedelta(days=30), today.replace(hour=23, minute=59, second=59)
    if label == "This Month":
        start = today.replace(day=1)
        return start, today.replace(hour=23, minute=59, second=59)
    return None, None


# ── Primitive helpers ─────────────────────────────────────────────────────────

def _lbl(text: str = "", size: int = 13, weight: int = 400,
         color: str = _T1, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color: {color}; font-size: {size}px; font-weight: {weight};"
        " font-family: 'Segoe UI', sans-serif; background: transparent;"
    )
    if wrap:
        w.setWordWrap(True)
    return w


def _badge_lbl(text: str, fg: str, bg: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet(
        f"color: {fg}; background: {bg}; border-radius: 10px;"
        " padding: 2px 8px; font-size: 10px; font-weight: 600;"
        " font-family: 'Segoe UI', sans-serif;"
    )
    return lbl


def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {_BORDER};")
    return f


def _action_btn(text: str, icon_name: str, primary: bool = True,
                danger: bool = False) -> QPushButton:
    b = QPushButton()
    b.setText(f"  {text}")
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(34)
    try:
        b.setIcon(qta.icon(icon_name, color="#FFFFFF"))
        b.setIconSize(QSize(16, 16))
    except Exception:
        pass
    if danger:
        ss = (
            f"QPushButton {{ background: {_RED}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 12px; font-weight: 600;"
            " font-family: 'Segoe UI', sans-serif; padding: 0 14px; }}"
            f"QPushButton:hover {{ background: #B91C1C; }}"
            f"QPushButton:disabled {{ background: #FCA5A5; }}"
        )
    elif primary:
        ss = (
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 12px; font-weight: 600;"
            " font-family: 'Segoe UI', sans-serif; padding: 0 14px; }}"
            f"QPushButton:hover {{ background: #005EA3; }}"
            f"QPushButton:disabled {{ background: #93C5FD; }}"
        )
    else:
        ss = (
            f"QPushButton {{ background: {_WHITE}; color: {_T1}; border: 1px solid {_BORDER};"
            " border-radius: 5px; font-size: 12px;"
            " font-family: 'Segoe UI', sans-serif; padding: 0 14px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
            f"QPushButton:disabled {{ color: {_TM}; }}"
        )
    b.setStyleSheet(ss)
    return b


def _input_ss() -> str:
    return (
        f"QLineEdit, QComboBox {{ border: 1px solid {_BORDER}; border-radius: 5px;"
        f" background: {_WHITE}; color: {_T1}; font-size: 12px;"
        " font-family: 'Segoe UI', sans-serif; padding: 0 8px;"
        " min-height: 32px; max-height: 32px; }}"
        f"QLineEdit:focus, QComboBox:focus {{ border-color: {_BLUE}; }}"
        "QComboBox::drop-down { border: none; width: 20px; }"
    )


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%d %b %y") if dt else "—"


def _short_name(name: str) -> str:
    parts = name.strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1][0]}."
    return name or "—"


def _field_pair(label: str, value: str, label_w: int = 80) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    hl = QHBoxLayout(w)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(6)
    lbl = _lbl(label + ":", size=11, weight=600, color=_T2)
    lbl.setFixedWidth(label_w)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    val = _lbl(value or "—", size=12)
    val.setWordWrap(True)
    hl.addWidget(lbl)
    hl.addWidget(val, 1)
    return w


# ── Category confidence badge ─────────────────────────────────────────────────

def _cat_badge(category_name: Optional[str], confidence: float) -> QLabel:
    if not category_name:
        lbl = _badge_lbl("⚠ Unmatched", _AMBER, _AMBER_L)
    elif confidence >= _AUTO_THRESHOLD:
        pct = int(confidence * 100)
        lbl = _badge_lbl(f"✓ {category_name}  {pct}%", _GREEN, _GREEN_L)
    else:
        lbl = _badge_lbl(f"⚠ {category_name}", _AMBER, _AMBER_L)
    lbl.setMaximumWidth(160)
    return lbl


# ── Detail Panel ──────────────────────────────────────────────────────────────

class _DetailPanel(QFrame):
    approved    = Signal()
    rejected    = Signal(str)
    save_next   = Signal(str)   # str = new category name

    def __init__(
        self,
        tx: Transaction,
        cashier_name: str,
        categories: List[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tx = tx
        self.setObjectName("detailPanel")
        self.setStyleSheet(
            "QFrame#detailPanel {"
            "  background: #F0F7FF;"
            f"  border-top: 1px solid {_BORDER};"
            "}"
        )
        self._build(tx, cashier_name, categories)

    def _build(self, tx: Transaction, cashier_name: str, categories: List[str]) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(60, 14, 20, 14)
        vl.setSpacing(10)

        # ── Two-column field grid ─────────────────────────────────────────
        grid = QHBoxLayout()
        grid.setSpacing(32)

        left = QVBoxLayout()
        left.setSpacing(5)
        left.addWidget(_field_pair("Date",        _fmt_date(tx.date)))
        left.addWidget(_field_pair("Truck",        tx.truck_number or "—"))
        left.addWidget(_field_pair("LPO Ref",      tx.lpo_do or "—"))
        left.addWidget(_field_pair("DO No.",       tx.do_number or "—"))
        left.addWidget(_field_pair("Description",  tx.description or "—"))
        left.addWidget(_field_pair("Item",         tx.item or "—"))
        left.addWidget(_field_pair("Memo",         tx.memo or "—"))
        left.addWidget(_field_pair("Ownership",    tx.ownership or "—"))
        lw = QWidget(); lw.setStyleSheet("background: transparent;")
        lw.setLayout(left)
        grid.addWidget(lw, 1)

        right = QVBoxLayout()
        right.setSpacing(5)
        currency = tx.currency or "TZS"
        right.addWidget(_field_pair("Amount",      f"{currency} {tx.amount:,.0f}"))
        right.addWidget(_field_pair("Receipt",     (tx.receipt_status or "").replace("_", " ").title() or "—"))
        right.addWidget(_field_pair("Notes Flag",  "Yes" if tx.notes_flag else "No"))
        right.addWidget(_field_pair("Cashier",     cashier_name or "—"))
        right.addWidget(_field_pair("APR BY",      tx.approver or "—"))
        if tx.rejection_reason:
            right.addWidget(_field_pair("Prev. Note", tx.rejection_reason))
        rw = QWidget(); rw.setStyleSheet("background: transparent;")
        rw.setLayout(right)
        grid.addWidget(rw, 1)

        vl.addLayout(grid)
        vl.addWidget(_hsep())

        # ── Category edit + Notes ─────────────────────────────────────────
        edit_row = QHBoxLayout()
        edit_row.setSpacing(16)

        cat_col = QVBoxLayout()
        cat_col.setSpacing(4)
        cat_col.addWidget(_lbl("Category", size=11, weight=600, color=_T2))
        self._cat_combo = QComboBox()
        self._cat_combo.setStyleSheet(_input_ss())
        self._cat_combo.setFixedWidth(240)
        self._cat_combo.setMinimumHeight(34)
        self._cat_combo.setMaximumHeight(34)
        for cat in categories:
            self._cat_combo.addItem(cat)
        if tx.category_name:
            idx = self._cat_combo.findText(tx.category_name)
            if idx >= 0:
                self._cat_combo.setCurrentIndex(idx)
            else:
                self._cat_combo.insertItem(0, tx.category_name)
                self._cat_combo.setCurrentIndex(0)
        cat_col.addWidget(self._cat_combo)
        edit_row.addLayout(cat_col)

        notes_col = QVBoxLayout()
        notes_col.setSpacing(4)
        notes_col.addWidget(_lbl("Notes for cashier (required for rejection)", size=11, weight=600, color=_T2))
        self._notes = QTextEdit()
        self._notes.setFixedHeight(62)
        self._notes.setPlaceholderText("Leave a reason if rejecting this entry…")
        self._notes.setStyleSheet(
            f"QTextEdit {{ border: 1px solid {_BORDER}; border-radius: 5px;"
            f" background: {_WHITE}; color: {_T1}; font-size: 12px;"
            " font-family: 'Segoe UI', sans-serif; padding: 5px; }}"
            f"QTextEdit:focus {{ border-color: {_BLUE}; }}"
        )
        if tx.rejection_reason:
            self._notes.setPlainText(tx.rejection_reason)
        notes_col.addWidget(self._notes)
        edit_row.addLayout(notes_col, 1)

        vl.addLayout(edit_row)

        # ── Action buttons ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        save_btn = _action_btn("Save & Next", "mdi.content-save-outline", primary=False)
        save_btn.setToolTip("Save category change without approving or rejecting")
        save_btn.clicked.connect(self._on_save_next)
        btn_row.addWidget(save_btn)

        approve_btn = _action_btn("Approve", "mdi.check-circle-outline")
        approve_btn.clicked.connect(self._on_approve)
        btn_row.addWidget(approve_btn)

        reject_btn = _action_btn("Reject & Return", "mdi.close-circle-outline", danger=True)
        reject_btn.clicked.connect(self._on_reject)
        btn_row.addWidget(reject_btn)

        vl.addLayout(btn_row)

    def _current_category(self) -> str:
        return self._cat_combo.currentText()

    def _on_approve(self) -> None:
        self.approved.emit()

    def _on_reject(self) -> None:
        note = self._notes.toPlainText().strip()
        if not note:
            QMessageBox.warning(
                self,
                "Note Required",
                "Please enter a reason before rejecting this entry.\n"
                "The cashier needs to know what to fix.",
            )
            return
        self.rejected.emit(note)

    def _on_save_next(self) -> None:
        self.save_next.emit(self._current_category())

    def get_category(self) -> str:
        return self._current_category()


# ── Single inbox row ──────────────────────────────────────────────────────────

class _InboxRow(QFrame):
    row_approved        = Signal(object)          # tx._id (ObjectId)
    row_rejected        = Signal(object, str)     # tx._id, reason
    row_save_category   = Signal(object, str)     # tx._id, new_category
    expand_requested    = Signal(object)          # tx._id — ask parent to collapse others

    def __init__(
        self,
        tx: Transaction,
        cashier_name: str,
        categories: List[str],
        row_num: int,
        alt: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tx = tx
        self._expanded = False
        self._categories = categories
        self._cashier_name = cashier_name
        self._detail: Optional[_DetailPanel] = None

        bg = "#FAFAFA" if alt else _WHITE
        self.setObjectName("inboxRow")
        self.setStyleSheet(
            f"QFrame#inboxRow {{ background: {bg}; border-bottom: 1px solid {_BORDER}; }}"
        )
        self._build(tx, cashier_name, categories, row_num)

    def _build(
        self,
        tx: Transaction,
        cashier_name: str,
        categories: List[str],
        row_num: int,
    ) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        # ── Compact header ───────────────────────────────────────────────
        compact = QWidget()
        compact.setStyleSheet("background: transparent;")
        compact.setFixedHeight(44)
        compact.setCursor(Qt.PointingHandCursor)
        hl = QHBoxLayout(compact)
        hl.setContentsMargins(10, 0, 10, 0)
        hl.setSpacing(10)

        # Checkbox
        self._chk = QCheckBox()
        self._chk.setFixedSize(20, 20)
        self._chk.setStyleSheet(
            f"QCheckBox::indicator {{ width: 16px; height: 16px;"
            f" border: 1px solid {_BORDER}; border-radius: 3px; background: {_WHITE}; }}"
            f"QCheckBox::indicator:checked {{ background: {_BLUE}; border-color: {_BLUE}; }}"
        )
        self._chk.clicked.connect(lambda: None)  # prevent row toggle when clicking checkbox
        hl.addWidget(self._chk)

        # S/No
        no_lbl = _lbl(f"{row_num:03d}", size=12, weight=600, color=_T2)
        no_lbl.setFixedWidth(36)
        hl.addWidget(no_lbl)

        # Date
        date_lbl = _lbl(_fmt_date(tx.date), size=12, color=_T2)
        date_lbl.setFixedWidth(78)
        hl.addWidget(date_lbl)

        # Cashier
        cashier_lbl = _lbl(_short_name(cashier_name), size=12)
        cashier_lbl.setFixedWidth(92)
        hl.addWidget(cashier_lbl)

        # Description (flexible)
        desc_lbl = _lbl(tx.description or "—", size=12, weight=500)
        desc_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        desc_lbl.setMaximumWidth(300)
        hl.addWidget(desc_lbl, 1)

        # Truck
        truck_lbl = _lbl(tx.truck_number or "—", size=12, color=_T2)
        truck_lbl.setFixedWidth(90)
        hl.addWidget(truck_lbl)

        # Category badge
        cat_badge = _cat_badge(tx.category_name, tx.category_confidence)
        cat_badge.setFixedWidth(155)
        hl.addWidget(cat_badge)

        # Amount (monospace)
        currency = tx.currency or "TZS"
        amount_text = f"{currency} {tx.amount:,.0f}"
        amount_lbl = _lbl(amount_text, size=12, weight=600)
        amount_lbl.setStyleSheet(
            "color: #111827; font-size: 12px; font-weight: 600;"
            " font-family: 'Cascadia Code', 'Consolas', monospace;"
            " background: transparent;"
        )
        amount_lbl.setFixedWidth(115)
        amount_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hl.addWidget(amount_lbl)

        # Status badge
        status_badge = _badge_lbl("Pending", _AMBER, _AMBER_L)
        status_badge.setFixedWidth(68)
        hl.addWidget(status_badge)

        # Expand chevron
        self._expand_icon = QLabel()
        self._expand_icon.setFixedSize(22, 22)
        self._expand_icon.setAlignment(Qt.AlignCenter)
        self._expand_icon.setStyleSheet("background: transparent;")
        self._update_chevron(False)
        hl.addWidget(self._expand_icon)

        vl.addWidget(compact)

        # Click anywhere on compact row to expand (except checkbox column)
        compact.mousePressEvent = self._on_compact_click

    def _on_compact_click(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # Don't toggle if click was on the checkbox area (leftmost ~36px)
            if event.pos().x() < 36:
                return
            self.toggle_expand()

    def toggle_expand(self) -> None:
        if self._expanded:
            self._collapse()
        else:
            self.expand_requested.emit(self._tx._id)
            self._expand()

    def _expand(self) -> None:
        if self._detail is None:
            self._detail = _DetailPanel(
                self._tx, self._cashier_name, self._categories, parent=self
            )
            self._detail.approved.connect(self._on_approved)
            self._detail.rejected.connect(self._on_rejected)
            self._detail.save_next.connect(self._on_save_next)
            self.layout().addWidget(self._detail)
        self._detail.show()
        self._expanded = True
        self._update_chevron(True)
        self.setStyleSheet(
            "QFrame#inboxRow { background: #EBF4FF; border-bottom: 1px solid #BFDBFE; }"
        )

    def _collapse(self) -> None:
        if self._detail:
            self._detail.hide()
        self._expanded = False
        self._update_chevron(False)
        self.setStyleSheet(
            "QFrame#inboxRow { background: #FFFFFF; border-bottom: 1px solid #E5E7EB; }"
        )

    def force_collapse(self) -> None:
        self._collapse()

    def _update_chevron(self, expanded: bool) -> None:
        icon_name = "mdi.chevron-up" if expanded else "mdi.chevron-down"
        try:
            self._expand_icon.setPixmap(
                qta.icon(icon_name, color=_T2).pixmap(16, 16)
            )
        except Exception:
            self._expand_icon.setText("▼" if not expanded else "▲")

    def is_checked(self) -> bool:
        return self._chk.isChecked()

    def set_checked(self, checked: bool) -> None:
        self._chk.setChecked(checked)

    def tx_id(self) -> Optional[ObjectId]:
        return self._tx._id

    def confidence(self) -> float:
        return self._tx.category_confidence

    def category_name(self) -> Optional[str]:
        return self._tx.category_name

    # ── Detail panel callbacks ────────────────────────────────────────────

    def _on_approved(self) -> None:
        new_cat = self._detail.get_category() if self._detail else None
        if new_cat and new_cat != self._tx.category_name:
            self.row_save_category.emit(self._tx._id, new_cat)
        self.row_approved.emit(self._tx._id)

    def _on_rejected(self, reason: str) -> None:
        new_cat = self._detail.get_category() if self._detail else None
        if new_cat and new_cat != self._tx.category_name:
            self.row_save_category.emit(self._tx._id, new_cat)
        self.row_rejected.emit(self._tx._id, reason)

    def _on_save_next(self, new_cat: str) -> None:
        if new_cat != self._tx.category_name:
            self.row_save_category.emit(self._tx._id, new_cat)
        self._collapse()


# ── Table header row ──────────────────────────────────────────────────────────

class _TableHeader(QFrame):
    select_all_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setStyleSheet(
            f"QFrame {{ background: #F1F5F9; border-bottom: 2px solid {_BORDER}; }}"
        )
        hl = QHBoxLayout(self)
        hl.setContentsMargins(10, 0, 10, 0)
        hl.setSpacing(10)

        self._select_all = QCheckBox()
        self._select_all.setFixedSize(20, 20)
        self._select_all.setToolTip("Select / deselect all")
        self._select_all.setStyleSheet(
            f"QCheckBox::indicator {{ width: 16px; height: 16px;"
            f" border: 1px solid {_BORDER}; border-radius: 3px; background: {_WHITE}; }}"
            f"QCheckBox::indicator:checked {{ background: {_BLUE}; border-color: {_BLUE}; }}"
        )
        self._select_all.stateChanged.connect(
            lambda s: self.select_all_changed.emit(s == Qt.Checked)
        )
        hl.addWidget(self._select_all)

        def _col(text: str, w: int, align=Qt.AlignLeft) -> QLabel:
            lbl = _lbl(text, size=11, weight=600, color=_T2)
            lbl.setFixedWidth(w)
            lbl.setAlignment(align | Qt.AlignVCenter)
            return lbl

        hl.addWidget(_col("S/NO", 36))
        hl.addWidget(_col("DATE", 78))
        hl.addWidget(_col("CASHIER", 92))
        hl.addWidget(_col("DESCRIPTION", 100), 1)
        hl.addWidget(_col("TRUCK NO", 90))
        hl.addWidget(_col("CATEGORY", 155))
        hl.addWidget(_col("AMOUNT", 115, Qt.AlignRight))
        hl.addWidget(_col("STATUS", 68))
        hl.addWidget(_lbl("", 22))  # chevron column spacer


# ── Filter toolbar ────────────────────────────────────────────────────────────

class _FilterBar(QFrame):
    filter_changed = Signal()
    approve_selected = Signal()
    reject_selected  = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-bottom: 1px solid {_BORDER}; }}"
        )
        self._build()

    def _build(self) -> None:
        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(10)

        # Search
        try:
            search_icon = QLabel()
            search_icon.setFixedSize(16, 16)
            search_icon.setPixmap(qta.icon("mdi.magnify", color=_TM).pixmap(16, 16))
            search_icon.setStyleSheet("background: transparent;")
            hl.addWidget(search_icon)
        except Exception:
            pass

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search description…")
        self._search.setFixedWidth(220)
        self._search.setStyleSheet(_input_ss())
        self._search.textChanged.connect(self._on_changed)
        hl.addWidget(self._search)

        # Truck filter
        self._truck_combo = QComboBox()
        self._truck_combo.addItem("All Trucks")
        self._truck_combo.setFixedWidth(130)
        self._truck_combo.setStyleSheet(_input_ss())
        self._truck_combo.currentTextChanged.connect(self._on_changed)
        hl.addWidget(self._truck_combo)

        # Cashier filter
        self._cashier_combo = QComboBox()
        self._cashier_combo.addItem("All Cashiers")
        self._cashier_combo.setFixedWidth(130)
        self._cashier_combo.setStyleSheet(_input_ss())
        self._cashier_combo.currentTextChanged.connect(self._on_changed)
        hl.addWidget(self._cashier_combo)

        # Date filter
        self._date_combo = QComboBox()
        for label, _, _ in _DATE_OPTIONS:
            self._date_combo.addItem(label)
        self._date_combo.setFixedWidth(120)
        self._date_combo.setStyleSheet(_input_ss())
        self._date_combo.currentTextChanged.connect(self._on_changed)
        hl.addWidget(self._date_combo)

        hl.addStretch()

        # Approve selected
        self._approve_btn = _action_btn("Approve Selected", "mdi.check-all")
        self._approve_btn.setEnabled(False)
        self._approve_btn.clicked.connect(self.approve_selected.emit)
        hl.addWidget(self._approve_btn)

        # Reject selected
        self._reject_btn = _action_btn("Reject Selected", "mdi.close-circle-multiple-outline", danger=True)
        self._reject_btn.setEnabled(False)
        self._reject_btn.clicked.connect(self.reject_selected.emit)
        hl.addWidget(self._reject_btn)

    def _on_changed(self) -> None:
        self.filter_changed.emit()

    def populate_trucks(self, trucks: List[str]) -> None:
        current = self._truck_combo.currentText()
        self._truck_combo.blockSignals(True)
        self._truck_combo.clear()
        self._truck_combo.addItem("All Trucks")
        for t in trucks:
            self._truck_combo.addItem(t)
        idx = self._truck_combo.findText(current)
        self._truck_combo.setCurrentIndex(max(0, idx))
        self._truck_combo.blockSignals(False)

    def populate_cashiers(self, cashiers: List[Tuple[str, Optional[ObjectId]]]) -> None:
        current = self._cashier_combo.currentText()
        self._cashier_combo.blockSignals(True)
        self._cashier_combo.clear()
        self._cashier_combo.addItem("All Cashiers", None)
        for name, cid in cashiers:
            self._cashier_combo.addItem(name, cid)
        idx = self._cashier_combo.findText(current)
        self._cashier_combo.setCurrentIndex(max(0, idx))
        self._cashier_combo.blockSignals(False)

    def set_bulk_enabled(self, enabled: bool) -> None:
        self._approve_btn.setEnabled(enabled)
        self._reject_btn.setEnabled(enabled)

    # ── Current filter values ─────────────────────────────────────────────

    def search_text(self) -> str:
        return self._search.text().strip()

    def truck_filter(self) -> str:
        t = self._truck_combo.currentText()
        return "" if t == "All Trucks" else t

    def cashier_id_filter(self) -> Optional[ObjectId]:
        return self._cashier_combo.currentData()

    def date_filter(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        label = self._date_combo.currentText()
        return _resolve_date_filter(label)


# ── Pagination bar ────────────────────────────────────────────────────────────

class _PaginationBar(QFrame):
    page_changed = Signal(int)   # new page index (0-based)
    size_changed = Signal(int)   # new page size

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-top: 1px solid {_BORDER}; }}"
        )
        self._page = 0
        self._total = 0
        self._size = _PAGE_SIZES[0]
        self._build()

    def _build(self) -> None:
        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(10)

        self._size_combo = QComboBox()
        for sz in _PAGE_SIZES:
            self._size_combo.addItem(f"Show {sz}", sz)
        self._size_combo.setFixedWidth(100)
        self._size_combo.setStyleSheet(_input_ss())
        self._size_combo.currentIndexChanged.connect(self._on_size_changed)
        hl.addWidget(self._size_combo)

        self._info_lbl = _lbl("—", size=12, color=_T2)
        hl.addWidget(self._info_lbl)

        hl.addStretch()

        self._prev_btn = _action_btn("← Prev", "mdi.chevron-left", primary=False)
        self._prev_btn.setFixedWidth(90)
        self._prev_btn.clicked.connect(self._on_prev)
        hl.addWidget(self._prev_btn)

        self._next_btn = _action_btn("Next →", "mdi.chevron-right", primary=False)
        self._next_btn.setFixedWidth(90)
        self._next_btn.clicked.connect(self._on_next)
        hl.addWidget(self._next_btn)

    def update_state(self, page: int, total: int, size: int) -> None:
        self._page = page
        self._total = total
        self._size = size
        max_page = max(0, (total - 1) // size) if total > 0 else 0
        self._prev_btn.setEnabled(page > 0)
        self._next_btn.setEnabled(page < max_page)
        start = page * size + 1 if total > 0 else 0
        end = min((page + 1) * size, total)
        pages = max_page + 1
        self._info_lbl.setText(
            f"Showing {start}–{end} of {total}  ·  Page {page + 1} of {pages}"
        )

    def current_size(self) -> int:
        return self._size_combo.currentData() or _PAGE_SIZES[0]

    def _on_size_changed(self) -> None:
        self._page = 0
        self.size_changed.emit(self._size_combo.currentData())

    def _on_prev(self) -> None:
        if self._page > 0:
            self.page_changed.emit(self._page - 1)

    def _on_next(self) -> None:
        max_page = max(0, (self._total - 1) // self._size) if self._total > 0 else 0
        if self._page < max_page:
            self.page_changed.emit(self._page + 1)


# ── VerifyInboxWidget (public) ────────────────────────────────────────────────

class VerifyInboxWidget(QWidget):
    badge_updated = Signal(int)   # emitted after any approve/reject with new pending count

    def __init__(self, user: User, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._categories: List[str] = []
        self._rows: List[_InboxRow] = []
        self._page = 0
        self._total = 0
        self._filters_loaded = False
        self._loading = False
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(350)
        self._debounce_timer.timeout.connect(self._reload)
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setStyleSheet(f"background: {_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Page title bar ───────────────────────────────────────────────
        title_bar = QFrame()
        title_bar.setFixedHeight(52)
        title_bar.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-bottom: 1px solid {_BORDER}; }}"
        )
        tb_hl = QHBoxLayout(title_bar)
        tb_hl.setContentsMargins(20, 0, 20, 0)
        tb_hl.setSpacing(12)

        try:
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(22, 22)
            icon_lbl.setPixmap(qta.icon("mdi.inbox-arrow-down", color=_BLUE).pixmap(22, 22))
            icon_lbl.setStyleSheet("background: transparent;")
            tb_hl.addWidget(icon_lbl)
        except Exception:
            pass

        tb_hl.addWidget(_lbl("Verify Inbox", size=16, weight=700))

        self._pending_badge = _badge_lbl("…", "#FFFFFF", _AMBER)
        self._pending_badge.setMinimumWidth(36)
        tb_hl.addWidget(self._pending_badge)

        tb_hl.addStretch()

        refresh_btn = QPushButton()
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setFixedSize(32, 32)
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
        tb_hl.addWidget(refresh_btn)

        root.addWidget(title_bar)

        # ── Filter bar ───────────────────────────────────────────────────
        self._filter_bar = _FilterBar()
        self._filter_bar.filter_changed.connect(self._on_filter_changed)
        self._filter_bar.approve_selected.connect(self._on_bulk_approve)
        self._filter_bar.reject_selected.connect(self._on_bulk_reject)
        root.addWidget(self._filter_bar)

        # ── Table header ─────────────────────────────────────────────────
        self._table_header = _TableHeader()
        self._table_header.select_all_changed.connect(self._on_select_all)
        root.addWidget(self._table_header)

        # ── Scroll area for rows ─────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {_BG}; border: none; }}"
            "QScrollBar:vertical { background: #F4F6F8; width: 6px; margin: 0; }"
            "QScrollBar::handle:vertical { background: #D1D5DB; border-radius: 3px; min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        self._rows_container = QWidget()
        self._rows_container.setStyleSheet(f"background: {_BG};")
        self._rows_vl = QVBoxLayout(self._rows_container)
        self._rows_vl.setContentsMargins(0, 0, 0, 0)
        self._rows_vl.setSpacing(0)
        self._rows_vl.addStretch()

        self._scroll.setWidget(self._rows_container)
        root.addWidget(self._scroll, 1)

        # ── Pagination bar ───────────────────────────────────────────────
        self._pagination = _PaginationBar()
        self._pagination.page_changed.connect(self._on_page_changed)
        self._pagination.size_changed.connect(self._on_size_changed)
        root.addWidget(self._pagination)

    # ── Public API ─────────────────────────────────────────────────────────

    def refresh(self) -> None:
        asyncio.ensure_future(self._reload())

    # ── Filter / page event handlers ───────────────────────────────────────

    def _on_filter_changed(self) -> None:
        self._page = 0
        self._debounce_timer.start()

    def _on_page_changed(self, page: int) -> None:
        self._page = page
        asyncio.ensure_future(self._reload())

    def _on_size_changed(self, size: int) -> None:
        self._page = 0
        asyncio.ensure_future(self._reload())

    def _on_select_all(self, checked: bool) -> None:
        for row in self._rows:
            row.set_checked(checked)
        self._update_bulk_buttons()

    def _on_row_check_changed(self, _state: int = 0) -> None:
        self._update_bulk_buttons()

    def _update_bulk_buttons(self) -> None:
        any_checked = any(r.is_checked() for r in self._rows)
        self._filter_bar.set_bulk_enabled(any_checked)

    # ── Reload ─────────────────────────────────────────────────────────────

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
            search = self._filter_bar.search_text()
            truck = self._filter_bar.truck_filter()
            cashier_id = self._filter_bar.cashier_id_filter()
            date_from, date_to = self._filter_bar.date_filter()

            # First load: fetch categories + filter options in parallel
            if not self._filters_loaded:
                cats, trucks, cashier_ids = await asyncio.gather(
                    get_all_categories(),
                    get_unverified_trucks(),
                    get_unverified_cashier_ids(),
                )
                self._categories = [c.name for c in cats]
                cashier_name_map = await get_cashier_names(cashier_ids)

                self._filter_bar.populate_trucks(trucks)
                cashier_list = [(cashier_name_map.get(cid, str(cid)), cid) for cid in cashier_ids]
                cashier_list.sort(key=lambda x: x[0])
                self._filter_bar.populate_cashiers(cashier_list)
                self._filters_loaded = True
            else:
                cashier_name_map = {}

            # Fetch transactions + total count in parallel
            txs, total, pending_count = await asyncio.gather(
                get_unverified_filtered(
                    search=search, truck=truck, cashier_id=cashier_id,
                    date_from=date_from, date_to=date_to, limit=size, skip=skip,
                ),
                count_unverified_filtered(
                    search=search, truck=truck, cashier_id=cashier_id,
                    date_from=date_from, date_to=date_to,
                ),
                get_pending_count(),
            )

            # Fetch cashier names for this page's transactions
            cids = list({tx.cashier_id for tx in txs if tx.cashier_id})
            if cids:
                page_names = await get_cashier_names(cids)
                cashier_name_map.update(page_names)

            self._total = total
            self._build_rows(txs, cashier_name_map, skip)
            self._pagination.update_state(self._page, total, size)
            self._pending_badge.setText(str(pending_count))
        except Exception as exc:
            self._show_empty(f"Failed to load: {exc}")
        finally:
            self._loading = False

    def _build_rows(
        self,
        txs: List[Transaction],
        cashier_names: Dict,
        skip: int,
    ) -> None:
        # Clear existing rows
        for row in self._rows:
            row.deleteLater()
        self._rows.clear()

        # Remove all widgets from layout (except stretch)
        while self._rows_vl.count() > 1:
            item = self._rows_vl.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not txs:
            self._show_empty("No pending transactions match the current filters.")
            return

        for i, tx in enumerate(txs):
            cashier_name = cashier_names.get(tx.cashier_id, "") if tx.cashier_id else ""
            row = _InboxRow(
                tx=tx,
                cashier_name=cashier_name,
                categories=self._categories,
                row_num=skip + i + 1,
                alt=(i % 2 == 1),
            )
            row.row_approved.connect(self._on_row_approved)
            row.row_rejected.connect(self._on_row_rejected)
            row.row_save_category.connect(self._on_row_save_category)
            row.expand_requested.connect(self._on_expand_requested)
            row._chk.stateChanged.connect(self._on_row_check_changed)
            self._rows_vl.insertWidget(self._rows_vl.count() - 1, row)
            self._rows.append(row)

        self._update_bulk_buttons()

    def _show_empty(self, message: str) -> None:
        while self._rows_vl.count() > 1:
            item = self._rows_vl.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        empty = QWidget()
        empty.setStyleSheet("background: transparent;")
        el = QVBoxLayout(empty)
        el.setAlignment(Qt.AlignCenter)
        el.setContentsMargins(0, 60, 0, 60)
        try:
            icon_lbl = QLabel()
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setPixmap(qta.icon("mdi.inbox-outline", color="#D1D5DB").pixmap(48, 48))
            icon_lbl.setStyleSheet("background: transparent;")
            el.addWidget(icon_lbl)
        except Exception:
            pass
        el.addSpacing(8)
        el.addWidget(_lbl(message, size=13, color=_TM, wrap=True))
        self._rows_vl.insertWidget(0, empty)

    # ── Row action handlers ────────────────────────────────────────────────

    def _on_expand_requested(self, tx_id: ObjectId) -> None:
        for row in self._rows:
            if row.tx_id() != tx_id:
                row.force_collapse()

    def _on_row_approved(self, tx_id: ObjectId) -> None:
        asyncio.ensure_future(self._do_approve(tx_id))

    def _on_row_rejected(self, tx_id: ObjectId, reason: str) -> None:
        asyncio.ensure_future(self._do_reject(tx_id, reason))

    def _on_row_save_category(self, tx_id: ObjectId, category: str) -> None:
        asyncio.ensure_future(self._do_save_category(tx_id, category))

    # ── Bulk actions ───────────────────────────────────────────────────────

    def _on_bulk_approve(self) -> None:
        checked = [r for r in self._rows if r.is_checked()]
        if not checked:
            return

        auto = [r for r in checked if r.confidence() >= _AUTO_THRESHOLD]
        low  = [r for r in checked if r.confidence() < _AUTO_THRESHOLD]

        if not auto:
            QMessageBox.warning(
                self,
                "Cannot Bulk Approve",
                f"All {len(low)} selected transaction(s) have low category confidence "
                f"(< {int(_AUTO_THRESHOLD * 100)}%) and require individual review.\n"
                "Please expand each row to review and approve individually.",
            )
            return

        msg = f"Approve {len(auto)} auto-matched transaction(s)?"
        if low:
            msg += (
                f"\n\n⚠ {len(low)} transaction(s) with low confidence will be skipped "
                "and require individual review."
            )
        if QMessageBox.question(
            self, "Bulk Approve", msg,
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            tx_ids = [r.tx_id() for r in auto if r.tx_id()]
            asyncio.ensure_future(self._do_bulk_approve(tx_ids))

    def _on_bulk_reject(self) -> None:
        checked = [r for r in self._rows if r.is_checked()]
        if not checked:
            return
        QMessageBox.information(
            self,
            "Bulk Reject",
            "Bulk rejection requires individual notes per transaction.\n"
            "Please expand each row to add a note before rejecting.",
        )

    # ── Async service calls ────────────────────────────────────────────────

    async def _do_approve(self, tx_id: ObjectId) -> None:
        from tahmeed.services.accountant_service import approve_transaction, get_pending_count
        try:
            acc_id = self._user._id
            await approve_transaction(tx_id, acc_id)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            asyncio.ensure_future(self._reload())
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to approve: {exc}")

    async def _do_reject(self, tx_id: ObjectId, reason: str) -> None:
        from tahmeed.services.accountant_service import reject_transaction, get_pending_count
        try:
            await reject_transaction(tx_id, reason)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            asyncio.ensure_future(self._reload())
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to reject: {exc}")

    async def _do_save_category(self, tx_id: ObjectId, category: str) -> None:
        from tahmeed.services.accountant_service import update_transaction_category
        try:
            await update_transaction_category(tx_id, category)
        except Exception:
            pass  # non-critical — category update failure shouldn't block workflow

    async def _do_bulk_approve(self, tx_ids: List[ObjectId]) -> None:
        from tahmeed.services.accountant_service import bulk_approve_transactions, get_pending_count
        try:
            n = await bulk_approve_transactions(tx_ids, self._user._id)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            asyncio.ensure_future(self._reload())
            QMessageBox.information(self, "Done", f"Approved {n} transaction(s) successfully.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Bulk approve failed: {exc}")
