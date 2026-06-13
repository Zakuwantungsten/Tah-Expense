"""
DailyRegister — unified QuickBooks-style cashier register.

Layout (top to bottom):
  ┌─ Date nav bar ────────────────────────────────────────┐
  │  ← Prev Day  |  09 June 2026 ▼  |  Next Day →  Today │
  ├─ Column headers ──────────────────────────────────────┤
  │  # | Date | Description | Truck | Memo | TZS | … │
  ├─ Saved rows (read-only, light-blue) ──────────────────┤
  │  ...existing transactions for the selected date...     │
  ├─ New entry rows (editable, white) ────────────────────┤
  │  ...blank rows for new entry...                        │
  ├─ Footer ──────────────────────────────────────────────┤
  │  5 entries  ·  TZS 2,202,500                           │
  └───────────────────────────────────────────────────────┘

Keyboard:
  Arrow / Tab / Enter       navigate cells
  Ctrl+C / Ctrl+V / Ctrl+X  clipboard (TSV — Excel-compatible)
  Delete / Backspace        clear selected cells
  Right-click               context menu (delete saved / edit row ops)

Save:
  save_rows() saves all non-empty editable rows and reloads the register.
  Saved rows are marked read-only with a blue tint.
"""

import asyncio
import csv
from datetime import datetime, date, timedelta
from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton, QLabel,
    QTableWidget, QTableWidgetItem, QApplication,
    QStyledItemDelegate, QMenu, QFileDialog,
    QMessageBox, QAbstractItemView, QHeaderView, QDateEdit,
    QStyle, QFrame, QComboBox,
)
from PySide6.QtCore import Qt, Signal, QDate, QEvent, QRect, QSize, QObject, QTimer
from PySide6.QtGui import QKeyEvent, QColor, QBrush, QFont, QPen, QPainter

from tahmeed.models.category import Category
from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.services.truck_service import search_trucks
from tahmeed.services.cashier_service import (
    get_transactions_by_date, save_transaction, delete_transaction,
    search_descriptions,
)
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit

# ---------------------------------------------------------------------------
# Column indices
# ---------------------------------------------------------------------------
COL_SNO     = 0
COL_DATE    = 1
COL_ITEM    = 2
COL_DESC    = 3
COL_TRUCK   = 4
COL_MEMO    = 5
COL_NOTES   = 6
COL_TZS     = 7
COL_RECEIPT = 8
COL_OWN     = 9
COL_APR     = 10

HEADERS = [
    "S/NO", "Date", "Item", "Description", "Truck No.",
    "Memo", "Notes", "TZS", "Receipt", "Ownership", "APR BY",
]

CHECK_COLS       = {COL_NOTES}
READONLY_COLS    = {COL_SNO}
_DATA_SKIP_COLS  = READONLY_COLS | {COL_NOTES, COL_RECEIPT}
DEFAULT_EDITABLE_ROWS = 20

# Colors
SAVED_BG  = QColor("#fff8f0")
NEW_BG    = QColor("#ffffff")
EMPTY_BG  = QColor("#fafafa")
NEG_COLOR = QColor("#dc2626")
SNO_BG    = QColor("#f1f5f9")

# Nav-bar tool button style
_NAV_BTN_STYLE = """
QToolButton {
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 5px;
    padding: 4px 10px;
    color: #374151;
    font-size: 12px;
}
QToolButton:hover   { background: #f9fafb; border-color: #9ca3af; }
QToolButton:pressed { background: #e5e7eb; }
"""

# Footer QB-style icon+text-below action buttons
_FOOTER_BTN_STYLE = """
QToolButton {
    background: transparent;
    border: none;
    padding: 3px 10px 2px 10px;
    color: #374151;
    font-size: 11px;
    min-width: 46px;
}
QToolButton:hover   { background: #ececec; border-radius: 4px; }
QToolButton:pressed { background: #dcdcdc; border-radius: 4px; }
QToolButton:disabled { color: #9ca3af; }
"""


# ---------------------------------------------------------------------------
# Delegates
# ---------------------------------------------------------------------------

