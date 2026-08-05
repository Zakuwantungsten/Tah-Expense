"""AccountantDashboard — Verification Inbox (QB-style redesign)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple, TypeVar

from bson import ObjectId
import qtawesome as qta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QLineEdit, QComboBox, QPushButton, QTextEdit,
    QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QDialog, QInputDialog,
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QColor, QFont

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.models.category import Category
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay
from tahmeed.ui.widgets.checkable_multi_combo import CheckableMultiCombo
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence

_T = TypeVar("_T")

# ── Design tokens ──────────────────────────────────────────────────────────────
_WHITE     = "#FFFFFF"
_BG        = "#F4F6F8"
_BORDER    = "#E5E7EB"
_BLUE      = "#0077C5"
_BLUE_L    = "#E8F4FD"
_GREEN     = "#16A34A"
_AMBER     = "#D97706"
_RED       = "#DC2626"
_T1        = "#111827"
_T2        = "#6B7280"
_TM        = "#9CA3AF"
_QB_HDR_BG = "#EFF6FF"
_QB_HDR_FG = "#1E3A5F"
_QB_SEL_BG = "#DBEAFE"
_QB_SEL_FG = "#1E3A5F"
# Shared grid look — matches Master Expenses / Separate Expenses
_HDR_BG    = "#F1F5F9"
_STRIPE    = "#F1F5F9"
_ROW_EVEN  = "#FFFFFF"
_ROW_ODD   = "#F1F5F9"   # slate-100 — manual stripe (readable on all row types)
_HDR_H     = 28
_ROW_H     = 28

_SCROLL_CHUNK      = 50
_SELECT_ALL_CHUNK  = 500
_DATE_OPTS         = ["All Dates", "Today", "Last 7 Days", "Last 30 Days", "This Month"]

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

# Fixed widths per column (Description gets a real default so it stays resizable)
_COL_WIDTHS = [32, 44, 80, 95, 90, 200, 90, 115, 90, 130, 80, 80]


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
        "font-family:'Segoe UI';background:transparent;"
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
              "font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
              "QPushButton:hover{background:#B91C1C;}"
              "QPushButton:disabled{background:#FCA5A5;}")
    elif primary:
        ss = (f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
              "font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
              "QPushButton:hover{background:#005EA3;}"
              "QPushButton:disabled{background:#93C5FD;}")
    else:
        ss = (f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
              f"border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 14px;}}"
              f"QPushButton:hover{{background:{_BG};}}"
              f"QPushButton:disabled{{color:{_TM};}}")
    b.setStyleSheet(ss)
    return b


def _input_ss() -> str:
    return (
        f"QLineEdit,QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
        f"background:{_WHITE};color:{_T1};font-size:12px;"
        "font-family:'Segoe UI';padding:0 8px;"
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
        "border:none;font-size:11px;font-family:'Segoe UI';}}"
        f"QTableWidget::item{{padding:2px 8px;color:{_T1};}}"
        f"QTableWidget::item:selected{{background:{_BLUE_L};color:{_T1};}}"
        f"QHeaderView::section{{background:{_HDR_BG};color:{_T2};"
        "font-size:10px;font-weight:600;font-family:'Segoe UI';"
        f"border:none;border-right:1px solid {_BORDER};"
        f"border-bottom:1px solid {_BORDER};padding:0 8px;height:{_HDR_H}px;}}"
        f"QHeaderView::section:hover{{background:#E2E8F0;}}"
        "QScrollBar:vertical{width:8px;background:transparent;}"
        "QScrollBar::handle:vertical{background:#D1D5DB;border-radius:4px;}"
        "QScrollBar:horizontal{height:8px;background:transparent;}"
        "QScrollBar::handle:horizontal{background:#D1D5DB;border-radius:4px;}"
    )


def _stripe_bg(row_idx: int) -> str:
    return _ROW_ODD if row_idx % 2 else _ROW_EVEN


def _apply_row_bg(t: QTableWidget, row: int, bg: str) -> None:
    for col in range(t.columnCount()):
        item = t.item(row, col)
        if item:
            item.setBackground(QColor(bg))


def _finish_table_row(
    t: QTableWidget, row: int, bg: str | None = None,
) -> None:
    """Apply stripe (or custom) background and compact row height."""
    _apply_row_bg(t, row, bg if bg else _stripe_bg(row))
    t.setRowHeight(row, _ROW_H)


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


def _fmt_num(v, prefix: str = "", decimals: int = 0) -> str:
    """Toll-plaza-style amount formatting; keeps a currency prefix (e.g. TZS)."""
    if v is None or v == "":
        return "—"
    try:
        if isinstance(v, (int, float)):
            amount = float(v)
        else:
            from tahmeed.services.daily_import_service import parse_amount
            parsed = parse_amount(v)
            if parsed is None:
                return str(v)
            amount = float(parsed)
        return f"{prefix}{amount:,.{decimals}f}"
    except Exception:
        return str(v)


def _fmt_amount(tx: Transaction) -> str:
    currency = (tx.currency or "TZS").upper()
    decimals = 0 if currency in {"TZS", "TSH", "TZ", "ZMW", "ZMB", "ZK"} else 2
    prefix = f"{currency} "
    return _fmt_num(tx.amount, prefix, decimals)


def _fmt_relative(dt) -> str:
    """Short human-friendly age, e.g. '2h ago' / '3d ago'. Falls back to a date."""
    if not isinstance(dt, datetime):
        return ""
    secs = (datetime.utcnow() - dt).total_seconds()
    if secs < 0:
        return "just now"
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    if secs < 604800:
        return f"{int(secs // 86400)}d ago"
    return dt.strftime("%d %b %y")


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
    confirm_delete_selected = Signal()
    restore_selected = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setStyleSheet(
            f"QFrame{{background:{_WHITE};border-bottom:1px solid {_BORDER};}}"
        )
        self._bulk_mode = "new"  # "new" | "edited" | "rejected" | "issues" | "deleted"
        self._build()

    def _build(self) -> None:
        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(8)

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
        self._search.setFixedWidth(180)
        self._search.setStyleSheet(_input_ss())
        self._search.textChanged.connect(self.filter_changed)
        hl.addWidget(self._search)

        self._item_cb = CheckableMultiCombo(
            "All Items", noun_plural="items", parent=self,
        )
        self._item_cb.setFixedWidth(140)
        self._item_cb.setStyleSheet(_input_ss())
        self._item_cb.selectionChanged.connect(self._on_items_changed)
        hl.addWidget(self._item_cb)

        self._desc_cb = CheckableMultiCombo(
            "All Descriptions", noun_plural="descriptions", parent=self,
        )
        self._desc_cb.setFixedWidth(160)
        self._desc_cb.setStyleSheet(_input_ss())
        self._desc_cb.selectionChanged.connect(self._on_descs_changed)
        hl.addWidget(self._desc_cb)

        self._truck_cb = QComboBox()
        self._truck_cb.addItem("All Trucks")
        self._truck_cb.setFixedWidth(110)
        self._truck_cb.setStyleSheet(_input_ss())
        self._truck_cb.currentTextChanged.connect(self.filter_changed)
        hl.addWidget(self._truck_cb)

        self._cashier_cb = QComboBox()
        self._cashier_cb.addItem("All Cashiers", None)
        self._cashier_cb.setFixedWidth(120)
        self._cashier_cb.setStyleSheet(_input_ss())
        self._cashier_cb.currentTextChanged.connect(self.filter_changed)
        hl.addWidget(self._cashier_cb)

        self._date_cb = QComboBox()
        for opt in _DATE_OPTS:
            self._date_cb.addItem(opt)
        self._date_cb.setFixedWidth(110)
        self._date_cb.setStyleSheet(_input_ss())
        self._date_cb.currentTextChanged.connect(self.filter_changed)
        hl.addWidget(self._date_cb)

        clear_btn = _btn("Clear", "mdi.filter-remove-outline", primary=False)
        clear_btn.clicked.connect(self.clear_filters)
        hl.addWidget(clear_btn)

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

        self._confirm_delete_btn = _btn(
            "Confirm Delete", "mdi.delete-forever", danger=True,
        )
        self._confirm_delete_btn.setEnabled(False)
        self._confirm_delete_btn.clicked.connect(self.confirm_delete_selected)
        self._confirm_delete_btn.hide()
        hl.addWidget(self._confirm_delete_btn)

        self._restore_btn = _btn("Restore Selected", "mdi.restore")
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self.restore_selected)
        self._restore_btn.hide()
        hl.addWidget(self._restore_btn)

        self._cascade_busy = False
        self._on_items_cascade = None   # optional async callback
        self._on_descs_cascade = None

    def set_cascade_handlers(self, on_items, on_descs) -> None:
        self._on_items_cascade = on_items
        self._on_descs_cascade = on_descs

    def _on_items_changed(self) -> None:
        if self._cascade_busy:
            return
        if callable(self._on_items_cascade):
            self._on_items_cascade()
        else:
            self.filter_changed.emit()

    def _on_descs_changed(self) -> None:
        if self._cascade_busy:
            return
        if callable(self._on_descs_cascade):
            self._on_descs_cascade()
        else:
            self.filter_changed.emit()

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

    def populate_items(self, items: List[str], *, emit: bool = False) -> None:
        self._cascade_busy = True
        try:
            self._item_cb.set_options(items, keep_selected=True, emit=False)
        finally:
            self._cascade_busy = False
        if emit:
            self.filter_changed.emit()

    def populate_descriptions(self, descs: List[str], *, emit: bool = False) -> None:
        self._cascade_busy = True
        try:
            self._desc_cb.set_options(descs, keep_selected=True, emit=False)
        finally:
            self._cascade_busy = False
        if emit:
            self.filter_changed.emit()

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
        self._confirm_delete_btn.setEnabled(enabled)
        self._restore_btn.setEnabled(enabled)

    def set_bulk_visible(self, visible: bool) -> None:
        """Show/hide standard approve/reject bulk actions (not Deleted-tab buttons)."""
        if self._bulk_mode == "deleted":
            self._approve_btn.setVisible(False)
            self._reject_btn.setVisible(False)
            self._confirm_delete_btn.setVisible(visible)
            self._restore_btn.setVisible(visible)
            return
        self._approve_btn.setVisible(visible)
        self._reject_btn.setVisible(visible)
        self._confirm_delete_btn.setVisible(False)
        self._restore_btn.setVisible(False)

    def set_bulk_mode(self, mode: str) -> None:
        """Switch bulk actions for tab: new/edited/issues vs rejected vs deleted."""
        self._bulk_mode = mode
        is_deleted = mode == "deleted"
        is_rejected = mode == "rejected"
        self._approve_btn.setVisible(not is_deleted and not is_rejected)
        self._reject_btn.setVisible(not is_deleted and not is_rejected)
        self._confirm_delete_btn.setVisible(is_deleted)
        self._restore_btn.setVisible(is_deleted)

    def search_text(self) -> str:
        return self._search.text().strip()

    def item_filter(self) -> List[str]:
        return self._item_cb.selected_values()

    def description_filter(self) -> List[str]:
        return self._desc_cb.selected_values()

    def truck_filter(self) -> str:
        t = self._truck_cb.currentText()
        return "" if t == "All Trucks" else t

    def cashier_id_filter(self) -> Optional[ObjectId]:
        return self._cashier_cb.currentData()

    def date_filter(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        return _resolve_date_filter(self._date_cb.currentText())

    def clear_filters(self) -> None:
        """Reset search, multi-combos, truck, cashier, and date to defaults."""
        self._search.blockSignals(True)
        self._truck_cb.blockSignals(True)
        self._cashier_cb.blockSignals(True)
        self._date_cb.blockSignals(True)
        self._cascade_busy = True
        try:
            self._search.clear()
            self._item_cb.reset_to_all(emit=False)
            self._desc_cb.reset_to_all(emit=False)
            self._truck_cb.setCurrentIndex(0)
            self._cashier_cb.setCurrentIndex(0)
            self._date_cb.setCurrentIndex(0)
        finally:
            self._search.blockSignals(False)
            self._truck_cb.blockSignals(False)
            self._cashier_cb.blockSignals(False)
            self._date_cb.blockSignals(False)
            self._cascade_busy = False
        self.filter_changed.emit()


# ── Status / scroll footer ─────────────────────────────────────────────────────

class _StatusBar(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setStyleSheet(
            f"QFrame{{background:{_WHITE};border-top:1px solid {_BORDER};}}"
        )
        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        self._info = _lbl("—", size=12, color=_T2)
        hl.addWidget(self._info)
        hl.addStretch()

    def set_text(self, text: str) -> None:
        self._info.setText(text)


# ── Action panel ───────────────────────────────────────────────────────────────

class _ActionPanel(QFrame):
    """Pinned review panel that slides in below the table when a row is double-clicked."""
    approved  = Signal(object, str)  # tx._id, selected category (may be empty)
    rejected  = Signal(object, str)  # tx._id, reason
    saved_cat = Signal(object, str)  # tx._id, category
    returned  = Signal(object)       # tx._id — return rejected entry to inbox
    deletion_confirmed = Signal(object)  # tx._id
    deletion_restored = Signal(object)   # tx._id
    dismissed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tx: Optional[Transaction] = None
        self._mode = "new"  # "new" | "edited" | "rejected" | "deleted"
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
        self._cat_combo.currentIndexChanged.connect(self._on_category_changed)
        self._category_user_selected = False
        cvl.addWidget(self._cat_combo)
        hl.addWidget(cat_w)

        # Notes
        notes_w = QWidget(); notes_w.setStyleSheet("background:transparent;")
        nvl = QVBoxLayout(notes_w); nvl.setContentsMargins(0, 0, 0, 0); nvl.setSpacing(3)
        self._notes_lbl = _lbl("NOTES FOR CASHIER  (required to reject)",
                               size=9, weight=600, color=_T2)
        nvl.addWidget(self._notes_lbl)
        self._notes = QTextEdit()
        self._notes.setFixedHeight(54)
        self._notes.setPlaceholderText("Leave a reason if rejecting this entry…")
        self._notes.setStyleSheet(
            f"QTextEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:5px;}}"
            f"QTextEdit:focus{{border-color:{_BLUE};}}"
        )
        nvl.addWidget(self._notes)
        hl.addWidget(notes_w, 1)

        # Buttons
        btn_w = QWidget(); btn_w.setStyleSheet("background:transparent;")
        bvl = QVBoxLayout(btn_w); bvl.setContentsMargins(0, 0, 0, 0); bvl.setSpacing(5)
        bvl.addStretch()

        self._save_btn = _btn("Save & Next", "mdi.content-save-outline", primary=False, height=30)
        self._save_btn.clicked.connect(self._on_save)
        bvl.addWidget(self._save_btn)

        self._approve_btn = _btn("Approve", "mdi.check-circle-outline", height=30)
        self._approve_btn.clicked.connect(self._on_approve)
        bvl.addWidget(self._approve_btn)

        self._reject_btn = _btn("Reject & Return", "mdi.close-circle-outline", danger=True, height=30)
        self._reject_btn.clicked.connect(self._on_reject)
        bvl.addWidget(self._reject_btn)

        self._return_btn = _btn("Return to Inbox", "mdi.inbox-arrow-up", height=30)
        self._return_btn.clicked.connect(self._on_return)
        self._return_btn.hide()
        bvl.addWidget(self._return_btn)

        self._confirm_delete_btn = _btn(
            "Confirm Delete", "mdi.delete-forever", danger=True, height=30,
        )
        self._confirm_delete_btn.clicked.connect(self._on_confirm_delete)
        self._confirm_delete_btn.hide()
        bvl.addWidget(self._confirm_delete_btn)

        self._restore_btn = _btn("Restore", "mdi.restore", height=30)
        self._restore_btn.clicked.connect(self._on_restore)
        self._restore_btn.hide()
        bvl.addWidget(self._restore_btn)

        dismiss_btn = _btn("✕", "", primary=False, height=30)
        dismiss_btn.setFixedWidth(34)
        dismiss_btn.setToolTip("Close panel")
        dismiss_btn.clicked.connect(self.dismissed)
        bvl.addWidget(dismiss_btn)

        hl.addWidget(btn_w)
        vl.addWidget(ctrl)

    def set_mode(self, mode: str) -> None:
        """Switch panel between 'new', 'edited', 'rejected', and 'deleted' modes."""
        self._mode = mode
        is_rejected = mode == "rejected"
        is_deleted = mode == "deleted"
        self._save_btn.setVisible(not is_rejected and not is_deleted)
        self._approve_btn.setVisible(not is_rejected and not is_deleted)
        self._reject_btn.setVisible(not is_rejected and not is_deleted)
        self._return_btn.setVisible(is_rejected)
        self._confirm_delete_btn.setVisible(is_deleted)
        self._restore_btn.setVisible(is_deleted)
        self._cat_combo.setEnabled(not is_deleted)
        self._notes.setEnabled(not is_deleted)
        if is_deleted:
            self._notes_lbl.setText("DELETION REQUEST")
            self._notes.setPlaceholderText("Cashier requested permanent removal of this approved entry.")
        elif is_rejected:
            self._notes_lbl.setText("REJECTION REASON")
            self._notes.setPlaceholderText("Leave a reason if rejecting this entry…")
        else:
            self._notes_lbl.setText("NOTES FOR CASHIER  (required to reject)")
            self._notes.setPlaceholderText("Leave a reason if rejecting this entry…")

    @staticmethod
    def _needs_item(tx: Transaction) -> bool:
        from tahmeed.services.description_mapping_service import transaction_needs_item

        return transaction_needs_item(tx.item, tx.category_name)

    def _on_category_changed(self, _index: int) -> None:
        if self._cat_combo.signalsBlocked():
            return
        self._category_user_selected = True

    def load(self, tx: Transaction, cashier_name: str, categories: List[str]) -> None:
        self._tx = tx
        self._category_user_selected = False
        currency = tx.currency or "TZS"
        self._info_lbl.setText(
            f"{tx.truck_number or '—'}  ·  {_fmt_date(tx.date)}  ·  "
            f"{tx.description or '—'}  ·  {_fmt_amount(tx)}"
            f"  ·  Cashier: {cashier_name or '—'}"
        )
        self._cat_combo.blockSignals(True)
        self._cat_combo.clear()
        for cat in categories:
            self._cat_combo.addItem(cat)
        if self._needs_item(tx) and categories:
            self._cat_combo.insertItem(0, "— map on approve —")
            self._cat_combo.setCurrentIndex(0)
        elif tx.category_name:
            idx = self._cat_combo.findText(tx.category_name)
            if idx >= 0:
                self._cat_combo.setCurrentIndex(idx)
                self._category_user_selected = True
            else:
                self._cat_combo.insertItem(0, tx.category_name)
                self._cat_combo.setCurrentIndex(0)
                self._category_user_selected = True
        elif categories:
            self._cat_combo.setCurrentIndex(0)
            self._category_user_selected = True
        self._cat_combo.blockSignals(False)
        if self._mode == "deleted":
            requester = cashier_name or "—"
            when = _fmt_date(tx.deletion_requested_at) if tx.deletion_requested_at else "—"
            self._notes.setPlainText(
                f"Requested by {requester} on {when}.\n"
                "Confirm to permanently remove from Master Expenses, or Restore to keep it."
            )
        else:
            self._notes.setPlainText(tx.rejection_reason or "")

    def _on_approve(self) -> None:
        if not self._tx:
            return
        cat = self._cat_combo.currentText().strip()
        if cat == "— map on approve —":
            cat = ""
        elif self._needs_item(self._tx) and not self._category_user_selected:
            cat = ""
        self.approved.emit(self._tx._id, cat)

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

    def _on_return(self) -> None:
        if not self._tx:
            return
        self.returned.emit(self._tx._id)

    def _on_confirm_delete(self) -> None:
        if not self._tx:
            return
        self.deletion_confirmed.emit(self._tx._id)

    def _on_restore(self) -> None:
        if not self._tx:
            return
        self.deletion_restored.emit(self._tx._id)


# ── Sub-tab bar (New | Edited | Rejected) ────────────────────────────────────

class _SubTabBar(QFrame):
    """Segmented New | Edited | Rejected | Issues | Deleted switcher with live count badges."""

    tab_changed = Signal(int)   # 0 = New, 1 = Edited, 2 = Rejected, 3 = Issues, 4 = Deleted

    _TAB_SS = (
        "QPushButton{background:transparent;border:none;"
        "border-bottom:2px solid transparent;color:%s;font-size:13px;font-weight:600;"
        "font-family:'Segoe UI';padding:0 4px;}"
        "QPushButton:hover{color:%s;}"
        "QPushButton:checked{color:%s;border-bottom:2px solid %s;}"
    ) % (_T2, _T1, _BLUE, _BLUE)

    _REJECTED_SS = (
        "QPushButton{background:transparent;border:none;"
        "border-bottom:2px solid transparent;color:%s;font-size:13px;font-weight:600;"
        "font-family:'Segoe UI';padding:0 4px;}"
        "QPushButton:hover{color:%s;}"
        "QPushButton:checked{color:%s;border-bottom:2px solid %s;}"
    ) % (_T2, _RED, _RED, _RED)

    _ISSUES_SS = (
        "QPushButton{background:transparent;border:none;"
        "border-bottom:2px solid transparent;color:%s;font-size:13px;font-weight:600;"
        "font-family:'Segoe UI';padding:0 4px;}"
        "QPushButton:hover{color:%s;}"
        "QPushButton:checked{color:%s;border-bottom:2px solid %s;}"
    ) % (_T2, _AMBER, _AMBER, _AMBER)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(42)
        self.setStyleSheet(
            f"QFrame{{background:{_WHITE};border-bottom:1px solid {_BORDER};}}"
        )
        self._current = 0
        self._labels = ["New", "Edited", "Rejected", "Issues", "Deleted"]

        hl = QHBoxLayout(self)
        hl.setContentsMargins(20, 0, 20, 0)
        hl.setSpacing(8)

        self._buttons: List[QPushButton] = []
        for i, label in enumerate(self._labels):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(42)
            if i == 2 or i == 4:
                ss = self._REJECTED_SS
            elif i == 3:
                ss = self._ISSUES_SS
            else:
                ss = self._TAB_SS
            btn.setStyleSheet(ss)
            btn.clicked.connect(lambda _checked, idx=i: self._select(idx))
            self._buttons.append(btn)
            hl.addWidget(btn)

        hl.addStretch()
        self._buttons[0].setChecked(True)

    def _select(self, idx: int) -> None:
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == idx)
        if idx != self._current:
            self._current = idx
            self.tab_changed.emit(idx)

    def current_tab(self) -> int:
        return self._current

    def set_counts(
        self,
        new_count: int,
        edited_count: int,
        rejected_count: int = 0,
        issues_count: int = 0,
        deleted_count: int = 0,
    ) -> None:
        self._buttons[0].setText(f"New ({new_count})")
        self._buttons[1].setText(f"Edited ({edited_count})")
        self._buttons[2].setText(f"Rejected ({rejected_count})")
        self._buttons[3].setText(f"Issues ({issues_count})")
        self._buttons[4].setText(f"Deleted ({deleted_count})")


# ── Main widget ────────────────────────────────────────────────────────────────

class VerifyInboxWidget(QWidget):
    badge_updated = Signal(int)

    def __init__(self, user: User, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._categories: List[str] = []
        self._category_objects: List[Category] = []
        self._transactions: List[Transaction] = []
        self._cashier_names: Dict = {}
        self._loaded = 0
        self._total = 0
        self._current_tab = 0   # 0 = New, 1 = Edited, 2 = Rejected, 3 = Issues, 4 = Deleted
        self._filters_tab = -1  # which tab's filters are currently loaded
        self._loading = False
        self._scroll_loading = False
        self._select_all_loading = False
        self._reload_generation = 0
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._reset_and_load)
        # Distinguish single-click (select) from double-click (open panel)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(250)
        self._click_timer.timeout.connect(self._commit_row_click)
        self._pending_click_row: Optional[int] = None
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
            "color:#6B7280;background:#F3F4F6;border-radius:8px;"
            "padding:2px 8px;font-size:11px;font-weight:600;"
            "font-family:'Segoe UI';"
        )
        self._pending_badge.setMinimumWidth(28)
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

        # Sub-tab bar (New | Edited | Rejected | Issues | Deleted)
        self._subtabs = _SubTabBar()
        self._subtabs.tab_changed.connect(self._on_subtab_changed)
        root.addWidget(self._subtabs)

        # Filter bar
        self._filter_bar = _FilterBar()
        self._filter_bar.filter_changed.connect(self._on_filter_changed)
        self._filter_bar.set_cascade_handlers(
            self._on_item_filter_cascade,
            self._on_desc_filter_cascade,
        )
        self._filter_bar.approve_selected.connect(self._on_bulk_approve)
        self._filter_bar.reject_selected.connect(self._on_bulk_reject)
        self._filter_bar.confirm_delete_selected.connect(self._on_bulk_confirm_delete)
        self._filter_bar.restore_selected.connect(self._on_bulk_restore)
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
        self._panel.returned.connect(self._on_returned)
        self._panel.deletion_confirmed.connect(self._on_deletion_confirmed)
        self._panel.deletion_restored.connect(self._on_deletion_restored)
        self._panel.dismissed.connect(self._panel.hide)
        cl.addWidget(self._panel)

        root.addWidget(content, 1)

        # Infinite-scroll status footer (Master-style)
        self._status = _StatusBar()
        root.addWidget(self._status)

        self._loading_overlay = LoadingOverlay(self, "Loading verify inbox…")

    def _build_table(self) -> QTableWidget:
        t = QTableWidget(0, _NCOLS)
        t.setHorizontalHeaderLabels(_HEADERS)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.setAlternatingRowColors(False)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(_ROW_H)
        t.setShowGrid(True)
        t.setStyleSheet(_table_style())
        hdr = t.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setMinimumSectionSize(28)
        # Interactive + persisted widths (shared across New / Edited / Rejected / Issues).
        bind_column_width_persistence(
            t,
            "verify_inbox",
            _COL_WIDTHS,
        )
        t.itemClicked.connect(self._on_item_clicked)
        t.itemDoubleClicked.connect(self._on_item_double_clicked)
        t.itemChanged.connect(self._on_checkbox_changed)
        hdr.sectionClicked.connect(self._on_header_click)
        t.verticalScrollBar().valueChanged.connect(self._on_scroll)
        return t

    # ── Public API ──────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        self._filters_tab = -1
        self._category_objects = []
        self._categories = []
        self._reset_and_load()

    def _filter_kw(self) -> dict:
        df, dt = self._filter_bar.date_filter()
        return dict(
            search=self._filter_bar.search_text(),
            truck=self._filter_bar.truck_filter(),
            cashier_id=self._filter_bar.cashier_id_filter(),
            date_from=df,
            date_to=dt,
            item=self._filter_bar.item_filter(),
            description=self._filter_bar.description_filter(),
        )

    def _reset_and_load(self) -> None:
        self._reload_generation += 1
        self._loaded = 0
        self._total = 0
        self._transactions = []
        self._panel.hide()
        self._table.setRowCount(0)
        self._update_status()
        generation = self._reload_generation

        def _kick() -> None:
            if self._reload_generation != generation:
                return
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            asyncio.ensure_future(self._load_initial(generation))

        try:
            nested = asyncio.current_task() is not None
        except RuntimeError:
            nested = False
        if nested:
            self._schedule_ui(_kick)
        else:
            _kick()

    def _schedule_ui(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` on the next Qt tick (outside any running asyncio task)."""
        QTimer.singleShot(0, fn)

    def _set_busy(
        self,
        message: str,
        *,
        value: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> None:
        """Show the verify overlay with a status message (and optional progress)."""
        self._loading_overlay.show_loading(message, value=value, maximum=maximum)

    def _clear_busy(self) -> None:
        self._loading_overlay.hide_loading()

    async def _await_ui(self, fn: Callable[[], _T]) -> _T:
        """Run blocking Qt UI while this asyncio task is suspended.

        Modal dialogs pump the Qt event loop; doing that while a task is the
        *current* executing task breaks sibling tasks on Python 3.14 / qasync.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def _run() -> None:
            if fut.done():
                return
            try:
                fut.set_result(fn())
            except Exception as exc:
                fut.set_exception(exc)

        QTimer.singleShot(0, _run)
        return await fut

    def _finish_mutation(
        self,
        *,
        info: Optional[str] = None,
        error: Optional[str] = None,
        warning: Optional[str] = None,
        hide_panel: bool = False,
        reload: bool = False,
        info_title: str = "Done",
        error_title: str = "Error",
        warning_title: str = "Not Found",
    ) -> None:
        """Reload / show result dialogs after the async mutation task returns."""

        def _run() -> None:
            self._clear_busy()
            if hide_panel:
                self._panel.hide()
            if reload:
                self._reset_and_load()
            if error:
                QMessageBox.critical(self, error_title, error)
            elif warning:
                QMessageBox.warning(self, warning_title, warning)
            elif info:
                QMessageBox.information(self, info_title, info)

        self._schedule_ui(_run)

    def _update_status(self) -> None:
        if self._loading and self._loaded == 0:
            self._status.set_text("Loading…")
        elif self._total == 0:
            self._status.set_text("No records match the current filters.")
        elif self._loaded >= self._total:
            self._status.set_text(f"Showing all {self._total:,} records")
        else:
            self._status.set_text(
                f"Showing {self._loaded:,} of {self._total:,}  •  Scroll down for more"
            )

    # ── Event handlers ──────────────────────────────────────────────────────────

    def _on_subtab_changed(self, idx: int) -> None:
        self._current_tab = idx
        self._panel.hide()
        if idx == 2:
            mode = "rejected"
        elif idx == 1:
            mode = "edited"
        elif idx == 4:
            mode = "deleted"
        else:
            mode = "new"
        self._panel.set_mode(mode)
        self._set_tab_header(idx)
        self._filter_bar.set_bulk_mode(mode if idx != 3 else "issues")
        self._filters_tab = -1  # force filter reload when tab changes
        self._reset_and_load()

    def _set_tab_header(self, tab: int) -> None:
        item = self._table.horizontalHeaderItem(_COL_APP)
        if item:
            if tab == 1:
                item.setText("EDITED")
            elif tab == 2:
                item.setText("REASON")
            elif tab == 3:
                item.setText("ISSUE")
            elif tab == 4:
                item.setText("REQUESTED")
            else:
                item.setText("APP BY")

    def _on_filter_changed(self) -> None:
        self._debounce.start()

    def _on_item_filter_cascade(self) -> None:
        asyncio.ensure_future(self._cascade_from_items())

    def _on_desc_filter_cascade(self) -> None:
        asyncio.ensure_future(self._cascade_from_descs())

    async def _cascade_from_items(self) -> None:
        """Items changed → rebuild Description options from matching entries."""
        from tahmeed.services.accountant_service import (
            get_unverified_descriptions, get_rejected_descriptions,
            get_deletion_requested_descriptions,
        )
        items = self._filter_bar.item_filter()
        if self._current_tab == 2:
            descs = await get_rejected_descriptions(items=items or None)
        elif self._current_tab == 4:
            descs = await get_deletion_requested_descriptions(items=items or None)
        else:
            descs = await get_unverified_descriptions(items=items or None)
        self._filter_bar.populate_descriptions(descs, emit=False)
        self._on_filter_changed()

    async def _cascade_from_descs(self) -> None:
        """Descriptions changed → rebuild Item options from matching entries."""
        from tahmeed.services.accountant_service import (
            get_unverified_items, get_rejected_items,
            get_deletion_requested_items,
        )
        descs = self._filter_bar.description_filter()
        if self._current_tab == 2:
            items = await get_rejected_items(descriptions=descs or None)
        elif self._current_tab == 4:
            items = await get_deletion_requested_items(descriptions=descs or None)
        else:
            items = await get_unverified_items(descriptions=descs or None)
        self._filter_bar.populate_items(items, emit=False)
        self._on_filter_changed()

    def _on_scroll(self, value: int) -> None:
        bar = self._table.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if value >= bar.maximum() - 24:
            asyncio.ensure_future(self._load_more())

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        # Checkbox column toggles itself; defer other columns so double-click
        # can cancel the pending select and open the review panel instead.
        if item.column() == _COL_CHK:
            return
        self._pending_click_row = item.row()
        self._click_timer.start()

    def _commit_row_click(self) -> None:
        row = self._pending_click_row
        self._pending_click_row = None
        if row is None:
            return
        chk = self._table.item(row, _COL_CHK)
        if not chk:
            return
        new_state = Qt.Unchecked if chk.checkState() == Qt.Checked else Qt.Checked
        chk.setCheckState(new_state)

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        self._click_timer.stop()
        self._pending_click_row = None
        self._open_review_panel(item.row())

    def _open_review_panel(self, row: int) -> None:
        if row < 0 or row >= len(self._transactions):
            return
        asyncio.ensure_future(self._open_review_panel_async(row))

    async def _open_review_panel_async(self, row: int) -> None:
        tx = self._transactions[row]
        if self._tx_needs_item(tx) and tx.description:
            from tahmeed.services.description_mapping_service import resolve_category_for_description

            resolved = await resolve_category_for_description(tx.description)
            if resolved:
                _, cat_name = resolved
                tx.category_name = cat_name
                tx.item = cat_name
        await self._ensure_mapping_categories()
        cashier = self._cashier_names.get(tx.cashier_id, "") if tx.cashier_id else ""
        tab = self._current_tab
        if tab == 2:
            mode = "rejected"
        elif tab == 1:
            mode = "edited"
        elif tab == 4:
            mode = "deleted"
            requester = (
                self._cashier_names.get(tx.deletion_requested_by, "")
                if tx.deletion_requested_by else ""
            )
            cashier = requester or cashier
        else:
            mode = "new"
        self._panel.set_mode(mode)
        self._panel.load(tx, cashier, self._categories)
        self._panel.show()

    def _on_checkbox_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == _COL_CHK:
            self._update_bulk_buttons()

    def _on_header_click(self, col: int) -> None:
        if col != _COL_CHK:
            return
        if self._select_all_loading or self._loading:
            return
        # Header checkbox selects / clears every row matching the current
        # Verify tab + filters — not only the currently loaded scroll page.
        fully_selected = (
            self._total > 0
            and self._loaded >= self._total
            and self._table.rowCount() > 0
            and all(
                self._table.item(r, _COL_CHK)
                and self._table.item(r, _COL_CHK).checkState() == Qt.Checked
                for r in range(self._table.rowCount())
            )
        )
        if fully_selected:
            self._set_all_checks(Qt.Unchecked)
        else:
            asyncio.ensure_future(self._select_all_matching())

    def _set_all_checks(self, state) -> None:
        self._table.blockSignals(True)
        for r in range(self._table.rowCount()):
            it = self._table.item(r, _COL_CHK)
            if it:
                it.setCheckState(state)
        self._table.blockSignals(False)
        self._update_bulk_buttons()

    async def _select_all_matching(self) -> None:
        """Load every matching record, then check all rows."""
        if self._select_all_loading or self._loading:
            return
        if self._total <= 0 and self._table.rowCount() == 0:
            return

        self._select_all_loading = True
        gen = self._reload_generation
        self._loading_overlay.show_loading("Selecting all records…")
        try:
            from tahmeed.services.accountant_service import get_cashier_names

            while (
                gen == self._reload_generation
                and self._loaded < self._total
            ):
                remaining = self._total - self._loaded
                self._status.set_text(
                    f"Loading all records… {self._loaded:,} of {self._total:,}"
                )
                kw = self._filter_kw()
                txs = await self._fetch_page(
                    skip=self._loaded,
                    limit=max(remaining, _SELECT_ALL_CHUNK),
                    kw=kw,
                )
                if gen != self._reload_generation:
                    return
                if not txs:
                    break

                page_ids = {tx.cashier_id for tx in txs if tx.cashier_id}
                page_ids |= {tx.last_edited_by for tx in txs if tx.last_edited_by}
                page_ids |= {
                    tx.deletion_requested_by
                    for tx in txs if tx.deletion_requested_by
                }
                cname_map = dict(self._cashier_names)
                if page_ids:
                    page_names = await get_cashier_names(list(page_ids))
                    cname_map.update(page_names)
                    self._cashier_names = cname_map

                self._fill_table(txs, cname_map, self._loaded, append=True)
                self._loaded += len(txs)

            if gen != self._reload_generation:
                return
            self._set_all_checks(Qt.Checked)
        except Exception:
            pass
        finally:
            self._select_all_loading = False
            self._loading_overlay.hide_loading()
            if gen == self._reload_generation:
                self._update_status()

    def _update_bulk_buttons(self) -> None:
        any_checked = any(
            self._table.item(r, _COL_CHK) and
            self._table.item(r, _COL_CHK).checkState() == Qt.Checked
            for r in range(self._table.rowCount())
        )
        self._filter_bar.set_bulk_enabled(any_checked)

    # ── Data load ───────────────────────────────────────────────────────────────

    async def _load_filter_options(self, tab: int) -> Dict:
        from tahmeed.services.accountant_service import (
            get_unverified_trucks, get_unverified_cashier_ids,
            get_unverified_items, get_unverified_descriptions,
            get_rejected_trucks, get_rejected_cashier_ids,
            get_rejected_items, get_rejected_descriptions,
            get_deletion_requested_trucks, get_deletion_requested_cashier_ids,
            get_deletion_requested_items, get_deletion_requested_descriptions,
            get_cashier_names,
        )
        from tahmeed.services.category_service import get_all_categories

        selected_items = self._filter_bar.item_filter()
        selected_descs = self._filter_bar.description_filter()

        # Reuse cached categories unless refresh() cleared them.
        async def _cats() -> List[Category]:
            if self._category_objects:
                return self._category_objects
            return await get_all_categories()

        if tab == 2:
            cats, trucks, cashier_ids, items, descs = await asyncio.gather(
                _cats(),
                get_rejected_trucks(),
                get_rejected_cashier_ids(),
                get_rejected_items(descriptions=selected_descs or None),
                get_rejected_descriptions(items=selected_items or None),
            )
        elif tab == 4:
            cats, trucks, cashier_ids, items, descs = await asyncio.gather(
                _cats(),
                get_deletion_requested_trucks(),
                get_deletion_requested_cashier_ids(),
                get_deletion_requested_items(descriptions=selected_descs or None),
                get_deletion_requested_descriptions(items=selected_items or None),
            )
        else:
            cats, trucks, cashier_ids, items, descs = await asyncio.gather(
                _cats(),
                get_unverified_trucks(),
                get_unverified_cashier_ids(),
                get_unverified_items(descriptions=selected_descs or None),
                get_unverified_descriptions(items=selected_items or None),
            )
        self._categories = [c.name for c in cats]
        self._category_objects = cats
        cname_map = await get_cashier_names(cashier_ids) if cashier_ids else {}
        self._filter_bar.populate_trucks(trucks)
        self._filter_bar.populate_items(items)
        self._filter_bar.populate_descriptions(descs)
        clist = sorted(
            [(cname_map.get(ci, "Unknown cashier"), ci) for ci in cashier_ids],
            key=lambda x: x[0],
        )
        self._filter_bar.populate_cashiers(clist)
        self._filters_tab = tab
        return cname_map

    async def _fetch_page(self, *, skip: int, limit: int, kw: dict):
        from tahmeed.services.accountant_service import (
            get_unverified_filtered, get_edited_transactions,
            get_rejected_transactions, get_deletion_requested_filtered,
        )
        from tahmeed.services.daily_import_service import get_issue_transactions

        tab = self._current_tab
        if tab == 2:
            return await get_rejected_transactions(
                **kw, limit=limit, skip=skip,
            )
        if tab == 1:
            return await get_edited_transactions(
                **kw, limit=limit, skip=skip,
            )
        if tab == 3:
            return await get_issue_transactions(
                skip=skip, limit=limit, **kw,
            )
        if tab == 4:
            return await get_deletion_requested_filtered(
                **kw, limit=limit, skip=skip,
            )
        return await get_unverified_filtered(
            **kw, limit=limit, skip=skip, edited=False,
        )

    async def _fetch_counts(self, kw: dict) -> Tuple[int, int, int, int, int, int]:
        from tahmeed.services.accountant_service import (
            count_unverified_filtered, count_edited_transactions,
            count_rejected_transactions, count_deletion_requested_filtered,
            get_pending_count,
        )
        from tahmeed.services.daily_import_service import count_issue_transactions

        (
            new_count, edited_count, rejected_count, issues_count,
            deleted_count, pending,
        ) = await asyncio.gather(
            count_unverified_filtered(**kw, edited=False),
            count_edited_transactions(**kw),
            count_rejected_transactions(**kw),
            count_issue_transactions(**kw),
            count_deletion_requested_filtered(**kw),
            get_pending_count(),
        )
        return (
            new_count, edited_count, rejected_count,
            issues_count, deleted_count, pending,
        )

    async def _load_initial(self, generation: int) -> None:
        self._loading = True
        tab_labels = ("New", "Edited", "Rejected", "Issues", "Deleted")
        label = tab_labels[self._current_tab] if 0 <= self._current_tab < 5 else "Verify"
        self._loading_overlay.show_loading(f"Loading {label}…")
        self._update_status()
        try:
            from tahmeed.services.accountant_service import get_cashier_names

            kw = self._filter_kw()
            tab = self._current_tab
            need_filters = self._filters_tab != tab

            # Page + counts first so rows appear quickly; filters load in parallel.
            page_task = asyncio.ensure_future(
                self._fetch_page(skip=0, limit=_SCROLL_CHUNK, kw=kw),
            )
            counts_task = asyncio.ensure_future(self._fetch_counts(kw))
            filters_task = (
                asyncio.ensure_future(self._load_filter_options(tab))
                if need_filters else None
            )

            txs, counts = await asyncio.gather(page_task, counts_task)
            if generation != self._reload_generation:
                return

            (
                new_count, edited_count, rejected_count,
                issues_count, deleted_count, pending,
            ) = counts
            if tab == 2:
                total = rejected_count
            elif tab == 1:
                total = edited_count
            elif tab == 3:
                total = issues_count
            elif tab == 4:
                total = deleted_count
            else:
                total = new_count

            # Paint rows immediately; filter dropdown names can arrive right after.
            cname_map: Dict = dict(self._cashier_names)
            page_ids = {tx.cashier_id for tx in txs if tx.cashier_id}
            page_ids |= {tx.last_edited_by for tx in txs if tx.last_edited_by}
            page_ids |= {
                tx.deletion_requested_by for tx in txs if tx.deletion_requested_by
            }
            if page_ids:
                page_names = await get_cashier_names(list(page_ids))
                cname_map.update(page_names)

            self._cashier_names = cname_map
            self._total = total
            self._subtabs.set_counts(
                new_count, edited_count, rejected_count, issues_count, deleted_count,
            )
            self._fill_table(txs, cname_map, 0, append=False)
            self._loaded = len(txs)
            self._pending_badge.setText(str(pending))
            self.badge_updated.emit(pending)
            self._loading = False
            self._loading_overlay.hide_loading()
            self._update_status()

            if filters_task is not None:
                try:
                    filter_names = await filters_task
                    if generation != self._reload_generation:
                        return
                    # Merge any extra cashier names from filter distincts.
                    if filter_names:
                        self._cashier_names.update(filter_names)
                except Exception:
                    pass
            return
        except Exception as exc:
            if generation == self._reload_generation:
                self._show_empty(f"Failed to load: {exc}")
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()
            if generation == self._reload_generation:
                self._update_status()

    async def _load_more(self) -> None:
        if self._scroll_loading or self._loading or self._select_all_loading:
            return
        if self._loaded >= self._total:
            return
        self._scroll_loading = True
        self._update_status()
        try:
            from tahmeed.services.accountant_service import get_cashier_names

            kw = self._filter_kw()
            gen = self._reload_generation
            txs = await self._fetch_page(
                skip=self._loaded, limit=_SCROLL_CHUNK, kw=kw,
            )
            if gen != self._reload_generation:
                return

            page_ids = {tx.cashier_id for tx in txs if tx.cashier_id}
            page_ids |= {tx.last_edited_by for tx in txs if tx.last_edited_by}
            page_ids |= {
                tx.deletion_requested_by for tx in txs if tx.deletion_requested_by
            }
            cname_map = dict(self._cashier_names)
            if page_ids:
                page_names = await get_cashier_names(list(page_ids))
                cname_map.update(page_names)
                self._cashier_names = cname_map

            if txs:
                self._fill_table(txs, cname_map, self._loaded, append=True)
                self._loaded += len(txs)
        except Exception:
            pass
        finally:
            self._scroll_loading = False
            self._update_status()

    # ── Table fill ──────────────────────────────────────────────────────────────

    def _fill_table(
        self,
        txs: List[Transaction],
        cnames: Dict,
        skip: int,
        *,
        append: bool = False,
    ) -> None:
        tab = self._current_tab  # 0=New, 1=Edited, 2=Rejected, 3=Issues, 4=Deleted
        t = self._table

        if not append:
            self._transactions = list(txs)
            self._panel.hide()
            t.clearSpans()
            t.blockSignals(True)
            t.setRowCount(0)
            if not txs:
                t.blockSignals(False)
                msg = (
                    "No rejected entries." if tab == 2 else
                    "No issue entries (duplicates or mixed dates)." if tab == 3 else
                    "No deletion requests." if tab == 4 else
                    "No edited transactions match the current filters." if tab == 1 else
                    "No pending transactions match the current filters."
                )
                self._show_empty(msg)
                self._update_bulk_buttons()
                return
        else:
            self._transactions.extend(txs)
            t.blockSignals(True)

        for i, tx in enumerate(txs):
            r = t.rowCount()
            t.insertRow(r)

            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            chk.setCheckState(Qt.Unchecked)
            chk.setTextAlignment(Qt.AlignCenter)
            t.setItem(r, _COL_CHK, chk)

            sn = QTableWidgetItem(str(skip + i + 1))
            sn.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            sn.setForeground(QColor(_T2))
            sn.setFlags(sn.flags() & ~Qt.ItemIsEditable)
            t.setItem(r, _COL_SN, sn)

            date_item = _cell(_fmt_date(tx.date), color=_T2)
            if isinstance(tx.date, datetime) and isinstance(tx.created_at, datetime):
                if tx.date.date() != tx.created_at.date():
                    date_item.setToolTip(
                        f"Submitted on {tx.created_at.strftime('%d %b %Y')} "
                        f"but dated {tx.date.strftime('%d %b %Y')}"
                    )
            t.setItem(r, _COL_DATE, date_item)

            cashier = cnames.get(tx.cashier_id, "") if tx.cashier_id else ""
            t.setItem(r, _COL_CASH, _cell(_short_name(cashier)))

            t.setItem(r, _COL_ITEM, _cell(tx.item or tx.category_name or "—"))

            desc_item = _cell(tx.description or "—")
            if tx.possible_duplicate:
                desc_item.setBackground(QColor("#FEE2E2"))
                desc_item.setForeground(QColor(_RED))
                desc_item.setToolTip(
                    "Cashier overrode a duplicate warning — similar entry exists in the system"
                )
            t.setItem(r, _COL_DESC, desc_item)

            t.setItem(r, _COL_TRUCK, _cell(tx.truck_number or "—", color=_T2))

            t.setItem(
                r,
                _COL_AMT,
                _cell(_fmt_amount(tx), align=Qt.AlignRight | Qt.AlignVCenter),
            )

            t.setItem(r, _COL_MEO, _cell(tx.memo or "—", color=_T2))

            notes_text = "Refund to my float" if tx.notes_flag else ""
            n_item = _cell(notes_text)
            if tx.notes_flag:
                n_item.setForeground(QColor(_AMBER))
            t.setItem(r, _COL_NOTES, n_item)

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

            if tab == 2:
                reason = (tx.rejection_reason or "—")[:60]
                reason_item = _cell(reason, color=_RED)
                if tx.rejection_reason:
                    reason_item.setToolTip(tx.rejection_reason)
                t.setItem(r, _COL_APP, reason_item)
                row_bg = "#FFF0F0"
            elif tab == 1:
                rel = _fmt_relative(tx.last_edited_at)
                ed_item = _cell(rel or "—", color=_AMBER)
                editor = cnames.get(tx.last_edited_by, "") if tx.last_edited_by else ""
                if tx.last_edited_at:
                    ed_item.setToolTip(
                        f"Edited by {editor or '—'} on {_fmt_date(tx.last_edited_at)}"
                    )
                t.setItem(r, _COL_APP, ed_item)
                row_bg = "#FFF7ED"
            elif tab == 3:
                bits = []
                if getattr(tx, "possible_duplicate", False):
                    bits.append("Duplicate")
                if getattr(tx, "date_discrepancy", False):
                    primary = getattr(tx, "import_primary_date", None)
                    if primary and hasattr(primary, "strftime"):
                        bits.append(f"Date ≠ {primary.strftime('%d/%m/%Y')}")
                    else:
                        bits.append("Mixed date")
                issue_item = _cell(" · ".join(bits) if bits else "Issue", color=_AMBER)
                tip_parts = []
                if getattr(tx, "possible_duplicate", False):
                    tip_parts.append("Cashier overrode a duplicate warning.")
                if getattr(tx, "date_discrepancy", False):
                    tip_parts.append(
                        "Row date differs from the Excel upload's main date."
                    )
                if tip_parts:
                    issue_item.setToolTip(" ".join(tip_parts))
                t.setItem(r, _COL_APP, issue_item)
                row_bg = "#FFFBEB"
            elif tab == 4:
                rel = _fmt_relative(tx.deletion_requested_at)
                req_item = _cell(rel or "—", color=_RED)
                requester = (
                    cnames.get(tx.deletion_requested_by, "")
                    if tx.deletion_requested_by else ""
                )
                if tx.deletion_requested_at:
                    req_item.setToolTip(
                        f"Deletion requested by {requester or '—'} on "
                        f"{_fmt_date(tx.deletion_requested_at)}"
                    )
                t.setItem(r, _COL_APP, req_item)
                row_bg = "#FEF2F2"
            else:
                # New tab — same white/slate zebra as Master / Separate Expenses.
                t.setItem(r, _COL_APP, _cell(tx.approver or "—", color=_T2))
                row_bg = None

            # Manual zebra (or soft status wash) — matches Separate Expenses.
            _finish_table_row(t, r, row_bg)

            # Re-apply cell-level highlights after row background.
            if tx.possible_duplicate:
                desc_cell = t.item(r, _COL_DESC)
                if desc_cell:
                    desc_cell.setBackground(QColor("#FEE2E2"))
                    desc_cell.setForeground(QColor(_RED))

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

    def _on_approved(self, tx_id: ObjectId, category: str = "") -> None:
        asyncio.ensure_future(self._do_approve(tx_id, category))

    def _on_rejected(self, tx_id: ObjectId, reason: str) -> None:
        asyncio.ensure_future(self._do_reject(tx_id, reason))

    def _on_save_category(self, tx_id: ObjectId, category: str) -> None:
        asyncio.ensure_future(self._do_save_category(tx_id, category))

    def _on_returned(self, tx_id: ObjectId) -> None:
        asyncio.ensure_future(self._do_return_to_inbox(tx_id))

    def _on_deletion_confirmed(self, tx_id: ObjectId) -> None:
        if QMessageBox.question(
            self, "Confirm Delete",
            "Permanently delete this approved expense from Master Expenses?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            asyncio.ensure_future(self._do_confirm_deletion(tx_id))

    def _on_deletion_restored(self, tx_id: ObjectId) -> None:
        asyncio.ensure_future(self._do_restore_deletion(tx_id))

    def _selected_tx_ids(self) -> List[ObjectId]:
        checked = [
            r for r in range(self._table.rowCount())
            if self._table.item(r, _COL_CHK) and
               self._table.item(r, _COL_CHK).checkState() == Qt.Checked
        ]
        return [
            self._transactions[r]._id
            for r in checked
            if r < len(self._transactions) and self._transactions[r]._id
        ]

    def _on_bulk_approve(self) -> None:
        checked = [
            r for r in range(self._table.rowCount())
            if self._table.item(r, _COL_CHK) and
               self._table.item(r, _COL_CHK).checkState() == Qt.Checked
        ]
        if not checked:
            return
        tx_rows = [(r, self._transactions[r]) for r in checked if r < len(self._transactions)]
        if not tx_rows:
            return

        if self._current_tab == 1:
            msg = f"Re-approve {len(tx_rows)} edited transaction(s)?"
            title = "Re-approve Edited"
        else:
            msg = f"Approve {len(tx_rows)} selected transaction(s)?"
            title = "Bulk Approve"

        if QMessageBox.question(self, title, msg,
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            tx_ids = [tx._id for _, tx in tx_rows if tx._id]
            asyncio.ensure_future(self._do_bulk_approve(tx_ids))

    def _on_bulk_reject(self) -> None:
        tx_ids = self._selected_tx_ids()
        if not tx_ids:
            return
        reason, ok = QInputDialog.getMultiLineText(
            self,
            "Reject Selected",
            (
                f"Enter a reason for rejecting {len(tx_ids)} transaction(s).\n"
                "The cashier will see this note on each rejected entry."
            ),
            "",
        )
        if not ok:
            return
        reason = reason.strip()
        if not reason:
            QMessageBox.warning(
                self,
                "Note Required",
                "Please enter a reason before rejecting these entries.",
            )
            return
        if (
            QMessageBox.question(
                self,
                "Reject Selected",
                f"Reject {len(tx_ids)} transaction(s) with this reason?",
                QMessageBox.Yes | QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        asyncio.ensure_future(self._do_bulk_reject(tx_ids, reason))

    def _on_bulk_confirm_delete(self) -> None:
        tx_ids = self._selected_tx_ids()
        if not tx_ids:
            return
        if QMessageBox.question(
            self, "Confirm Delete",
            f"Permanently delete {len(tx_ids)} approved expense(s) from Master Expenses?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            asyncio.ensure_future(self._do_bulk_confirm_deletions(tx_ids))

    def _on_bulk_restore(self) -> None:
        tx_ids = self._selected_tx_ids()
        if not tx_ids:
            return
        if QMessageBox.question(
            self, "Restore",
            f"Restore {len(tx_ids)} expense(s) to Master Expenses?",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            asyncio.ensure_future(self._do_bulk_restore_deletions(tx_ids))

    # ── Item resolution (description → item mappings) ───────────────────────────

    @staticmethod
    def _tx_needs_item(tx: Transaction) -> bool:
        from tahmeed.services.description_mapping_service import transaction_needs_item

        return transaction_needs_item(tx.item, tx.category_name)

    def _category_by_name(self, name: str) -> Optional[Category]:
        key = (name or "").strip().lower()
        if not key:
            return None
        return next((c for c in self._category_objects if c.name.lower() == key), None)

    async def _ensure_mapping_categories(self) -> List[Category]:
        """Load the item catalog used by the mapping dialog (always fresh)."""
        from tahmeed.services.api_models import desktop_document
        from tahmeed.services.category_service import get_all_categories

        self._set_busy("Loading items…")
        categories: List[Category] = []
        try:
            categories = await get_all_categories(include_inactive=True)
        except Exception as exc:
            self._clear_busy()
            await self._await_ui(lambda: QMessageBox.critical(
                self,
                "Items Unavailable",
                f"Could not load items from the server:\n{exc}",
            ))
            return []

        if not categories:
            try:
                from tahmeed.db.connection import get_db

                db = get_db()
                docs = await db.categories.find({"active": {"$ne": False}}).sort("name", 1).to_list(
                    length=None
                )
                categories = [
                    Category.from_doc(desktop_document(doc)) for doc in docs
                ]
            except Exception:
                pass

        self._category_objects = categories
        self._categories = [c.name for c in categories]
        return categories

    async def _assign_item_and_remember(
        self,
        tx_id: ObjectId,
        description: str,
        category: Category,
    ) -> None:
        from tahmeed.services.accountant_service import update_transaction_category
        from tahmeed.services.description_mapping_service import save_mapping

        await update_transaction_category(tx_id, category.name, category._id)
        if description.strip():
            await save_mapping(description, category._id, category.name)

    async def _resolve_items_for_transactions(self, txs: List[Transaction]) -> bool:
        """Ensure each transaction has an item; prompt when mapping is unknown.

        All mapping dialogs run inside a single ``_await_ui`` callback (no asyncio
        resume between prompts). Mappings and category updates are persisted in
        bulk afterward.
        """
        from tahmeed.services.description_mapping_service import (
            get_mappings_for_descriptions,
            normalize_description,
            save_mapping,
            transaction_needs_item,
        )
        from tahmeed.services.accountant_service import bulk_update_transaction_category
        from tahmeed.ui.dialogs.description_mapping_dialog import DescriptionMappingDialog

        pending = [tx for tx in txs if transaction_needs_item(tx.item, tx.category_name)]
        if not pending:
            return True

        categories = await self._ensure_mapping_categories()
        if not categories:
            self._clear_busy()
            await self._await_ui(lambda: QMessageBox.warning(
                self,
                "No Items",
                "There are no items to map descriptions to.\n\n"
                "Open Manage → Items and import your Chart of Accounts, "
                "or add items manually, then try again.",
            ))
            return False

        self._set_busy("Looking up saved description mappings…")
        descriptions = [tx.description for tx in pending if tx.description]
        mappings = await get_mappings_for_descriptions(descriptions)

        still_need: List[Transaction] = []
        # Group known mappings by category for one bulk update each.
        known_groups: Dict[Tuple[str, ObjectId], List[ObjectId]] = {}
        for tx in pending:
            if not tx._id:
                continue
            key = normalize_description(tx.description or "")
            mapping = mappings.get(key)
            if mapping:
                gk = (mapping.category_name, mapping.category_id)
                known_groups.setdefault(gk, []).append(tx._id)
            elif not (tx.description or "").strip():
                self._clear_busy()
                await self._await_ui(lambda: QMessageBox.warning(
                    self,
                    "Missing Item",
                    "One or more selected entries have no item and no description.\n"
                    "Assign a category manually before approving.",
                ))
                return False
            else:
                still_need.append(tx)

        if known_groups:
            self._set_busy("Applying saved item mappings…")
            await asyncio.gather(*[
                bulk_update_transaction_category(ids, name, cat_id)
                for (name, cat_id), ids in known_groups.items()
            ])

        if not still_need:
            return True

        groups: Dict[str, List[Transaction]] = {}
        display: Dict[str, str] = {}
        for tx in still_need:
            key = normalize_description(tx.description or "")
            groups.setdefault(key, []).append(tx)
            display[key] = tx.description or key

        # Show every mapping dialog in one Qt-tick callback. Returning to the
        # asyncio task between dialogs (via repeated _await_ui) lets qasync /
        # Py3.14 nest tasks during exec() and aborts the sequence mid-way —
        # user sees ~3–5 prompts then the window vanishes until they approve again.
        keys = list(groups.keys())
        total_unique = len(keys)

        def _prompt_all() -> Optional[Dict[str, Category]]:
            chosen: Dict[str, Category] = {}
            remaining = total_unique
            for key in keys:
                dlg = DescriptionMappingDialog(
                    description=display[key],
                    row_count=len(groups[key]),
                    categories=categories,
                    remaining=remaining,
                    parent=self,
                    scope_label="in verify inbox",
                    cancel_label="Cancel",
                    total=total_unique,
                )
                if dlg.exec() != QDialog.Accepted:
                    return None
                cat = dlg.selected_category()
                if cat is None:
                    return None
                chosen[key] = cat
                remaining -= 1
            return chosen

        # Hide busy UI so mapping dialogs are not covered / delayed behind it.
        self._clear_busy()
        assignments = await self._await_ui(_prompt_all)
        if not assignments:
            return False

        # Persist mappings + category updates in parallel after all dialogs.
        self._set_busy(
            f"Saving {len(assignments):,} item mapping(s)…",
            value=0,
            maximum=max(1, len(assignments)),
        )
        persist = []
        for key, cat in assignments.items():
            persist.append(save_mapping(display[key], cat._id, cat.name))
            ids = [tx._id for tx in groups[key] if tx._id]
            if ids:
                persist.append(
                    bulk_update_transaction_category(ids, cat.name, cat._id)
                )
        if persist:
            await asyncio.gather(*persist)

        return True

    async def _prepare_transactions_for_approval(
        self,
        txs: List[Transaction],
        category_overrides: Optional[Dict[ObjectId, str]] = None,
    ) -> bool:
        """Apply panel selections and description mappings before approval."""
        category_overrides = category_overrides or {}
        manual: List[Transaction] = []
        for tx in txs:
            if not tx._id:
                continue
            chosen = category_overrides.get(tx._id, "").strip()
            if chosen:
                current = (tx.category_name or tx.item or "").strip()
                if chosen.lower() != current.lower():
                    cat = self._category_by_name(chosen)
                    if cat is None:
                        chosen_name = chosen
                        await self._await_ui(lambda: QMessageBox.warning(
                            self,
                            "Invalid Item",
                            f'"{chosen_name}" is not a known item. Pick one from the category list.',
                        ))
                        return False
                    if self._tx_needs_item(tx):
                        await self._assign_item_and_remember(
                            tx._id, tx.description or "", cat,
                        )
                    else:
                        from tahmeed.services.accountant_service import update_transaction_category
                        await update_transaction_category(tx._id, cat.name, cat._id)
            elif self._tx_needs_item(tx):
                manual.append(tx)
        if manual:
            return await self._resolve_items_for_transactions(manual)
        return True

    # ── Async service calls ─────────────────────────────────────────────────────

    async def _do_approve(self, tx_id: ObjectId, category_override: str = "") -> None:
        from tahmeed.services.accountant_service import (
            approve_transaction, re_approve_transaction, get_pending_count,
            get_transactions_by_ids,
        )
        self._set_busy("Preparing approval…")
        try:
            txs = await get_transactions_by_ids([tx_id])
            if not txs:
                self._finish_mutation(
                    warning="Transaction could not be loaded.",
                    warning_title="Not Found",
                )
                return
            overrides = {tx_id: category_override} if category_override else {}
            if not await self._prepare_transactions_for_approval(txs, overrides):
                self._clear_busy()
                return
            self._set_busy("Approving…")
            if self._current_tab == 1:
                await re_approve_transaction(tx_id, self._user._id)
            else:
                await approve_transaction(tx_id, self._user._id)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            self._finish_mutation(hide_panel=True, reload=True)
        except Exception as exc:
            self._finish_mutation(error=f"Failed to approve: {exc}")

    async def _do_reject(self, tx_id: ObjectId, reason: str) -> None:
        from tahmeed.services.accountant_service import reject_transaction, get_pending_count
        try:
            await reject_transaction(tx_id, reason)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            self._finish_mutation(hide_panel=True, reload=True)
        except Exception as exc:
            self._finish_mutation(error=f"Failed to reject: {exc}")

    async def _do_save_category(self, tx_id: ObjectId, category: str) -> None:
        from tahmeed.services.accountant_service import update_transaction_category
        try:
            await update_transaction_category(tx_id, category)
        except Exception:
            pass

    async def _do_bulk_reject(self, tx_ids: List[ObjectId], reason: str) -> None:
        from tahmeed.services.accountant_service import bulk_reject_transactions, get_pending_count

        self._set_busy(f"Rejecting {len(tx_ids):,} transaction(s)…")
        try:
            n = await bulk_reject_transactions(tx_ids, reason)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            self._finish_mutation(
                info=f"Rejected {n} transaction(s).",
                reload=True,
            )
        except Exception as exc:
            self._finish_mutation(error=f"Bulk reject failed: {exc}")

    async def _do_bulk_approve(self, tx_ids: List[ObjectId]) -> None:
        from tahmeed.services.accountant_service import (
            bulk_approve_transactions, bulk_re_approve_transactions, get_pending_count,
            get_transactions_by_ids,
        )
        self._set_busy(f"Preparing {len(tx_ids):,} approval(s)…")
        try:
            txs = await get_transactions_by_ids(tx_ids)
            self._set_busy("Checking items & description mappings…")
            if not await self._prepare_transactions_for_approval(txs):
                self._clear_busy()
                return
            self._set_busy(f"Approving {len(tx_ids):,} transaction(s)…")
            if self._current_tab == 1:
                n = await bulk_re_approve_transactions(tx_ids, self._user._id)
            else:
                n = await bulk_approve_transactions(tx_ids, self._user._id)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            self._finish_mutation(
                info=f"Approved {n} transaction(s) successfully.",
                reload=True,
            )
        except Exception as exc:
            self._finish_mutation(error=f"Bulk approve failed: {exc}")

    async def _do_return_to_inbox(self, tx_id: ObjectId) -> None:
        from tahmeed.services.accountant_service import return_to_inbox, get_pending_count
        try:
            ok = await return_to_inbox(tx_id)
            if ok:
                count = await get_pending_count()
                self.badge_updated.emit(count)
                self._finish_mutation(hide_panel=True, reload=True)
            else:
                self._finish_mutation(
                    warning="Could not return this entry — it may have already been updated.",
                )
        except Exception as exc:
            self._finish_mutation(error=f"Failed to return entry: {exc}")

    async def _do_confirm_deletion(self, tx_id: ObjectId) -> None:
        from tahmeed.services.accountant_service import confirm_deletion, get_pending_count
        try:
            ok = await confirm_deletion(tx_id)
            if not ok:
                self._finish_mutation(
                    warning="Could not delete this entry — it may have already been updated.",
                )
                return
            count = await get_pending_count()
            self.badge_updated.emit(count)
            self._finish_mutation(hide_panel=True, reload=True)
        except Exception as exc:
            self._finish_mutation(error=f"Failed to confirm deletion: {exc}")

    async def _do_restore_deletion(self, tx_id: ObjectId) -> None:
        from tahmeed.services.accountant_service import restore_deletion, get_pending_count
        try:
            ok = await restore_deletion(tx_id)
            if not ok:
                self._finish_mutation(
                    warning="Could not restore this entry — it may have already been updated.",
                )
                return
            count = await get_pending_count()
            self.badge_updated.emit(count)
            self._finish_mutation(hide_panel=True, reload=True)
        except Exception as exc:
            self._finish_mutation(error=f"Failed to restore: {exc}")

    async def _do_bulk_confirm_deletions(self, tx_ids: List[ObjectId]) -> None:
        from tahmeed.services.accountant_service import (
            bulk_confirm_deletions, get_pending_count,
        )
        try:
            n = await bulk_confirm_deletions(tx_ids)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            self._finish_mutation(
                info=f"Permanently deleted {n} expense(s).",
                reload=True,
            )
        except Exception as exc:
            self._finish_mutation(error=f"Bulk delete failed: {exc}")

    async def _do_bulk_restore_deletions(self, tx_ids: List[ObjectId]) -> None:
        from tahmeed.services.accountant_service import (
            bulk_restore_deletions, get_pending_count,
        )
        try:
            n = await bulk_restore_deletions(tx_ids)
            count = await get_pending_count()
            self.badge_updated.emit(count)
            self._finish_mutation(
                info=f"Restored {n} expense(s) to Master.",
                reload=True,
            )
        except Exception as exc:
            self._finish_mutation(error=f"Bulk restore failed: {exc}")
