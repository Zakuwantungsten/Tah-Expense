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
from datetime import datetime, date
from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QToolButton, QLabel,
    QTableWidget, QTableWidgetItem, QApplication,
    QAbstractItemDelegate, QStyledItemDelegate, QStyleOptionViewItem, QMenu, QFileDialog,
    QMessageBox, QAbstractItemView, QHeaderView, QDateEdit, QLineEdit,
    QStyle, QComboBox, QDialog,
)
from PySide6.QtCore import Qt, Signal, QDate, QEvent, QRect, QSize, QObject, QTimer
from PySide6.QtGui import QAction, QKeyEvent, QColor, QBrush, QFont, QPen, QPainter

from tahmeed.models.category import Category
from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.services.truck_service import search_fleet, get_fleet_numbers
from tahmeed.services.cashier_service import (
    get_transactions_by_date, save_transaction, delete_transaction,
    search_descriptions,
)
from tahmeed.services.category_service import (
    create_category, get_all_categories, item_key,
)
from tahmeed.services.subtable_service import get_subtables
from tahmeed.services.settings_service import get_setting
from tahmeed.signals import app_signals
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit
from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit, accept_completion

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
    "Memo", "Ref_Float", "TZS", "Receipt", "Ownership", "APR BY",
]

CHECK_COLS       = {COL_NOTES}
READONLY_COLS    = {COL_SNO}
_DATA_SKIP_COLS  = READONLY_COLS | {COL_NOTES, COL_RECEIPT}
# Columns that should NOT be auto-uppercased (keys/dates/checkboxes/readonly)
_UPPER_SKIP_COLS = READONLY_COLS | CHECK_COLS | {COL_RECEIPT, COL_DATE}
DEFAULT_EDITABLE_ROWS = 20

# Colors
SAVED_BG  = QColor("#fff8f0")
NEW_BG    = QColor("#ffffff")
EMPTY_BG  = QColor("#fafafa")
NEG_COLOR = QColor("#dc2626")
SNO_BG    = QColor("#f1f5f9")

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
# Delegate helpers
# ---------------------------------------------------------------------------

def _accept_editor_completion(editor) -> None:
    """If the editor has an autocomplete popup visible, accept the highlighted item."""
    completer = getattr(editor, '_completer', None)
    if completer:
        accept_completion(editor, completer)


def _upper_text(col: int, text: str) -> str:
    """Return text uppercased unless the column stores structured/non-text data."""
    return text.upper() if col not in _UPPER_SKIP_COLS else text


# ---------------------------------------------------------------------------
# Delegates
# ---------------------------------------------------------------------------

class _ExcelCellDelegate(QStyledItemDelegate):
    """
    Base delegate implementing Excel's selection visual model:
      - current + selected  → white background + 2 px QB-blue border
      - selected (not current) → #cde0f5 fill
      - normal              → item's own background (saved warm, new white)
    All specialised delegates inherit from this.
    """
    _ACTIVE_PEN  = QColor("#0077C5")
    _SELECT_FILL = QColor("#cde0f5")

    def _is_current(self, index) -> bool:
        t = self.parent()
        return t is not None and t.currentIndex() == index

    def _paint_bg(self, painter: QPainter, option, index) -> None:
        """Fill cell background only — no border."""
        is_sel = bool(option.state & QStyle.State_Selected)
        is_cur = self._is_current(index)
        if is_cur and is_sel:
            painter.fillRect(option.rect, QColor("#ffffff"))
        elif is_sel:
            painter.fillRect(option.rect, self._SELECT_FILL)
        else:
            bg = option.backgroundBrush
            if bg.style() != Qt.NoBrush:
                painter.fillRect(option.rect, bg)
            # else: table stylesheet background (#ffffff) shows through

    def _draw_active_border(self, painter: QPainter, option, index) -> None:
        """Draw the thick QB-blue border when this is the active/current cell."""
        if self._is_current(index) and bool(option.state & QStyle.State_Selected):
            pen = QPen(self._ACTIVE_PEN, 2)
            pen.setJoinStyle(Qt.MiterJoin)
            painter.save()
            painter.setPen(pen)
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
            painter.restore()

    def _stripped_option(self, option) -> QStyleOptionViewItem:
        """Copy of option with selection/focus flags removed so Qt won't repaint bg."""
        opt = QStyleOptionViewItem(option)
        opt.state &= ~(QStyle.State_Selected | QStyle.State_HasFocus)
        return opt

    def eventFilter(self, obj, event) -> bool:
        """Intercept Tab/Enter in editors: accept autocomplete, commit, navigate."""
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Tab:
                _accept_editor_completion(obj)
                self.commitData.emit(obj)
                self.closeEditor.emit(obj, QAbstractItemDelegate.NoHint)
                table = self.parent()
                if table is not None:
                    table.setFocus()  # reclaim focus before the timer fires
                    reg = table.parent()
                    if hasattr(reg, '_tab_forward'):
                        QTimer.singleShot(0, reg._tab_forward)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                _accept_editor_completion(obj)
                self.commitData.emit(obj)
                self.closeEditor.emit(obj, QAbstractItemDelegate.NoHint)
                table = self.parent()
                if table is not None:
                    table.setFocus()  # reclaim focus before the timer fires
                    reg = table.parent()
                    if hasattr(reg, '_step'):
                        QTimer.singleShot(0, lambda: reg._step(+1, 0))
                return True
        return super().eventFilter(obj, event)

    def paint(self, painter: QPainter, option, index) -> None:
        self.initStyleOption(option, index)
        self._paint_bg(painter, option, index)
        super().paint(painter, self._stripped_option(option), index)
        self._draw_active_border(painter, option, index)