class _DescriptionDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        ed = TruckLineEdit(fetch_fn=search_descriptions, parent=parent)
        ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
        return ed

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text().strip())

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _TruckDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        ed = TruckLineEdit(fetch_fn=search_trucks, parent=parent)
        ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
        return ed

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text().strip().upper())

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _DateDelegate(QStyledItemDelegate):
    def __init__(self, get_current_date, parent=None):
        super().__init__(parent)
        self._get_current_date = get_current_date

    def paint(self, painter, option, index) -> None:
        value = (index.data() or "").strip()
        is_focused = bool(option.state & QStyle.State_Selected)

        if not value and is_focused:
            # Focused empty cell — show register date as a blue highlighted suggestion
            cur = self._get_current_date()
            suggestion = QDate(cur.year, cur.month, cur.day).toString("dd/MM/yyyy")
            painter.save()
            painter.fillRect(option.rect, QColor("#cde0f5"))
            painter.setPen(QColor("#0077C5"))
            painter.drawText(
                option.rect.adjusted(6, 0, -22, 0),
                Qt.AlignVCenter | Qt.AlignLeft,
                suggestion,
            )
            painter.restore()
        else:
            super().paint(painter, option, index)

        # Calendar icon — only when cell has data or is focused
        if value or is_focused:
            sp_icon = QApplication.style().standardIcon(QStyle.SP_FileDialogDetailedView)
            pix = sp_icon.pixmap(QSize(14, 14))
            ix = option.rect.right() - 18
            iy = option.rect.top() + (option.rect.height() - 14) // 2
            painter.drawPixmap(ix, iy, pix)

    def createEditor(self, parent, option, index):
        ed = QDateEdit(parent)
        ed.setCalendarPopup(True)
        ed.setDisplayFormat("dd/MM/yyyy")
        ed.lineEdit().setReadOnly(True)  # calendar-only — no manual typing
        ed.setStyleSheet(
            "QDateEdit { color: #111827; background: #ffffff; }"
            "QDateEdit::drop-down { width: 20px; }"
        )
        return ed

    def setEditorData(self, editor, index):
        text = index.data() or ""
        try:
            dt = datetime.strptime(text, "%d/%m/%Y")
            editor.setDate(QDate(dt.year, dt.month, dt.day))
        except ValueError:
            cur = self._get_current_date()
            editor.setDate(QDate(cur.year, cur.month, cur.day))
        QTimer.singleShot(50, editor.showPopup)  # open calendar immediately on click

    def setModelData(self, editor, model, index):
        model.setData(index, editor.date().toString("dd/MM/yyyy"))

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _CheckDelegate(QStyledItemDelegate):
    """Centered checkbox; toggles via click/Space/Return."""

    def paint(self, painter, option, index) -> None:
        value = index.data(Qt.UserRole)
        if value is None:
            return  # blank row — nothing to draw
        checked = value is True
        size = 13
        cx = option.rect.x() + (option.rect.width() - size) // 2
        cy = option.rect.y() + (option.rect.height() - size) // 2
        box = QRect(cx, cy, size, size)
        painter.save()
        if checked:
            painter.fillRect(box, QColor("#E85D04"))
            painter.setPen(QPen(QColor("#DC2F02"), 1))
            painter.drawRect(box)
            painter.setPen(QPen(QColor("#ffffff"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(cx + 2, cy + 7, cx + 5, cy + 10)
            painter.drawLine(cx + 5, cy + 10, cx + 11, cy + 3)
        else:
            painter.fillRect(box, QColor("#ffffff"))
            painter.setPen(QPen(QColor("#9ca3af"), 1))
            painter.drawRect(box)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if not (index.flags() & Qt.ItemIsEditable):
            return False
        if event.type() == QEvent.MouseButtonRelease:
            model.setData(index, not (index.data(Qt.UserRole) is True), Qt.UserRole)
            return True
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Space, Qt.Key_Return):
            model.setData(index, not (index.data(Qt.UserRole) is True), Qt.UserRole)
            return True
        return False

    def createEditor(self, parent, option, index):
        return None


_RCPT_COLORS = {
    "received": ("#dcfce7", "#16a34a"),
    "pending":  ("#fff7ed", "#ea580c"),
    "missing":  ("#fef2f2", "#dc2626"),
}
_RCPT_LABEL = {"received": "Received", "pending": "Pending", "missing": "Missing"}
_RECEIPT_OPTS = ["Pending", "Received", "Missing"]


class _ReceiptDelegate(QStyledItemDelegate):
    """Colored badge painter + QComboBox editor for the Receipt column."""

    def paint(self, painter, option, index) -> None:
        status = (index.data() or "").strip().lower()
        if not status:
            super().paint(painter, option, index)
            return
        label  = _RCPT_LABEL.get(status, status.capitalize())
        bg, fg = _RCPT_COLORS.get(status, ("#f3f4f6", "#6b7280"))
        rect   = option.rect.adjusted(4, 4, -4, -4)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(bg))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, rect.height() // 2, rect.height() // 2)
        painter.setPen(QColor(fg))
        f = painter.font()
        f.setPointSize(9)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(rect, Qt.AlignCenter, label)
        painter.restore()

    def createEditor(self, parent, option, index):
        ed = QComboBox(parent)
        ed.addItems(_RECEIPT_OPTS)
        return ed

    def setEditorData(self, editor, index):
        val = (index.data() or "pending").strip().lower()
        idx = {"pending": 0, "received": 1, "missing": 2}.get(val, 0)
        editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText().lower())

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


# ---------------------------------------------------------------------------
# Key event filter — captures Tab before Qt's focus-chain system can steal it
# ---------------------------------------------------------------------------

class _TableKeyFilter(QObject):
    def __init__(self, handler):
        super().__init__()
        self._handler = handler

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.KeyPress:
            self._handler(event)
            return True
        return False


# ---------------------------------------------------------------------------
# DailyRegister
# ---------------------------------------------------------------------------

class DailyRegister(QWidget):
    """Unified daily expense register (replaces ExcelGrid + TransactionsTable)."""

    rows_saved = Signal(int)

    def __init__(self, user: User, categories: List[Category], parent=None):
        super().__init__(parent)
        self._user        = user
        self._categories  = categories
        self._current_date: date = date.today()
        self._saved_count: int   = 0
        self._saved_ids: dict    = {}   # row_index -> ObjectId
        self._check_delegate   = _CheckDelegate(self)
        self._receipt_delegate = _ReceiptDelegate(self)
        self._build_ui()
        asyncio.ensure_future(self._load_date(self._current_date))

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Table ──────────────────────────────────────────────────────
        self._table = QTableWidget(DEFAULT_EDITABLE_ROWS, len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.setStyleSheet("""
            QTableWidget {
                background: #ffffff;
                gridline-color: #e5e7eb;
                border: none;
                selection-background-color: #cde0f5;
                selection-color: #1B2B4B;
            }
            QHeaderView::section {
                background: #253A5C;
                color: #F9FAFB;
                font-weight: 600;
                font-size: 11px;
                padding: 5px 8px;
                border: none;
                border-right: 1px solid #1B2B4B;
                border-bottom: 2px solid #0077C5;
            }
            QTableWidget::item         { padding: 2px 6px; color: #111827; }
            QTableWidget::item:selected { color: #1B2B4B; font-weight: 500; }
            QTableWidget::item:hover   { background: #eaf3fb; }
            QLineEdit { color: #111827; background: #ffffff; }
        """)

        hh = self._table.horizontalHeader()
        hh.setSectionsMovable(False)
        hh.setStretchLastSection(False)
        # All columns interactive (user-draggable) except fixed checkbox cols and S/NO
        for col in range(len(HEADERS)):
            hh.setSectionResizeMode(col, QHeaderView.Interactive)
        hh.setSectionResizeMode(COL_SNO,     QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_NOTES,   QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_RECEIPT, QHeaderView.Fixed)

        self._table.setColumnWidth(COL_SNO,     38)
        self._table.setColumnWidth(COL_DATE,    110)
        self._table.setColumnWidth(COL_ITEM,    120)
        self._table.setColumnWidth(COL_DESC,    360)
        self._table.setColumnWidth(COL_TRUCK,   82)
        self._table.setColumnWidth(COL_MEMO,    130)
        self._table.setColumnWidth(COL_NOTES,   52)
        self._table.setColumnWidth(COL_TZS,     120)
        self._table.setColumnWidth(COL_RECEIPT, 60)
        self._table.setColumnWidth(COL_OWN,     90)
        self._table.setColumnWidth(COL_APR,     80)

        self._table.setSelectionMode(QAbstractItemView.ContiguousSelection)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.verticalHeader().setVisible(False)
        self._table.setTabKeyNavigation(False)

        self._table.setItemDelegateForColumn(COL_DESC,    _DescriptionDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_TRUCK,   _TruckDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_DATE,    _DateDelegate(lambda: self._current_date, self._table))
        self._table.setItemDelegateForColumn(COL_NOTES,   self._check_delegate)
        self._table.setItemDelegateForColumn(COL_RECEIPT, self._receipt_delegate)

        self._table.itemChanged.connect(self._on_item_changed)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        root.addWidget(self._table)

        # ── Footer — date navigation + totals ─────────────────────────
        footer = QWidget()
        footer.setFixedHeight(48)
        footer.setStyleSheet(
            "background: #f5f6f7;"
            "border-top: 2px solid #d1d5db;"
        )
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(10, 0, 14, 0)
        fl.setSpacing(4)

        _qstyle = QApplication.style()

        self._prev_btn = QToolButton()
        self._prev_btn.setText(" Prev")
        self._prev_btn.setIcon(_qstyle.standardIcon(QStyle.SP_ArrowBack))
        self._prev_btn.setIconSize(QSize(14, 14))
        self._prev_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._prev_btn.setStyleSheet(_NAV_BTN_STYLE)
        self._prev_btn.setToolTip("Go to previous day")
        self._prev_btn.clicked.connect(self._go_prev)

        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setDisplayFormat("dd MMM yyyy")
        self._date_edit.setFixedWidth(148)
        self._date_edit.setStyleSheet(
            "QDateEdit { border: 1px solid #d1d5db; border-radius: 5px;"
            " padding: 3px 8px; font-size: 13px; font-weight: 600; color: #111827; background: #ffffff; }"
        )
        self._date_edit.dateChanged.connect(self._on_date_changed)

        self._next_btn = QToolButton()
        self._next_btn.setText("Next ")
        self._next_btn.setIcon(_qstyle.standardIcon(QStyle.SP_ArrowForward))
        self._next_btn.setIconSize(QSize(14, 14))
        self._next_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._next_btn.setLayoutDirection(Qt.RightToLeft)
        self._next_btn.setStyleSheet(_NAV_BTN_STYLE)
        self._next_btn.setToolTip("Go to next day")
        self._next_btn.clicked.connect(self._go_next)

        self._today_btn = QToolButton()
        self._today_btn.setText(" Today")
        self._today_btn.setIcon(_qstyle.standardIcon(QStyle.SP_BrowserReload))
        self._today_btn.setIconSize(QSize(14, 14))
        self._today_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._today_btn.setStyleSheet(_NAV_BTN_STYLE)
        self._today_btn.setToolTip("Jump to today")
        self._today_btn.clicked.connect(self._go_today)

        nav_sep = QFrame()
        nav_sep.setFrameShape(QFrame.VLine)
        nav_sep.setFixedHeight(26)
        nav_sep.setStyleSheet("color: #d1d5db; margin: 0 4px;")

        self._day_label = QLabel("")
        self._day_label.setStyleSheet("color: #6b7280; font-size: 12px;")

        self._loading_label = QLabel("Loading…")
        self._loading_label.setStyleSheet("color: #9ca3af; font-size: 12px;")
        self._loading_label.hide()

        fl.addWidget(self._prev_btn)
        fl.addWidget(self._date_edit)
        fl.addWidget(self._next_btn)
        fl.addWidget(self._today_btn)
        fl.addWidget(nav_sep)
        fl.addWidget(self._day_label)
        fl.addWidget(self._loading_label)
        fl.addStretch()

        self._totals_label = QLabel("0 entries")
        self._totals_label.setStyleSheet(
            "color: #374151; font-size: 12px; font-weight: 500;"
        )
        fl.addWidget(self._totals_label)

        root.addWidget(footer)

        # Init blank rows
        self._init_editable_rows(0, DEFAULT_EDITABLE_ROWS)
        self._install_key_handler()

    # ------------------------------------------------------------------
    # Date navigation
    # ------------------------------------------------------------------

    def _go_prev(self) -> None:
        self._current_date -= timedelta(days=1)
        self._sync_date_edit()
        asyncio.ensure_future(self._load_date(self._current_date))

    def _go_next(self) -> None:
        self._current_date += timedelta(days=1)
        self._sync_date_edit()
        asyncio.ensure_future(self._load_date(self._current_date))

    def _go_today(self) -> None:
        self._current_date = date.today()
        self._sync_date_edit()
        asyncio.ensure_future(self._load_date(self._current_date))

    def _on_date_changed(self, qdate: QDate) -> None:
        self._current_date = date(qdate.year(), qdate.month(), qdate.day())
        asyncio.ensure_future(self._load_date(self._current_date))

    def _sync_date_edit(self) -> None:
        self._date_edit.blockSignals(True)
        self._date_edit.setDate(
            QDate(self._current_date.year, self._current_date.month, self._current_date.day)
        )
        self._date_edit.blockSignals(False)

    def navigate_to_date(self, d: date) -> None:
        """Called by dashboard when TransactionBrowser 'Go To' is used."""
        self._current_date = d
        self._sync_date_edit()
        asyncio.ensure_future(self._load_date(d))

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------

    async def _load_date(self, d: date) -> None:
        self._loading_label.show()
        try:
            txs = await get_transactions_by_date(d)
            self._populate(txs)
            day_name = datetime(d.year, d.month, d.day).strftime("%A")
            self._day_label.setText(day_name)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load:\n{exc}")
        finally:
            self._loading_label.hide()

    def refresh(self) -> None:
        asyncio.ensure_future(self._load_date(self._current_date))

    def _populate(self, transactions: List[Transaction]) -> None:
        self._table.blockSignals(True)
        self._table.clearContents()
        self._saved_count = len(transactions)
        self._saved_ids   = {}

        total_rows = self._saved_count + DEFAULT_EDITABLE_ROWS
        self._table.setRowCount(total_rows)

        for i, tx in enumerate(transactions):
            self._fill_saved_row(i, tx)
            self._saved_ids[i] = tx._id

        self._init_editable_rows(self._saved_count, total_rows)
        self._table.blockSignals(False)
        self._renumber()
        self._update_footer(transactions)

    # ------------------------------------------------------------------
    # Row initialisation helpers
    # ------------------------------------------------------------------

    def _fill_saved_row(self, row: int, tx: Transaction) -> None:
        bg = QBrush(SAVED_BG)
        ro = Qt.ItemIsEnabled | Qt.ItemIsSelectable

        def saved_item(text: str, align=Qt.AlignVCenter | Qt.AlignLeft) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            it.setFlags(ro)
            it.setBackground(bg)
            it.setTextAlignment(align)
            return it

        # S/NO
        sno = saved_item(str(row + 1), Qt.AlignCenter)
        sno.setBackground(QBrush(SNO_BG))
        self._table.setItem(row, COL_SNO, sno)

        date_str = tx.date.strftime("%d/%m/%Y") if tx.date else ""
        self._table.setItem(row, COL_DATE,  saved_item(date_str))
        self._table.setItem(row, COL_ITEM,  saved_item(tx.item or ""))
        self._table.setItem(row, COL_DESC,  saved_item(tx.description))
        self._table.setItem(row, COL_TRUCK, saved_item(tx.truck_number or ""))
        self._table.setItem(row, COL_MEMO,  saved_item(tx.memo or ""))

        # Notes checkbox
        notes_it = QTableWidgetItem()
        notes_it.setData(Qt.UserRole, tx.notes_flag)
        notes_it.setFlags(ro)
        notes_it.setBackground(bg)
        self._table.setItem(row, COL_NOTES, notes_it)

        # TZS
        tzs_str = f"{tx.amount:,.2f}" if tx.amount else ""
        tzs_it  = saved_item(tzs_str, Qt.AlignRight | Qt.AlignVCenter)
        if tx.amount and tx.amount < 0:
            tzs_it.setForeground(NEG_COLOR)
        self._table.setItem(row, COL_TZS, tzs_it)

        # Receipt badge
        rcpt_it = saved_item(tx.receipt_status or "pending")
        self._table.setItem(row, COL_RECEIPT, rcpt_it)

        self._table.setItem(row, COL_OWN, saved_item(tx.ownership or ""))
        self._table.setItem(row, COL_APR, saved_item(tx.approver or ""))

    def _init_editable_rows(self, start: int, end: int) -> None:
        self._table.blockSignals(True)
        for row in range(start, end):
            # S/NO — blank until row is activated by data entry or Tab wrap
            sno = QTableWidgetItem("")
            sno.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            sno.setBackground(QBrush(SNO_BG))
            sno.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, COL_SNO, sno)
            # Checkbox items are created lazily in _activate_row
        self._table.blockSignals(False)

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    def _update_footer(self, transactions: Optional[List[Transaction]] = None) -> None:
        if transactions is None:
            n, tzs = 0, 0.0
        else:
            n   = len(transactions)
            tzs = sum(t.amount for t in transactions)

        amount_str = f"TZS {tzs:,.0f}" if tzs else "—"
        self._totals_label.setText(
            f"{n} entr{'y' if n == 1 else 'ies'}   ·   {amount_str}"
        )

    def _go_to_first_empty(self) -> None:
        """Scroll to and focus the first empty editable row (New button)."""
        row = self._first_empty_editable_row()
        if row >= self._table.rowCount():
            self._append_editable_rows(10)
        self._table.setCurrentCell(row, COL_DESC)
        self._table.scrollTo(self._table.model().index(row, COL_DESC))
        self._table.setFocus()

    # ------------------------------------------------------------------
    # Row numbering
    # ------------------------------------------------------------------

    def _renumber(self) -> None:
        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            it = self._table.item(row, COL_SNO)
            if not it:
                continue
            is_saved = row < self._saved_count
            is_active = it.text() != "" or self._row_has_data(row)
            if is_saved or is_active:
                it.setText(str(row + 1))
                if not is_saved:
                    if not self._table.item(row, COL_NOTES):
                        ci = QTableWidgetItem()
                        ci.setData(Qt.UserRole, False)
                        ci.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                        self._table.setItem(row, COL_NOTES, ci)
                    if not self._table.item(row, COL_RECEIPT):
                        ri = QTableWidgetItem("")
                        ri.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                        self._table.setItem(row, COL_RECEIPT, ri)
        self._table.blockSignals(False)

    # ------------------------------------------------------------------
    # Dynamic row expansion
    # ------------------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if row < self._saved_count:
            return

        col = item.column()

        # Activate the row (show S/NO + checkboxes) on first data entry
        if col not in READONLY_COLS and col not in CHECK_COLS and item.text().strip():
            self._activate_row(row)

        # Auto-fill date with the register's current date the first time
        # any data cell in this row gets a value.
        if (
            col not in (COL_DATE, COL_SNO) and col not in CHECK_COLS
            and item.text().strip()
        ):
            date_it = self._table.item(row, COL_DATE)
            if date_it is None or not date_it.text().strip():
                today_str = QDate(
                    self._current_date.year,
                    self._current_date.month,
                    self._current_date.day,
                ).toString("dd/MM/yyyy")
                self._table.blockSignals(True)
                self._table.setItem(row, COL_DATE, QTableWidgetItem(today_str))
                self._table.blockSignals(False)

        # Dynamic row expansion near the bottom
        if row >= self._table.rowCount() - 5 and item.text().strip():
            self._append_editable_rows(10)

    def _activate_row(self, row: int) -> None:
        """Make a blank editable row visible: set S/NO number and create input items."""
        if row < self._saved_count:
            return
        sno_it = self._table.item(row, COL_SNO)
        if sno_it and sno_it.text():
            return  # already active
        if sno_it:
            sno_it.setText(str(row + 1))
        self._table.blockSignals(True)
        if not self._table.item(row, COL_NOTES):
            ci = QTableWidgetItem()
            ci.setData(Qt.UserRole, False)
            ci.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._table.setItem(row, COL_NOTES, ci)
        if not self._table.item(row, COL_RECEIPT):
            ri = QTableWidgetItem("")
            ri.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._table.setItem(row, COL_RECEIPT, ri)
        self._table.blockSignals(False)

    def _append_editable_rows(self, n: int = 10) -> None:
        start = self._table.rowCount()
        self._table.setRowCount(start + n)
        self._init_editable_rows(start, start + n)
        self._renumber()

    # ------------------------------------------------------------------
    # Keyboard navigation
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        super().keyPressEvent(event)

    def _table_key_press(self, event: QKeyEvent) -> None:
        mod = event.modifiers()
        key = event.key()

        if mod == Qt.ControlModifier:
            if key == Qt.Key_C:    self._copy();                               return
            if key == Qt.Key_X:    self._cut();                                return
            if key == Qt.Key_V:    self._paste();                              return
            if key == Qt.Key_A:    self._table.selectAll();                    return
            if key == Qt.Key_D:    self._fill_down();                          return
            if key == Qt.Key_R:    self._fill_right();                         return
            if key == Qt.Key_Home: self._table.setCurrentCell(0, 0);          return
            if key == Qt.Key_End:  self._go_to_last_cell();                   return

        if mod == Qt.ShiftModifier:
            if key in (Qt.Key_Return, Qt.Key_Enter): self._step(-1, 0);      return
            if key == Qt.Key_Space:                  self._select_row();      return

        if key == Qt.Key_F2:
            it = self._table.currentItem()
            if it:
                self._table.editItem(it)
            return

        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self._clear_selected(); return

        if key == Qt.Key_Tab:
            self._commit_date_suggestion()
            self._tab_forward(); return

        if key == Qt.Key_Backtab:
            self._step(0, -1, skip=CHECK_COLS | READONLY_COLS); return

        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._commit_date_suggestion()
            self._step(+1, 0); return

        QTableWidget.keyPressEvent(self._table, event)

    def _tab_forward(self) -> None:
        """Advance Tab: skip check/readonly cols, wrap to next row at last column."""
        row, col = self._table.currentRow(), self._table.currentColumn()
        skip = CHECK_COLS | READONLY_COLS
        next_col = col + 1
        while next_col < self._table.columnCount() and next_col in skip:
            next_col += 1
        if next_col >= self._table.columnCount():
            next_row = row + 1
            if next_row >= self._table.rowCount():
                self._append_editable_rows(10)
            first_col = 0
            while first_col < self._table.columnCount() and first_col in skip:
                first_col += 1
            self._activate_row(next_row)
            self._table.setCurrentCell(next_row, first_col)
        else:
            self._table.setCurrentCell(row, next_col)

    def _install_key_handler(self) -> None:
        self._key_filter = _TableKeyFilter(self._table_key_press)
        self._table.installEventFilter(self._key_filter)

    def _commit_date_suggestion(self) -> None:
        """If the focused cell is an empty Date cell, write the register date into it."""
        row, col = self._table.currentRow(), self._table.currentColumn()
        if col != COL_DATE or row < self._saved_count:
            return
        it = self._table.item(row, COL_DATE)
        if it is not None and it.text().strip():
            return
        cur = self._current_date
        today_str = QDate(cur.year, cur.month, cur.day).toString("dd/MM/yyyy")
        new_it = QTableWidgetItem(today_str)
        new_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        self._table.blockSignals(True)
        self._table.setItem(row, COL_DATE, new_it)
        self._table.blockSignals(False)
        self._activate_row(row)

    def _step(self, dr: int, dc: int, skip: set = None) -> None:
        row, col = self._table.currentRow(), self._table.currentColumn()
        new_col, new_row = col + dc, row + dr
        if skip and dc != 0:
            while 0 <= new_col < self._table.columnCount():
                if new_col not in skip:
                    break
                new_col += dc
        new_row = max(0, min(new_row, self._table.rowCount() - 1))
        new_col = max(0, min(new_col, self._table.columnCount() - 1))
        self._table.setCurrentCell(new_row, new_col)

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------

    def _copy(self) -> None:
        items = self._table.selectedItems()
        if not items:
            return
        rows = sorted(set(it.row() for it in items))
        cols = sorted(set(it.column() for it in items))
        cell_map = {(it.row(), it.column()): it for it in items}
        lines = []
        for row in rows:
            row_cells = []
            for col in cols:
                it = cell_map.get((row, col))
                if it is None:
                    row_cells.append("")
                elif col in CHECK_COLS:
                    row_cells.append("1" if it.data(Qt.UserRole) else "0")
                else:
                    row_cells.append(it.text())
            lines.append("\t".join(row_cells))
        QApplication.clipboard().setText("\n".join(lines))

    def _cut(self) -> None:
        self._copy()
        self._clear_selected()

    def _paste(self) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return
        # Anchor at top-left of current selection (Excel behaviour)
        sel = self._table.selectedItems()
        if sel:
            start_row = max(min(it.row() for it in sel), self._saved_count)
            start_col = min(it.column() for it in sel)
        else:
            start_row = max(self._table.currentRow(), self._saved_count)
            start_col = self._table.currentColumn()
        self._table.blockSignals(True)
        for r, line in enumerate(text.splitlines()):
            for c, cell in enumerate(line.split("\t")):
                row = start_row + r
                col = start_col + c
                if row >= self._table.rowCount():
                    self._append_editable_rows(20)
                if col >= self._table.columnCount() or col in READONLY_COLS:
                    continue
                if row < self._saved_count:
                    continue
                if col in CHECK_COLS:
                    it = self._table.item(row, col) or QTableWidgetItem()
                    it.setData(Qt.UserRole, cell.strip() in ("1", "true", "True", "YES"))
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    self._table.setItem(row, col, it)
                elif col == COL_RECEIPT:
                    _rcpt_map = {
                        "received": "received", "1": "received", "yes": "received",
                        "missing": "missing",
                        "pending": "pending", "0": "pending",
                    }
                    norm = _rcpt_map.get(cell.strip().lower(), "pending")
                    it = self._table.item(row, col) or QTableWidgetItem()
                    it.setText(norm)
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    self._table.setItem(row, col, it)
                else:
                    self._table.setItem(row, col, QTableWidgetItem(cell.strip()))
        self._table.blockSignals(False)
        self._renumber()

    def _clear_selected(self) -> None:
        self._table.blockSignals(True)
        for item in self._table.selectedItems():
            row = item.row()
            col = item.column()
            if row < self._saved_count or col in READONLY_COLS:
                continue
            if col in CHECK_COLS:
                item.setData(Qt.UserRole, False)
            else:
                item.setText("")
        self._table.blockSignals(False)

    def _fill_down(self) -> None:
        """Ctrl+D: copy the top row of the selection into all rows below it."""
        items = self._table.selectedItems()
        if not items:
            return
        rows = sorted(set(it.row() for it in items))
        cols = sorted(set(it.column() for it in items))
        if len(rows) < 2:
            return
        source_row = rows[0]
        cell_map = {(it.row(), it.column()): it for it in items}
        self._table.blockSignals(True)
        for col in cols:
            if col in READONLY_COLS:
                continue
            src = cell_map.get((source_row, col))
            if src is None:
                continue
            for row in rows[1:]:
                if row < self._saved_count:
                    continue
                if col in CHECK_COLS:
                    it = self._table.item(row, col) or QTableWidgetItem()
                    it.setData(Qt.UserRole, src.data(Qt.UserRole))
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    self._table.setItem(row, col, it)
                else:
                    self._table.setItem(row, col, QTableWidgetItem(src.text()))
        self._table.blockSignals(False)
        self._renumber()

    def _fill_right(self) -> None:
        """Ctrl+R: copy the leftmost column of the selection into all cols to its right."""
        items = self._table.selectedItems()
        if not items:
            return
        rows = sorted(set(it.row() for it in items))
        cols = sorted(set(it.column() for it in items))
        if len(cols) < 2:
            return
        source_col = cols[0]
        cell_map = {(it.row(), it.column()): it for it in items}
        self._table.blockSignals(True)
        for row in rows:
            if row < self._saved_count:
                continue
            src = cell_map.get((row, source_col))
            if src is None:
                continue
            for col in cols[1:]:
                if col in READONLY_COLS or col in CHECK_COLS:
                    continue
                self._table.setItem(row, col, QTableWidgetItem(src.text()))
        self._table.blockSignals(False)
        self._renumber()

    def _go_to_last_cell(self) -> None:
        """Ctrl+End: jump to the last cell that contains data."""
        last_row, last_col = 0, 0
        for row in range(self._table.rowCount()):
            for col in range(1, self._table.columnCount()):
                it = self._table.item(row, col)
                if it and it.text().strip():
                    last_row = max(last_row, row)
                    last_col = max(last_col, col)
        self._table.setCurrentCell(last_row, last_col)
        self._table.scrollTo(self._table.model().index(last_row, last_col))

    def _select_row(self) -> None:
        """Shift+Space: select the entire current row."""
        self._table.selectRow(self._table.currentRow())

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        menu = QMenu(self._table)
        if 0 <= row < self._saved_count:
            act = menu.addAction("Delete Saved Entry")
            act.triggered.connect(lambda: self._delete_saved_row(row))
        else:
            menu.addAction("Copy",  self._copy)
            menu.addAction("Cut",   self._cut)
            menu.addAction("Paste", self._paste)
            menu.addSeparator()
            menu.addAction("Insert Row Above",       self._insert_above)
            menu.addAction("Insert Row Below",       self._insert_below)
            menu.addAction("Delete Selected Row(s)", self._delete_rows)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _insert_above(self) -> None:
        row = max(self._table.currentRow(), self._saved_count)
        self._table.insertRow(row)
        self._init_editable_rows(row, row + 1)
        self._renumber()

    def _insert_below(self) -> None:
        row = max(self._table.currentRow() + 1, self._saved_count)
        self._table.insertRow(row)
        self._init_editable_rows(row, row + 1)
        self._renumber()

    def _delete_rows(self) -> None:
        rows = sorted(
            {i.row() for i in self._table.selectedIndexes()
             if i.row() >= self._saved_count},
            reverse=True,
        )
        for row in rows:
            self._table.removeRow(row)
        min_rows = self._saved_count + DEFAULT_EDITABLE_ROWS
        if self._table.rowCount() < min_rows:
            start = self._table.rowCount()
            self._table.setRowCount(min_rows)
            self._init_editable_rows(start, min_rows)
        self._renumber()

    # ------------------------------------------------------------------
    # Delete saved row
    # ------------------------------------------------------------------

    def _delete_saved_row(self, row: int) -> None:
        tx_id = self._saved_ids.get(row)
        if not tx_id:
            return
        it = self._table.item(row, COL_DESC)
        desc = it.text() if it else "?"
        if (
            QMessageBox.question(
                self, "Delete Entry",
                f'Delete saved transaction:\n"{desc}"?',
                QMessageBox.Yes | QMessageBox.No,
            )
            == QMessageBox.Yes
        ):
            asyncio.ensure_future(self._do_delete_saved(tx_id))

    async def _do_delete_saved(self, tx_id) -> None:
        try:
            await delete_transaction(tx_id)
            await self._load_date(self._current_date)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to delete:\n{exc}")

    # ------------------------------------------------------------------
    # Import from Excel / CSV
    # ------------------------------------------------------------------

    def import_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import from Excel or CSV", "",
            "Spreadsheets (*.xlsx *.xls *.csv);;All files (*)",
        )
        if path:
            asyncio.ensure_future(self._do_import(path))

    async def _do_import(self, path: str) -> None:
        try:
            rows = await asyncio.get_event_loop().run_in_executor(
                None, _read_spreadsheet, path
            )
            self._load_rows(rows)
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", f"Could not read file:\n{exc}")

    def _load_rows(self, file_rows: List[List]) -> None:
        if not file_rows:
            return
        data = file_rows[1:] if _is_header(file_rows[0]) else file_rows

        FILE_MAP = {
            COL_DATE:    1,
            COL_DESC:    3,
            COL_TRUCK:   4,
            COL_MEMO:    9,
            COL_NOTES:   10,
            COL_TZS:     11,
            COL_RECEIPT: 13,
            COL_OWN:     14,
            COL_APR:     15,
        }

        start = self._first_empty_editable_row()
        self._table.blockSignals(True)
        for r, row_data in enumerate(data):
            target = start + r
            if target >= self._table.rowCount():
                self._append_editable_rows(20)
            if target < self._saved_count:
                continue

            for grid_col, file_col in FILE_MAP.items():
                if file_col >= len(row_data):
                    continue
                raw = str(row_data[file_col]).strip() if row_data[file_col] is not None else ""

                if grid_col in CHECK_COLS:
                    it = self._table.item(target, grid_col) or QTableWidgetItem()
                    it.setData(Qt.UserRole, bool(raw) and raw != "None")
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    self._table.setItem(target, grid_col, it)
                elif grid_col == COL_RECEIPT:
                    _rcpt_map = {
                        "received": "received", "1": "received", "yes": "received",
                        "missing": "missing",
                    }
                    norm = _rcpt_map.get(raw.lower(), "pending")
                    it = self._table.item(target, grid_col) or QTableWidgetItem()
                    it.setText(norm)
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    self._table.setItem(target, grid_col, it)
                elif grid_col == COL_DATE:
                    try:
                        from datetime import datetime as _dt
                        if isinstance(row_data[file_col], _dt):
                            formatted = row_data[file_col].strftime("%d/%m/%Y")
                        else:
                            formatted = raw
                    except Exception:
                        formatted = raw
                    self._table.setItem(target, grid_col, QTableWidgetItem(formatted))
                else:
                    if raw and raw != "None":
                        self._table.setItem(target, grid_col, QTableWidgetItem(raw))

        self._table.blockSignals(False)
        self._renumber()

    def _first_empty_editable_row(self) -> int:
        last = self._saved_count - 1
        for row in range(self._saved_count, self._table.rowCount()):
            if self._row_has_data(row):
                last = row
        return last + 1

    # ------------------------------------------------------------------
    # Save to MongoDB
    # ------------------------------------------------------------------

    def save_rows(self) -> None:
        asyncio.ensure_future(self._do_save())

    async def _do_save(self) -> None:
        saved, errors = 0, []

        for row in range(self._saved_count, self._table.rowCount()):
            if not self._row_has_data(row):
                continue

            def txt(col: int) -> str:
                it = self._table.item(row, col)
                return it.text().strip() if it else ""

            def checked(col: int) -> bool:
                it = self._table.item(row, col)
                return it.data(Qt.UserRole) is True if it else False

            description = txt(COL_DESC)
            if not description:
                continue

            try:
                date_str = txt(COL_DATE)
                try:
                    tx_date = datetime.strptime(date_str, "%d/%m/%Y")
                except ValueError:
                    tx_date = datetime(
                        self._current_date.year,
                        self._current_date.month,
                        self._current_date.day,
                    )

                def parse_num(s: str) -> float:
                    return float(s.replace(",", "")) if s else 0.0

                amount = parse_num(txt(COL_TZS))

                rcpt_raw = txt(COL_RECEIPT).lower()
                rcpt_status = rcpt_raw if rcpt_raw in ("pending", "received", "missing") else "pending"

                tx = Transaction(
                    date=tx_date,
                    description=description,
                    item=txt(COL_ITEM),
                    truck_number=txt(COL_TRUCK).upper(),
                    amount=amount,
                    currency="TZS",
                    memo=txt(COL_MEMO),
                    receipt_status=rcpt_status,
                    notes_flag=checked(COL_NOTES),
                    ownership=txt(COL_OWN),
                    approver=txt(COL_APR),
                    cashier_id=self._user._id,
                )
                await save_transaction(tx)
                saved += 1
            except Exception as exc:
                errors.append(f"Row {row + 1}: {exc}")

        if errors:
            QMessageBox.warning(
                self, "Save — partial errors",
                f"{saved} saved.\n\nErrors:\n" + "\n".join(errors),
            )
        elif saved == 0:
            QMessageBox.information(self, "Nothing to save", "No new rows with data found.")
            return
        else:
            # Silently reload — no popup for clean saves
            pass

        self.rows_saved.emit(saved)
        await self._load_date(self._current_date)

    def _row_has_data(self, row: int) -> bool:
        for col in range(self._table.columnCount()):
            if col in _DATA_SKIP_COLS:
                continue
            it = self._table.item(row, col)
            if it and it.text().strip():
                return True
        return False

    # ------------------------------------------------------------------
    # Public action entry-points (called by CashierDashboard toolbar)
    # ------------------------------------------------------------------

    def go_to_new_row(self) -> None:
        self._go_to_first_empty()

    def delete_rows(self) -> None:
        self._delete_rows()

    # ------------------------------------------------------------------
    # Category update
    # ------------------------------------------------------------------

    def update_categories(self, categories: List[Category]) -> None:
        self._categories = categories


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _read_spreadsheet(path: str) -> List[List]:
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            return [row for row in csv.reader(f)]
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    result = [list(row) for row in ws.iter_rows(values_only=True)]
    wb.close()
    return result


def _is_header(row: List) -> bool:
    numeric = sum(
        1 for c in row
        if c is not None
        and str(c).replace(".", "").replace(",", "").replace("-", "").strip().isdigit()
    )
    return numeric == 0


# Backward-compat alias
ExcelGrid = DailyRegister