class _DescriptionDelegate(_ExcelCellDelegate):
    """Description editor that adapts to the row's chosen Item.

    - If that item has ``lock_description`` set *and* has sub-items, the editor
      is a restricted popup limited to those sub-item names (the cashier can
      only pick one of them).
    - Otherwise it falls back to the free-text editor with history autocomplete.
    """

    def __init__(self, cat_getter, subs_getter, parent=None):
        super().__init__(parent)
        self._cat_getter = cat_getter      # name -> Category | None
        self._subs_getter = subs_getter    # name -> list[str]

    def createEditor(self, parent, option, index):
        item_name = (index.sibling(index.row(), COL_ITEM).data() or "").strip()
        cat = self._cat_getter(item_name) if item_name else None
        if cat is not None and getattr(cat, "lock_description", False):
            subs = self._subs_getter(item_name)
            if subs:
                ed = CompleterLineEdit(subs, parent=parent)
                ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
                return ed
        ed = TruckLineEdit(fetch_fn=search_descriptions, parent=parent)
        ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
        return ed

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        text = editor.text().strip()
        # Snap to the canonical sub-item name when the editor is a restricted list.
        if isinstance(editor, CompleterLineEdit):
            text = editor.canonical(text) or text
        model.setData(index, text)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _TruckDelegate(_ExcelCellDelegate):
    def createEditor(self, parent, option, index):
        ed = TruckLineEdit(fetch_fn=search_fleet, parent=parent)
        ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
        return ed

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text().strip().upper())

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _DateDelegate(_ExcelCellDelegate):
    def __init__(self, get_current_date, parent=None):
        super().__init__(parent)
        self._get_current_date = get_current_date

    def paint(self, painter, option, index) -> None:
        self.initStyleOption(option, index)
        value  = (index.data() or "").strip()
        is_sel = bool(option.state & QStyle.State_Selected)

        self._paint_bg(painter, option, index)

        if not value and is_sel:
            # Empty selected date cell — overlay suggestion text in QB blue
            cur = self._get_current_date()
            suggestion = QDate(cur.year, cur.month, cur.day).toString("dd/MM/yyyy")
            painter.save()
            painter.setPen(QColor("#0077C5"))
            painter.drawText(
                option.rect.adjusted(6, 0, -22, 0),
                Qt.AlignVCenter | Qt.AlignLeft,
                suggestion,
            )
            painter.restore()
        else:
            QStyledItemDelegate.paint(self, painter, self._stripped_option(option), index)

        # Calendar icon — only when cell has data or is selected
        if value or is_sel:
            sp_icon = QApplication.style().standardIcon(QStyle.SP_FileDialogDetailedView)
            pix = sp_icon.pixmap(QSize(14, 14))
            ix = option.rect.right() - 18
            iy = option.rect.top() + (option.rect.height() - 14) // 2
            painter.drawPixmap(ix, iy, pix)

        self._draw_active_border(painter, option, index)

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

    def setModelData(self, editor, model, index):
        model.setData(index, editor.date().toString("dd/MM/yyyy"))

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _RefFloatDelegate(_ExcelCellDelegate):
    """Blank or 'Refund to Float' orange badge; toggles via click/Space/Return."""

    def paint(self, painter, option, index) -> None:
        value = index.data(Qt.UserRole)
        if value is None:
            return  # uninitialised blank row

        self.initStyleOption(option, index)
        self._paint_bg(painter, option, index)

        if value is True:
            rect = option.rect.adjusted(4, 5, -4, -5)
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor("#FFF7ED"))
            painter.setPen(QPen(QColor("#EA580C"), 1))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor("#EA580C"))
            f = painter.font()
            f.setPointSize(8)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(rect, Qt.AlignCenter, "Refund to Float")
            painter.restore()

        self._draw_active_border(painter, option, index)

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
    "received":   ("#dcfce7", "#16a34a"),
    "pending":    ("#fff7ed", "#ea580c"),
    "missing":    ("#fef2f2", "#dc2626"),
    "no_receipt": ("#f3f4f6", "#6b7280"),
}
_RCPT_LABEL = {
    "received": "Received", "pending": "Pending",
    "missing": "Missing", "no_receipt": "No Receipt",
}
_RECEIPT_OPTS = ["Pending", "Received", "Missing", "No Receipt"]
# Display label (lowercased) -> stored status key
_RCPT_OPT_KEY = {
    "pending": "pending", "received": "received",
    "missing": "missing", "no receipt": "no_receipt",
}
# Any incoming text (paste / import) -> stored status key
_RCPT_NORM = {
    "received": "received", "1": "received", "yes": "received",
    "missing": "missing",
    "pending": "pending", "0": "pending",
    "no receipt": "no_receipt", "no_receipt": "no_receipt", "none": "no_receipt",
}
_VALID_RCPT = {"pending", "received", "missing", "no_receipt"}


class _ReceiptDelegate(_ExcelCellDelegate):
    """Colored badge painter + QComboBox editor for the Receipt column."""

    def paint(self, painter, option, index) -> None:
        self.initStyleOption(option, index)
        status = (index.data() or "").strip().lower()

        self._paint_bg(painter, option, index)

        if status:
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
        else:
            QStyledItemDelegate.paint(self, painter, self._stripped_option(option), index)

        self._draw_active_border(painter, option, index)

    def createEditor(self, parent, option, index):
        ed = CompleterLineEdit(_RECEIPT_OPTS, parent=parent)
        ed._completer.setFilterMode(Qt.MatchStartsWith)
        ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
        return ed

    def setEditorData(self, editor, index):
        val = (index.data() or "").strip().lower()
        editor.setText(_RCPT_LABEL.get(val, "") if val else "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        disp = editor.canonical(editor.text().strip()) or editor.text().strip()
        model.setData(index, _RCPT_OPT_KEY.get(disp.lower(), ""))

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _ItemDelegate(_ExcelCellDelegate):
    """Live popup of accountant-managed items for the Item column.

    Behaves like the Truck No. field: the list narrows in real time as the
    cashier types, and Tab accepts the highlighted suggestion — writing the
    *canonical* item name (so "m" + Tab gives "MILEAGE", not "mILEAGE"). The
    list is read live via ``items_getter`` so newly-created items appear at once.
    Whether unknown entries are allowed is enforced at the grid level.
    """

    def __init__(self, items_getter, parent=None):
        super().__init__(parent)
        self._items_getter = items_getter

    def createEditor(self, parent, option, index):
        ed = CompleterLineEdit(self._items_getter() or [], parent=parent)
        ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
        return ed

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        text = editor.text().strip()
        # Snap typed text to the canonical item name when it matches one.
        model.setData(index, editor.canonical(text) or text)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _CurrencyLineEdit(QLineEdit):
    """QLineEdit that inserts comma thousands-separators in real time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fmt = False
        self.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, text: str) -> None:
        if self._fmt:
            return
        cursor = self.cursorPosition()
        nc_before = sum(1 for ch in text[:cursor] if ch != ",")

        neg = text.replace(",", "").startswith("-")
        raw = text.replace(",", "").lstrip("-")
        clean, dot = "", False
        for ch in raw:
            if ch == "." and not dot:
                clean += ch; dot = True
            elif ch.isdigit():
                clean += ch

        if "." in clean:
            int_s, dec_s = clean.split(".", 1)
            int_fmt = f"{int(int_s):,}" if int_s else ""
            new_text = ("-" if neg else "") + int_fmt + "." + dec_s
        else:
            new_text = ("-" if neg else "") + (f"{int(clean):,}" if clean else "")

        if new_text == text:
            return

        self._fmt = True
        self.setText(new_text)
        nc, pos = 0, len(new_text)
        for i, ch in enumerate(new_text):
            if nc >= nc_before:
                pos = i
                break
            if ch != ",":
                nc += 1
        self.setCursorPosition(pos)
        self._fmt = False

    def value_text(self) -> str:
        return self.text().replace(",", "")


class _TZSDelegate(_ExcelCellDelegate):
    """Right-aligned currency editor with live comma-thousands formatting."""

    def createEditor(self, parent, option, index):
        ed = _CurrencyLineEdit(parent)
        ed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
        return ed

    def setEditorData(self, editor, index):
        editor.blockSignals(True)
        editor.setText((index.data() or "").strip().replace(",", ""))
        editor.blockSignals(False)
        editor.selectAll()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text())

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
# Column filter header
# ---------------------------------------------------------------------------

_FILTER_COLS = set(range(len(HEADERS))) - {COL_SNO}


class _FilterMenu(QMenu):
    """QMenu that stays open when the user clicks checkable (filter) items,
    so they can tick multiple values before closing."""

    def mouseReleaseEvent(self, event):
        action = self.activeAction()
        if action and action.isCheckable():
            action.setChecked(not action.isChecked())
            # Do NOT call super — that would close the menu
        else:
            super().mouseReleaseEvent(event)


class _FilterHeaderView(QHeaderView):
    """Horizontal header that paints a ▾ chevron on filterable columns and
    opens a multi-select filter menu on click in the chevron area."""

    filter_changed = Signal(int, set)   # (col_index, accepted_values); empty = cleared

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._active: dict = {}   # col -> set of accepted values

    # ── Painting ──────────────────────────────────────────────────────────
    def paintSection(self, painter, rect, logical_index):
        super().paintSection(painter, rect, logical_index)
        if logical_index not in _FILTER_COLS or rect.width() < 28:
            return
        painter.save()
        is_active = bool(self._active.get(logical_index))
        painter.setPen(QColor("#EA580C") if is_active else QColor("#94A3B8"))
        f = painter.font()
        f.setPointSize(7)
        painter.setFont(f)
        painter.drawText(
            QRect(rect.right() - 15, rect.top(), 13, rect.height()),
            Qt.AlignVCenter | Qt.AlignHCenter,
            "▾",
        )
        painter.restore()

    # ── Click handling ────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        col = self.logicalIndexAt(event.pos())
        if col in _FILTER_COLS:
            x      = event.pos().x()
            col_x  = self.sectionViewportPosition(col)
            col_w  = self.sectionSize(col)
            if x >= col_x + col_w - 20:
                self._open_menu(col, event.globalPosition().toPoint())
                return
        super().mousePressEvent(event)

    def _open_menu(self, col: int, global_pos) -> None:
        table = self.parent()
        if not isinstance(table, QTableWidget):
            return

        # Collect unique non-empty values visible in this column
        values: set = set()
        for row in range(table.rowCount()):
            it = table.item(row, col)
            if not it:
                continue
            if col == COL_NOTES:
                if it.data(Qt.UserRole) is True:
                    values.add("Refund to Float")
            else:
                v = it.text().strip()
                if v:
                    values.add(v)

        if not values:
            return

        current = self._active.get(col, set())

        menu = _FilterMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #ffffff;
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 4px 0;
                min-width: 190px;
            }
            QMenu::item {
                padding: 5px 16px 5px 30px;
                font-size: 12px;
                color: #111827;
            }
            QMenu::item:selected { background: #EFF6FF; color: #0077C5; }
            QMenu::item:checked  { font-weight: 600; }
            QMenu::separator     { height: 1px; background: #E5E7EB; margin: 4px 0; }
        """)

        clear_act = menu.addAction("Show All")
        clear_act.setEnabled(bool(current))
        menu.addSeparator()

        for val in sorted(values, key=lambda v: v.lower()):
            act = QAction(val, menu)
            act.setCheckable(True)
            act.setChecked(val in current)
            menu.addAction(act)

        chosen = menu.exec(global_pos)

        if chosen is clear_act:
            new_filter: set = set()
        else:
            new_filter = {
                act.text() for act in menu.actions()
                if act.isCheckable() and act.isChecked()
            }

        if new_filter:
            self._active[col] = new_filter
        else:
            self._active.pop(col, None)

        self.filter_changed.emit(col, new_filter)
        self.viewport().update()


# ---------------------------------------------------------------------------
# DailyRegister
# ---------------------------------------------------------------------------

class DailyRegister(QWidget):
    """Unified daily expense register (replaces ExcelGrid + TransactionsTable)."""

    rows_saved    = Signal(int)
    stats_updated = Signal(int, float, float)  # (n_entries, total_tzs, refund_total)

    def __init__(self, user: User, categories: List[Category], parent=None):
        super().__init__(parent)
        self._user        = user
        self._categories  = categories
        self._cat_by_name: dict = {c.name.lower(): c for c in categories}
        self._locked_subitems: dict = {}   # item name (lower) -> [sub-item names]
        self._restrict_items: bool = False
        self._restrict_trucks: bool = False
        self._fleet_numbers: set = set()   # uppercased valid truck/trailer numbers
        self._current_date: date = date.today()
        self._saved_count: int   = 0
        self._saved_ids: dict    = {}   # row_index -> ObjectId
        self._retry_queue: set   = set()
        self._retry_timer        = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._on_retry_timer)
        self._col_filters: dict  = {}   # col -> set of accepted values
        self._search_text: str   = ""
        self._build_ui()
        asyncio.ensure_future(self._load_date(self._current_date))
        asyncio.ensure_future(self._load_restrict_setting())
        asyncio.ensure_future(self._load_locked_subitems())
        asyncio.ensure_future(self._load_fleet_numbers())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Table ──────────────────────────────────────────────────────
        self._table = QTableWidget(DEFAULT_EDITABLE_ROWS, len(HEADERS))
        _fhv = _FilterHeaderView(self._table)
        _fhv.filter_changed.connect(self._on_col_filter_changed)
        self._table.setHorizontalHeader(_fhv)
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
        hh.setSectionResizeMode(COL_SNO,   QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_NOTES, QHeaderView.Fixed)

        self._table.setColumnWidth(COL_SNO,     38)
        self._table.setColumnWidth(COL_DATE,    110)
        self._table.setColumnWidth(COL_ITEM,    120)
        self._table.setColumnWidth(COL_DESC,    360)
        self._table.setColumnWidth(COL_TRUCK,   82)
        self._table.setColumnWidth(COL_MEMO,    130)
        self._table.setColumnWidth(COL_NOTES,   96)
        self._table.setColumnWidth(COL_TZS,     120)
        self._table.setColumnWidth(COL_RECEIPT, 60)
        self._table.setColumnWidth(COL_OWN,     90)
        self._table.setColumnWidth(COL_APR,     80)

        self._table.setSelectionMode(QAbstractItemView.ContiguousSelection)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.verticalHeader().setVisible(False)
        self._table.setTabKeyNavigation(False)

        # Excel selection model on every column; per-column delegates override as needed
        self._table.setItemDelegate(_ExcelCellDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_ITEM,    _ItemDelegate(lambda: [c.name for c in self._categories], self._table))
        self._table.setItemDelegateForColumn(COL_DESC,    _DescriptionDelegate(
            cat_getter=lambda name: self._cat_by_name.get(name.lower()),
            subs_getter=lambda name: self._locked_subitems.get(name.lower(), []),
            parent=self._table,
        ))
        self._table.setItemDelegateForColumn(COL_TRUCK,   _TruckDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_DATE,    _DateDelegate(lambda: self._current_date, self._table))
        self._table.setItemDelegateForColumn(COL_NOTES,   _RefFloatDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_TZS,     _TZSDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_RECEIPT, _ReceiptDelegate(self._table))

        self._table.itemChanged.connect(self._on_item_changed)
        self._table.model().dataChanged.connect(self._on_model_data_changed)
        self._table.currentCellChanged.connect(self._on_current_cell_changed)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        root.addWidget(self._table)

        # ── Footer — totals only ───────────────────────────────────────
        footer = QWidget()
        footer.setFixedHeight(48)
        footer.setStyleSheet(
            "background: #f5f6f7;"
            "border-top: 2px solid #d1d5db;"
        )
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(10, 0, 14, 0)
        fl.setSpacing(4)

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

    def navigate_to_date(self, d: date) -> None:
        """Called by dashboard when TransactionBrowser 'Go To' is used."""
        self._current_date = d
        asyncio.ensure_future(self._load_date(d))

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------

    async def _load_date(self, d: date) -> None:
        try:
            txs = await get_transactions_by_date(d, cashier_id=self._user._id)
            self._populate(txs)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load:\n{exc}")

    def refresh(self) -> None:
        asyncio.ensure_future(self._load_date(self._current_date))
        asyncio.ensure_future(self._load_restrict_setting())

    def reload_settings(self) -> None:
        """Re-read the restrict toggles, locked sub-items and fleet list without
        touching the grid rows (so unsaved entries survive). Called on entering
        the table tab."""
        asyncio.ensure_future(self._load_restrict_setting())
        asyncio.ensure_future(self._load_locked_subitems())
        asyncio.ensure_future(self._load_fleet_numbers())

    def _populate(self, transactions: List[Transaction]) -> None:
        self._table.blockSignals(True)
        self._table.clearContents()
        self._saved_count = len(transactions)
        self._saved_ids   = {}
        self._retry_queue.clear()

        total_rows = self._saved_count + DEFAULT_EDITABLE_ROWS
        self._table.setRowCount(total_rows)

        for i, tx in enumerate(transactions):
            self._fill_saved_row(i, tx)
            self._saved_ids[i] = tx._id

        self._init_editable_rows(self._saved_count, total_rows)
        self._table.blockSignals(False)
        self._renumber()
        self._update_footer()
        self._apply_filters()

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

    def _update_footer(self) -> None:
        n, tzs, refund = 0, 0.0, 0.0
        for row in range(self._table.rowCount()):
            tzs_it = self._table.item(row, COL_TZS)
            if not tzs_it:
                continue
            raw = tzs_it.text().strip().replace(",", "")
            if not raw:
                continue
            try:
                amount = float(raw)
            except ValueError:
                continue
            n += 1
            tzs += amount
            notes_it = self._table.item(row, COL_NOTES)
            if notes_it and notes_it.data(Qt.UserRole) is True:
                refund += amount

        amount_str = f"TZS {tzs:,.0f}" if tzs else "—"
        self._totals_label.setText(
            f"{n} entr{'y' if n == 1 else 'ies'}   ·   {amount_str}"
        )
        self.stats_updated.emit(n, tzs, refund)

    # ------------------------------------------------------------------
    # Auto-save
    # ------------------------------------------------------------------

    def _build_transaction_from_row(self, row: int) -> Optional[Transaction]:
        """Read cell values for a single row and return a Transaction, or None
        if the row has no description. Raises ValueError on validation errors
        (bad item / locked description / unregistered truck) so callers can
        distinguish logical from network failures and skip retries."""
        def txt(col: int) -> str:
            it = self._table.item(row, col)
            return it.text().strip() if it else ""

        def checked(col: int) -> bool:
            it = self._table.item(row, col)
            return it.data(Qt.UserRole) is True if it else False

        description = txt(COL_DESC)
        if not description:
            return None

        date_str = txt(COL_DATE)
        try:
            tx_date = datetime.strptime(date_str, "%d/%m/%Y")
        except ValueError:
            tx_date = datetime(
                self._current_date.year,
                self._current_date.month,
                self._current_date.day,
            )

        raw_tzs = txt(COL_TZS).replace(",", "")
        amount = float(raw_tzs) if raw_tzs else 0.0

        rcpt_raw = txt(COL_RECEIPT).lower()
        rcpt_status = rcpt_raw if rcpt_raw in _VALID_RCPT else "pending"

        item_name = txt(COL_ITEM)
        cat = self._cat_by_name.get(item_name.lower()) if item_name else None
        if cat is not None:
            item_name = cat.name
        elif item_name and self._restrict_items:
            raise ValueError(f'"{item_name}" is not a known item.')

        if cat is not None and getattr(cat, "lock_description", False):
            allowed = self._locked_subitems.get(item_name.lower(), [])
            if allowed:
                match = next(
                    (a for a in allowed if a.lower() == description.lower()), None
                )
                if match is None:
                    raise ValueError(
                        f'"{description}" is not an allowed description for "{item_name}".'
                    )
                description = match

        truck_number = txt(COL_TRUCK).upper()
        if (truck_number and self._restrict_trucks
                and truck_number not in self._fleet_numbers):
            raise ValueError(f'"{truck_number}" is not a registered truck/trailer.')

        return Transaction(
            date=tx_date,
            description=description,
            item=item_name,
            category_name=item_name or None,
            truck_number=truck_number,
            amount=amount,
            currency="TZS",
            memo=txt(COL_MEMO),
            receipt_status=rcpt_status,
            notes_flag=checked(COL_NOTES),
            ownership=txt(COL_OWN),
            approver=txt(COL_APR),
            cashier_id=self._user._id,
        )

    def _freeze_row(self, row: int, tx: Transaction) -> None:
        """Convert a single editable row to read-only saved state without
        touching any other row."""
        self._table.blockSignals(True)
        self._fill_saved_row(row, tx)
        self._saved_ids[row] = tx._id
        self._table.blockSignals(False)
        # Advance _saved_count through the newly contiguous saved block
        while self._saved_count in self._saved_ids:
            self._saved_count += 1
        self._update_footer()
        self.rows_saved.emit(1)

    def _on_current_cell_changed(
        self, cur_row: int, cur_col: int, prev_row: int, prev_col: int
    ) -> None:
        if prev_row < 0 or prev_row == cur_row:
            return
        if prev_row in self._saved_ids:
            return
        asyncio.ensure_future(self._autosave_row(prev_row))

    async def _autosave_row(self, row: int) -> None:
        if row in self._saved_ids:
            return
        try:
            tx = self._build_transaction_from_row(row)
        except ValueError:
            return  # validation error — row data is logically invalid, skip retry
        if tx is None:
            return  # no description yet

        try:
            tx = await save_transaction(tx)
        except Exception:
            # DB / network failure — queue for retry, don't reload the table
            self._retry_queue.add(row)
            if not self._retry_timer.isActive():
                self._retry_timer.start(4000)
            return

        self._retry_queue.discard(row)
        self._freeze_row(row, tx)
        app_signals.transaction_saved.emit()

    def _on_retry_timer(self) -> None:
        for row in list(self._retry_queue):
            asyncio.ensure_future(self._autosave_row(row))

    # ------------------------------------------------------------------
    # Search & column filtering
    # ------------------------------------------------------------------

    def set_search(self, text: str) -> None:
        self._search_text = text.strip().lower()
        self._apply_filters()

    def _on_col_filter_changed(self, col: int, accepted: set) -> None:
        if accepted:
            self._col_filters[col] = accepted
        else:
            self._col_filters.pop(col, None)
        self._apply_filters()

    def _apply_filters(self) -> None:
        search = self._search_text
        for row in range(self._table.rowCount()):
            # Always show editable rows so new entry is never hidden
            if row >= self._saved_count:
                self._table.setRowHidden(row, False)
                continue

            # ── Search ───────────────────────────────────────────────
            if search:
                matched = False
                for col in range(self._table.columnCount()):
                    it = self._table.item(row, col)
                    if col == COL_NOTES:
                        if it and it.data(Qt.UserRole) is True and search in "refund to float":
                            matched = True
                            break
                    else:
                        if it and search in it.text().lower():
                            matched = True
                            break
                if not matched:
                    self._table.setRowHidden(row, True)
                    continue

            # ── Column filters ───────────────────────────────────────
            visible = True
            for col, accepted in self._col_filters.items():
                if not accepted:
                    continue
                it = self._table.item(row, col)
                if col == COL_NOTES:
                    val = "Refund to Float" if (it and it.data(Qt.UserRole) is True) else ""
                else:
                    val = it.text().strip() if it else ""
                if val not in accepted:
                    visible = False
                    break

            self._table.setRowHidden(row, not visible)

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

        # Uppercase all free-text cells
        if col not in _UPPER_SKIP_COLS:
            text = item.text()
            if text and text != text.upper():
                self._table.blockSignals(True)
                item.setText(text.upper())
                self._table.blockSignals(False)

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

        # Item / Description / Truck validation (canonicalise, restrict, locked lists)
        if col == COL_ITEM and item.text().strip():
            self._validate_item_cell(row, item)
        elif col == COL_DESC and item.text().strip():
            self._validate_locked_description(row, item)
        elif col == COL_TRUCK and item.text().strip():
            self._validate_truck_cell(row, item)

        # Dynamic row expansion near the bottom
        if row >= self._table.rowCount() - 5 and item.text().strip():
            self._append_editable_rows(10)

        if col == COL_TZS:
            self._update_footer()

    def _on_model_data_changed(self, top_left, bottom_right, roles=()) -> None:
        if top_left.column() == COL_NOTES and Qt.UserRole in roles:
            self._update_footer()

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
        """Advance Tab: skip readonly cols, wrap to next row at last column."""
        row, col = self._table.currentRow(), self._table.currentColumn()
        skip = READONLY_COLS
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
        self._table.setFocus()
        # Blank editable rows have no QTableWidgetItem; Qt returns ItemIsDropEnabled
        # only when there is no item, so edit() silently fails.  Create a placeholder
        # so the cell is treated as editable before we attempt to open the editor.
        idx = self._table.currentIndex()
        trow, tcol = idx.row(), idx.column()
        if trow >= self._saved_count and not self._table.item(trow, tcol):
            self._table.blockSignals(True)
            it = QTableWidgetItem("")
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._table.setItem(trow, tcol, it)
            self._table.blockSignals(False)
        self._table.edit(idx)

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

        lines = text.splitlines()

        # selectedIndexes() covers blank rows (which have no QTableWidgetItem and
        # therefore never appear in selectedItems()).
        sel_indexes = self._table.selectedIndexes()
        if sel_indexes:
            start_row = max(min(i.row() for i in sel_indexes), self._saved_count)
            start_col = min(i.column() for i in sel_indexes)
            sel_rows = sorted({i.row() for i in sel_indexes if i.row() >= self._saved_count})
            sel_cols = sorted({i.column() for i in sel_indexes})
        else:
            start_row = max(self._table.currentRow(), self._saved_count)
            start_col = self._table.currentColumn()
            sel_rows = []
            sel_cols = []

        # Single clipboard value pasted onto a multi-cell selection: fill every
        # selected editable cell with that value (Excel behaviour).
        if len(lines) == 1 and "\t" not in lines[0] and sel_rows and (
            len(sel_rows) > 1 or len(sel_cols) > 1
        ):
            cell_value = lines[0].strip()
            self._table.blockSignals(True)
            for row in sel_rows:
                for col in sel_cols:
                    if col in READONLY_COLS:
                        continue
                    if col in CHECK_COLS:
                        it = self._table.item(row, col) or QTableWidgetItem()
                        it.setData(Qt.UserRole, cell_value in ("1", "true", "True", "YES"))
                        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                        self._table.setItem(row, col, it)
                    elif col == COL_RECEIPT:
                        norm = _RCPT_NORM.get(cell_value.lower(), "pending")
                        it = self._table.item(row, col) or QTableWidgetItem()
                        it.setText(norm)
                        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                        self._table.setItem(row, col, it)
                    else:
                        self._table.setItem(row, col, QTableWidgetItem(_upper_text(col, cell_value)))
            self._table.blockSignals(False)
            self._renumber()
            return

        # Multi-row / multi-column clipboard: paste starting at anchor (TSV layout).
        self._table.blockSignals(True)
        for r, line in enumerate(lines):
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
                    norm = _RCPT_NORM.get(cell.strip().lower(), "pending")
                    it = self._table.item(row, col) or QTableWidgetItem()
                    it.setText(norm)
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    self._table.setItem(row, col, it)
                else:
                    self._table.setItem(row, col, QTableWidgetItem(_upper_text(col, cell.strip())))
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
                    self._table.setItem(row, col, QTableWidgetItem(_upper_text(col, src.text())))
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
                self._table.setItem(row, col, QTableWidgetItem(_upper_text(col, src.text())))
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
                    norm = _RCPT_NORM.get(raw.lower(), "pending")
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
                        self._table.setItem(target, grid_col, QTableWidgetItem(_upper_text(grid_col, raw)))

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
            if row in self._saved_ids:
                continue  # already auto-saved
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
                rcpt_status = rcpt_raw if rcpt_raw in _VALID_RCPT else "pending"

                item_name = txt(COL_ITEM)
                cat = self._cat_by_name.get(item_name.lower()) if item_name else None
                if cat is not None:
                    item_name = cat.name  # canonical casing
                elif item_name and self._restrict_items:
                    errors.append(f'Row {row + 1}: "{item_name}" is not a known item.')
                    continue

                # Backstop for description-lock (covers paste / fill-down that
                # skip the live editor validation).
                if cat is not None and getattr(cat, "lock_description", False):
                    allowed = self._locked_subitems.get(item_name.lower(), [])
                    if allowed:
                        match = next((a for a in allowed if a.lower() == description.lower()), None)
                        if match is None:
                            errors.append(
                                f'Row {row + 1}: "{description}" is not an allowed '
                                f'description for "{item_name}".'
                            )
                            continue
                        description = match  # canonical casing

                truck_number = txt(COL_TRUCK).upper()
                if (truck_number and self._restrict_trucks
                        and truck_number not in self._fleet_numbers):
                    errors.append(
                        f'Row {row + 1}: "{truck_number}" is not a registered truck/trailer.'
                    )
                    continue

                tx = Transaction(
                    date=tx_date,
                    description=description,
                    item=item_name,
                    # The chosen item *is* the category — keep them in sync so the
                    # item's sidebar tab (which filters on category_name) shows it.
                    category_name=item_name or None,
                    truck_number=truck_number,
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
    # Category update
    # ------------------------------------------------------------------

    def update_categories(self, categories: List[Category]) -> None:
        self._categories = categories
        self._cat_by_name = {c.name.lower(): c for c in categories}
        asyncio.ensure_future(self._load_locked_subitems())

    # ------------------------------------------------------------------
    # Settings / locked sub-item cache
    # ------------------------------------------------------------------

    async def _load_restrict_setting(self) -> None:
        try:
            self._restrict_items = bool(await get_setting("restrict_items"))
        except Exception:
            self._restrict_items = False
        try:
            self._restrict_trucks = bool(await get_setting("restrict_trucks"))
        except Exception:
            self._restrict_trucks = False

    async def _load_fleet_numbers(self) -> None:
        try:
            self._fleet_numbers = await get_fleet_numbers()
        except Exception:
            self._fleet_numbers = set()

    async def _load_locked_subitems(self) -> None:
        """Cache the allowed sub-item names for every lock-description item."""
        cache: dict = {}
        for c in self._categories:
            if getattr(c, "lock_description", False):
                try:
                    subs = await get_subtables(item_key(c.name))
                    cache[c.name.lower()] = [s.name for s in subs]
                except Exception:
                    cache[c.name.lower()] = []
        self._locked_subitems = cache

    # ------------------------------------------------------------------
    # Item-column validation (canonicalise / restrict / add-new prompt)
    # ------------------------------------------------------------------

    def _validate_item_cell(self, row: int, item: QTableWidgetItem) -> None:
        text = item.text().strip()
        if not text:
            return
        cat = self._cat_by_name.get(text.lower())
        if cat is not None:
            # Known item — snap to its canonical casing.
            if item.text() != cat.name:
                self._table.blockSignals(True)
                item.setText(cat.name)
                self._table.blockSignals(False)
            return
        if not self._restrict_items:
            return
        # Unknown item with restriction on — prompt to add (deferred to avoid
        # reentering the table's edit machinery).
        QTimer.singleShot(0, lambda: self._prompt_add_item(row, text))

    def _prompt_add_item(self, row: int, name: str) -> None:
        it = self._table.item(row, COL_ITEM)
        if it is None or it.text().strip().lower() != name.lower():
            return  # cell changed in the meantime
        # Open the same full Add-Item dialog the accountant uses, pre-filled with
        # the typed name, so every field can be set just like in Manage Items.
        from tahmeed.ui.accountant.manage_items import _ItemDialog
        dlg = _ItemDialog(parent=self, prefill_name=name)
        if dlg.exec() == QDialog.Accepted and dlg.result_data:
            asyncio.ensure_future(self._create_item_and_refresh(dlg.result_data, row))
        else:
            self._table.blockSignals(True)
            it.setText("")
            self._table.blockSignals(False)

    async def _create_item_and_refresh(self, data: dict, row: int) -> None:
        try:
            await create_category(
                data["name"], data["color"],
                data["requires_receipt"], data["requires_truck"],
                data.get("description", ""),
                icon=data.get("icon", "mdi.tag-outline"),
                show_in_sidebar=data.get("show_in_sidebar", False),
                lock_description=data.get("lock_description", False),
            )
            cats = await get_all_categories()
            self.update_categories(cats)
            cat = self._cat_by_name.get(data["name"].lower())
            it = self._table.item(row, COL_ITEM)
            if it is not None and cat is not None:
                self._table.blockSignals(True)
                it.setText(cat.name)
                self._table.blockSignals(False)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to add item:\n{exc}")

    # ------------------------------------------------------------------
    # Truck-column validation (restrict to truck/trailer registry)
    # ------------------------------------------------------------------

    def _validate_truck_cell(self, row: int, item: QTableWidgetItem) -> None:
        if not self._restrict_trucks:
            return
        number = item.text().strip().upper()
        if not number:
            return
        if number in self._fleet_numbers:
            if item.text() != number:
                self._table.blockSignals(True)
                item.setText(number)
                self._table.blockSignals(False)
            return
        QTimer.singleShot(0, lambda: self._reject_truck(row, number))

    def _reject_truck(self, row: int, number: str) -> None:
        it = self._table.item(row, COL_TRUCK)
        if it is None or it.text().strip().upper() != number:
            return
        QMessageBox.information(
            self, "Not in registry",
            f'"{number}" is not in the truck or trailer registry.\n\n'
            "Only registered trucks/trailers can be entered. Ask the accountant "
            "to add it under Manage → Trucks / Trailers.",
        )
        self._table.blockSignals(True)
        it.setText("")
        self._table.blockSignals(False)

    # ------------------------------------------------------------------
    # Description-lock validation
    # ------------------------------------------------------------------

    def _validate_locked_description(self, row: int, item: QTableWidgetItem) -> None:
        item_name = self._cell_text(row, COL_ITEM)
        if not item_name:
            return
        cat = self._cat_by_name.get(item_name.lower())
        if cat is None or not getattr(cat, "lock_description", False):
            return
        allowed = self._locked_subitems.get(item_name.lower(), [])
        if not allowed:
            return  # locked but no sub-items defined → don't block
        text = item.text().strip()
        if not text:
            return
        match = next((a for a in allowed if a.lower() == text.lower()), None)
        if match is not None:
            if item.text() != match:
                self._table.blockSignals(True)
                item.setText(match)
                self._table.blockSignals(False)
            return
        QTimer.singleShot(
            0, lambda: self._reject_locked_description(row, text, cat.name, allowed)
        )

    def _reject_locked_description(
        self, row: int, text: str, item_name: str, allowed: List[str]
    ) -> None:
        it = self._table.item(row, COL_DESC)
        if it is None or it.text().strip().lower() != text.lower():
            return
        QMessageBox.information(
            self, "Description locked",
            f'"{item_name}" only allows these descriptions:\n\n• '
            + "\n• ".join(allowed)
            + "\n\nPlease pick one of the above.",
        )
        self._table.blockSignals(True)
        it.setText("")
        self._table.blockSignals(False)

    def _cell_text(self, row: int, col: int) -> str:
        it = self._table.item(row, col)
        return it.text().strip() if it else ""


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
