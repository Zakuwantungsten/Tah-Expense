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
from PySide6.QtCore import (
    Qt, Signal, QDate, QEvent, QRect, QSize, QObject, QTimer,
    QItemSelection, QItemSelectionModel,
)
from PySide6.QtGui import QAction, QKeyEvent, QColor, QBrush, QFont, QPen, QPainter

from tahmeed.models.category import Category
from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.services.truck_service import get_fleet_numbers
from tahmeed.services.truck_format import (
    normalize_truck_number, try_match_fleet, normalize_place_label,
    is_allowed_place_label, DEFAULT_PLACE_LABELS, merge_allowed_labels,
)
from tahmeed.services.cashier_service import (
    get_transactions_by_date, save_transaction, delete_transaction,
    search_descriptions, update_transaction, insert_pending_edit,
    check_for_duplicates,
)
from tahmeed.services.category_service import (
    create_cashier_category, get_all_categories, item_key,
)
from tahmeed.services.subtable_service import get_subtables
from tahmeed.services.settings_service import get_setting, set_setting
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit
from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit, accept_completion
from tahmeed.ui.dialogs.truck_correction_dialog import TruckCorrectionDialog, TruckIssue

# ---------------------------------------------------------------------------
# Column indices
# ---------------------------------------------------------------------------
COL_SNO      = 0
COL_DATE     = 1
COL_REPORTED = 2
COL_ITEM     = 3
COL_DESC     = 4
COL_TRUCK    = 5
COL_MEMO     = 6
COL_REF      = 7   # Ref_Float (free text; was checkbox NOTES)
COL_TZS      = 8
COL_RECEIPT  = 9
COL_OWN      = 10
COL_APR      = 11
COL_PAYEE    = 12
COL_CHEQUE   = 13

HEADERS = [
    "S/NO", "Date", "Reported Date", "Item", "Description", "Truck No.",
    "Memo", "Ref_Float", "TZS", "Receipt", "Ownership", "APR BY",
    "Payee", "Cheque",
]

CHECK_COLS       = set()   # legacy; Ref_Float is free text now
READONLY_COLS    = {COL_SNO}
# Date / reported date alone do not count as entry data.
_DATA_SKIP_COLS  = READONLY_COLS | {COL_RECEIPT, COL_DATE, COL_REPORTED}
# Columns that should NOT be auto-uppercased
_UPPER_SKIP_COLS = READONLY_COLS | {COL_RECEIPT, COL_DATE, COL_REPORTED}
DEFAULT_EDITABLE_ROWS = 20

_REF_FLOAT_OPTS = ["REFUND TO FLOAT"]

# Preferred column widths — Description stretches to fill leftover viewport space.
_COL_PREFERRED = {
    COL_SNO: 48,
    COL_DATE: 92,
    COL_REPORTED: 92,
    COL_ITEM: 100,
    COL_DESC: 200,
    COL_TRUCK: 72,
    COL_MEMO: 90,
    COL_REF: 100,
    COL_TZS: 96,
    COL_RECEIPT: 88,
    COL_OWN: 80,
    COL_APR: 72,
    COL_PAYEE: 90,
    COL_CHEQUE: 80,
}
# Columns that shrink first when the viewport is tighter than the preferred sum.
_COL_FLEX = (
    COL_DESC, COL_MEMO, COL_PAYEE, COL_ITEM, COL_REF,
    COL_REPORTED, COL_DATE, COL_OWN, COL_APR, COL_CHEQUE, COL_RECEIPT,
)
_COL_MIN = {
    COL_SNO: 40,
    COL_DATE: 78,
    COL_REPORTED: 78,
    COL_ITEM: 70,
    COL_DESC: 120,
    COL_TRUCK: 60,
    COL_MEMO: 60,
    COL_REF: 70,
    COL_TZS: 72,
    COL_RECEIPT: 70,
    COL_OWN: 60,
    COL_APR: 56,
    COL_PAYEE: 60,
    COL_CHEQUE: 56,
}


def _is_refund_float(text: str) -> bool:
    return (text or "").strip().lower() == "refund to float"


def _ref_float_text(tx: "Transaction") -> str:
    """Display Ref_Float; backfill from legacy notes_flag when needed."""
    if getattr(tx, "ref_float", None):
        return str(tx.ref_float).strip().upper()
    if tx.notes_flag:
        return "REFUND TO FLOAT"
    return ""


def _parse_optional_date(text: str):
    """Parse dd/MM/yyyy → datetime, or None when blank/invalid."""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d/%m/%Y")
    except ValueError:
        return None

# Colors
SAVED_BG  = QColor("#fff8f0")
NEW_BG    = QColor("#ffffff")
EMPTY_BG  = QColor("#fafafa")
NEG_COLOR = QColor("#dc2626")
EDIT_BG   = QColor("#FFFBEB")   # warm yellow — saved rows unlocked for editing
DIRTY_BG  = QColor("#FEF3C7")   # stronger amber — a saved row that was modified
DUP_BG    = QColor("#FEE2E2")   # light red — possible duplicate flag
MISMATCH_BG = QColor("#FEF3C7") # amber — date mismatch (submitted vs transaction date)

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
        opt.backgroundBrush = QBrush(Qt.NoBrush)
        return opt

    def _paint_text(self, painter: QPainter, option, index) -> None:
        """Draw cell text only — never paints background (selection stays visible)."""
        text = option.text if option.text is not None else ""
        if text == "" and index.data() is not None:
            text = str(index.data())
        fg = index.data(Qt.ForegroundRole)
        if isinstance(fg, QBrush) and fg.style() != Qt.NoBrush:
            painter.setPen(fg.color())
        elif isinstance(fg, QColor):
            painter.setPen(fg)
        else:
            painter.setPen(QColor("#111827"))
        align = index.data(Qt.TextAlignmentRole)
        if align is None:
            align = int(option.displayAlignment) if option.displayAlignment else int(
                Qt.AlignLeft | Qt.AlignVCenter
            )
        else:
            align = int(align)
        painter.drawText(option.rect.adjusted(6, 0, -6, 0), align, text)

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
        self._paint_text(painter, option, index)
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
    def __init__(self, get_fleet, parent=None):
        super().__init__(parent)
        self._get_fleet = get_fleet

    def createEditor(self, parent, option, index):
        # Sync local filter — safe during import modals / nested asyncio.
        ed = TruckLineEdit(local_numbers=self._get_fleet, parent=parent)
        ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
        return ed

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")

    def setModelData(self, editor, model, index):
        text = editor.text().strip()
        if not text:
            model.setData(index, "")
            return
        result = normalize_truck_number(text)
        if result.status in ("ok", "normalized"):
            model.setData(index, result.value)
        else:
            model.setData(index, result.value or text.upper())

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
            self._paint_text(painter, option, index)

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
    """Ref_Float free-text editor with Item-like autocomplete (REFUND TO FLOAT)."""

    def createEditor(self, parent, option, index):
        ed = CompleterLineEdit(list(_REF_FLOAT_OPTS), parent=parent)
        ed._completer.setFilterMode(Qt.MatchStartsWith)
        ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
        return ed

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        text = (editor.canonical(editor.text().strip()) or editor.text().strip()).upper()
        model.setData(index, text)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


_RCPT_COLORS = {
    "received":   ("#dcfce7", "#16a34a"),
    "pending":    ("#fff7ed", "#ea580c"),
    "missing":    ("#fef2f2", "#dc2626"),
    "no_receipt": ("#f3f4f6", "#6b7280"),
}
# Display labels are uppercase (RECEIPT, not Received).
_RCPT_LABEL = {
    "received": "RECEIPT",
    "pending": "PENDING",
    "missing": "MISSING",
    "no_receipt": "NO RECEIPT",
}
_RECEIPT_OPTS = ["PENDING", "RECEIPT", "MISSING", "NO RECEIPT"]
# Display label (lowercased) -> stored status key
_RCPT_OPT_KEY = {
    "pending": "pending",
    "receipt": "received",
    "received": "received",
    "missing": "missing",
    "no receipt": "no_receipt",
}
# Any incoming text (paste / import) -> stored status key
_RCPT_NORM = {
    "received": "received",
    "receipt": "received",
    "1": "received",
    "yes": "received",
    "rcvd": "received",
    "missing": "missing",
    "pending": "pending",
    "0": "pending",
    "no receipt": "no_receipt",
    "no_receipt": "no_receipt",
    "none": "no_receipt",
    "n/a": "no_receipt",
    "na": "no_receipt",
}
_VALID_RCPT = {"pending", "received", "missing", "no_receipt"}


def _norm_receipt_text(raw: str) -> str:
    key = " ".join((raw or "").strip().lower().split())
    if key in _RCPT_NORM:
        return _RCPT_NORM[key]
    if "no receipt" in key:
        return "no_receipt"
    if key == "receipt" or "received" in key:
        return "received"
    return "pending"


def _parse_amount_text(raw: str) -> float:
    from tahmeed.services.daily_import_service import parse_amount
    val = parse_amount(raw)
    return float(val) if val is not None else 0.0


class _ReceiptDelegate(_ExcelCellDelegate):
    """Receipt status — normal cell font + autocomplete (same interaction as Item)."""

    def paint(self, painter, option, index) -> None:
        self.initStyleOption(option, index)
        status = (index.data() or "").strip().lower()
        option.text = _RCPT_LABEL.get(status, status.upper() if status else "")
        self._paint_bg(painter, option, index)
        self._paint_text(painter, option, index)
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
        key = _RCPT_OPT_KEY.get(disp.lower())
        if key is None:
            key = _norm_receipt_text(disp)
        model.setData(index, key if key in _VALID_RCPT else "")

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _ItemDelegate(_ExcelCellDelegate):
    """Live popup of accountant-managed items for the Item column.

    QuickBooks-style contains match: the list narrows to names that contain the
    typed text anywhere (start/middle/end), ranked so exact and prefix hits
    come first. The field is not auto-filled while typing; Tab/Enter commits the
    highlighted suggestion as the *canonical* item name (so "m" + Tab gives
    "MILEAGE"). The list is read live via ``items_getter`` so newly-created
    items appear at once.
    Whether unknown entries are allowed is enforced at the grid level.
    """

    def __init__(self, items_getter, parent=None):
        super().__init__(parent)
        self._items_getter = items_getter

    def createEditor(self, parent, option, index):
        # Ranked contains: "csh" finds "Diesel CSH". Popup only — field stays
        # as typed until Tab/Enter/click commits the highlighted item.
        ed = CompleterLineEdit(
            self._items_getter() or [],
            parent=parent,
            ranked_contains=True,
        )
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
# Table with Excel-like S/NO row selection
# ---------------------------------------------------------------------------

class _ExcelTableWidget(QTableWidget):
    """QTableWidget that selects full rows when the S/NO column is clicked.

    Other columns keep normal contiguous cell-range selection (copy/paste).
    Clicking or dragging S/NO behaves like Excel's row-number gutter.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sn_dragging = False
        self._sn_anchor_row = -1

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            index = self.indexAt(event.pos())
            if index.isValid() and index.column() == COL_SNO:
                row = index.row()
                self._sn_dragging = True
                if (event.modifiers() & Qt.ShiftModifier) and self._sn_anchor_row >= 0:
                    self._select_sn_rows(self._sn_anchor_row, row)
                else:
                    self._sn_anchor_row = row
                    self._select_sn_rows(row, row)
                return
        self._sn_dragging = False
        super().mousePressEvent(event)
        cur = self.currentIndex()
        if cur.isValid() and not (event.modifiers() & Qt.ShiftModifier):
            self._sn_anchor_row = cur.row()

    def mouseMoveEvent(self, event):
        if self._sn_dragging and (event.buttons() & Qt.LeftButton):
            index = self.indexAt(event.pos())
            if index.isValid() and self._sn_anchor_row >= 0:
                self._select_sn_rows(self._sn_anchor_row, index.row())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._sn_dragging = False
        super().mouseReleaseEvent(event)

    def _select_sn_rows(self, row_a: int, row_b: int) -> None:
        """Select every column in the contiguous row range (Excel row gutter)."""
        r0, r1 = min(row_a, row_b), max(row_a, row_b)
        model = self.model()
        selection = QItemSelection(
            model.index(r0, 0),
            model.index(r1, self.columnCount() - 1),
        )
        self.selectionModel().select(
            selection, QItemSelectionModel.ClearAndSelect
        )
        self.selectionModel().setCurrentIndex(
            model.index(row_b, COL_SNO),
            QItemSelectionModel.NoUpdate,
        )


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
            if col == COL_REF:
                v = it.text().strip()
                if v:
                    values.add(v)
            elif col == COL_RECEIPT:
                v = (it.text() or "").strip().lower()
                if v:
                    values.add(_RCPT_LABEL.get(v, v))
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

    rows_saved        = Signal(int)
    stats_updated     = Signal(int, float, float)  # (n_entries, total_tzs, refund_total)
    edit_state_changed = Signal(bool, int)         # (edit_mode_active, dirty_row_count)

    def __init__(self, user: User, categories: List[Category], parent=None):
        super().__init__(parent)
        self._user        = user
        self._categories  = categories
        self._cat_by_name: dict = {c.name.lower(): c for c in categories}
        self._locked_subitems: dict = {}   # item name (lower) -> [sub-item names]
        self._restrict_items: bool = False
        self._defer_item_to_verify: bool = False
        self._restrict_trucks: bool = True  # always on — only registered fleet numbers
        self._fleet_numbers: set = set()   # uppercased valid truck/trailer numbers
        self._allowed_truck_labels: set = set(DEFAULT_PLACE_LABELS)
        self._people_names: list = []      # Ownership / APR BY suggestions (unrestricted)
        self._current_date: date = date.today()
        self._saved_count: int   = 0
        self._saved_ids: dict    = {}   # row_index -> ObjectId
        self._saved_txs: dict    = {}   # row_index -> original Transaction (saved rows)
        self._edit_mode: bool    = False
        self._dirty_rows: set    = set()  # saved row indices modified while editing
        self._col_filters: dict   = {}   # col -> set of accepted values
        self._search_text: str    = ""
        self._pending_highlight: str = ""  # set by navigate_to_date; consumed in _populate
        # row_index -> import metadata stamped onto Transaction at save time
        self._pending_row_meta: dict = {}
        # When True, skip async side-effects from itemChanged (bulk paste/import).
        self._bulk_mutating: bool = False
        # When True, queue truck issues but do not open the correction dialog yet
        # (avoids nested asyncio during daily import modals on Python 3.14).
        self._suppress_truck_dialog: bool = False
        # Coalesce truck issues into one combined dialog (paste / import / edit).
        self._pending_truck_issues: dict = {}  # row -> TruckIssue
        self._truck_dialog_scheduled: bool = False
        self._open_truck_dialog: object = None
        self._build_ui()
        asyncio.ensure_future(self._load_date(self._current_date))
        asyncio.ensure_future(self._load_cashier_settings())
        asyncio.ensure_future(self._load_locked_subitems())
        asyncio.ensure_future(self._load_fleet_numbers())
        asyncio.ensure_future(self._load_people_names())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Table ──────────────────────────────────────────────────────
        self._table = _ExcelTableWidget(DEFAULT_EDITABLE_ROWS, len(HEADERS))
        _fhv = _FilterHeaderView(self._table)
        _fhv.filter_changed.connect(self._on_col_filter_changed)
        self._table.setHorizontalHeader(_fhv)
        self._table.setHorizontalHeaderLabels(HEADERS)
        sno_hdr = self._table.horizontalHeaderItem(COL_SNO)
        if sno_hdr is not None:
            sno_hdr.setTextAlignment(Qt.AlignCenter)
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
                padding: 5px 4px;
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
        hh.setMinimumSectionSize(50)
        # Interactive columns; S/NO fixed; Description stretches to fill the viewport.
        for col in range(len(HEADERS)):
            hh.setSectionResizeMode(col, QHeaderView.Interactive)
        hh.setSectionResizeMode(COL_SNO,  QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_DESC, QHeaderView.Stretch)

        for col, width in _COL_PREFERRED.items():
            self._table.setColumnWidth(col, width)
        QTimer.singleShot(0, self._fit_table_columns)

        self._table.setSelectionMode(QAbstractItemView.ContiguousSelection)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.verticalHeader().setVisible(False)
        self._table.setTabKeyNavigation(False)

        # Excel selection model on every column; per-column delegates override as needed
        self._table.setItemDelegate(_ExcelCellDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_ITEM,     _ItemDelegate(lambda: [c.name for c in self._categories], self._table))
        self._table.setItemDelegateForColumn(COL_DESC,     _DescriptionDelegate(
            cat_getter=lambda name: self._cat_by_name.get(name.lower()),
            subs_getter=lambda name: self._locked_subitems.get(name.lower(), []),
            parent=self._table,
        ))
        self._table.setItemDelegateForColumn(COL_TRUCK,    _TruckDelegate(
            lambda: sorted(self._fleet_numbers), self._table
        ))
        date_del = _DateDelegate(lambda: self._current_date, self._table)
        self._table.setItemDelegateForColumn(COL_DATE,     date_del)
        self._table.setItemDelegateForColumn(COL_REPORTED, date_del)
        self._table.setItemDelegateForColumn(COL_REF,      _RefFloatDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_TZS,      _TZSDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_RECEIPT,  _ReceiptDelegate(self._table))
        # Ownership + APR BY — same Item-style autocomplete/preview; free text always allowed.
        people_del = _ItemDelegate(lambda: list(self._people_names), self._table)
        self._table.setItemDelegateForColumn(COL_OWN, people_del)
        self._table.setItemDelegateForColumn(COL_APR, people_del)

        self._table.itemChanged.connect(self._on_item_changed)
        self._table.model().dataChanged.connect(self._on_model_data_changed)
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_table_columns()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_table_columns)

    def _fit_table_columns(self) -> None:
        """Scale columns to the viewport so the register fits without H-scroll."""
        if not hasattr(self, "_table") or self._table is None:
            return
        vp = self._table.viewport().width()
        if vp <= 0:
            return

        widths = dict(_COL_PREFERRED)
        total = sum(widths.values())
        if total > vp:
            deficit = total - vp
            for col in _COL_FLEX:
                if deficit <= 0:
                    break
                floor = _COL_MIN.get(col, 50)
                cut = min(max(0, widths[col] - floor), deficit)
                widths[col] -= cut
                deficit -= cut

        hh = self._table.horizontalHeader()
        # Apply concrete widths, then let Description absorb any leftover slack.
        hh.setSectionResizeMode(COL_DESC, QHeaderView.Interactive)
        for col, width in widths.items():
            self._table.setColumnWidth(col, width)
        hh.setSectionResizeMode(COL_SNO,  QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_DESC, QHeaderView.Stretch)

    def navigate_to_date(self, d: date, highlight_term: str = "") -> None:
        """Called by dashboard when TransactionBrowser 'Go To' is used.

        highlight_term — if provided, the register scrolls to the first row
        containing this text after the date loads and briefly flashes it.
        """
        self._pending_highlight = highlight_term
        if self._edit_mode and self._dirty_rows:
            resp = QMessageBox.question(
                self, "Unsaved changes",
                "You have unsaved changes on this date.\nSave them before leaving?",
                QMessageBox.Yes | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if resp == QMessageBox.Cancel:
                self._pending_highlight = ""
                return
            if resp == QMessageBox.Yes:
                asyncio.ensure_future(self._save_then_navigate(d))
                return
            # Discard → fall through and reload the new date
        self._reset_edit_state()
        self._current_date = d
        asyncio.ensure_future(self._load_date(d))

    async def _save_then_navigate(self, d: date) -> None:
        await self._do_save()
        self._current_date = d
        await self._load_date(d)

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------

    async def _load_date(self, d: date) -> None:
        try:
            txs = await get_transactions_by_date(d, cashier_id=self._user._id)
            self._pending_row_meta.clear()
            self._populate(txs)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load:\n{exc}")

    def refresh(self) -> None:
        asyncio.ensure_future(self._load_date(self._current_date))
        asyncio.ensure_future(self._load_cashier_settings())

    def reload_settings(self) -> None:
        """Re-read the restrict toggles, locked sub-items and fleet list without
        touching the grid rows (so unsaved entries survive). Called on entering
        the table tab."""
        asyncio.ensure_future(self._load_cashier_settings())
        asyncio.ensure_future(self._load_locked_subitems())
        asyncio.ensure_future(self._load_fleet_numbers())
        asyncio.ensure_future(self._load_people_names())

    def update_people(self, names: list) -> None:
        """Refresh Ownership / APR BY suggestion list (free text still allowed)."""
        self._people_names = [str(n).strip().upper() for n in (names or []) if str(n).strip()]

    async def _load_people_names(self) -> None:
        try:
            from tahmeed.services.people_service import get_people_names
            self.update_people(await get_people_names())
        except Exception:
            self._people_names = []

    def _populate(self, transactions: List[Transaction]) -> None:
        # A fresh load always returns the grid to read-only state.
        self._edit_mode = False
        self._dirty_rows = set()

        self._table.blockSignals(True)
        self._table.clearContents()
        self._saved_count = len(transactions)
        self._saved_ids   = {}
        self._saved_txs   = {}

        total_rows = self._saved_count + DEFAULT_EDITABLE_ROWS
        self._table.setRowCount(total_rows)

        for i, tx in enumerate(transactions):
            self._fill_saved_row(i, tx)
            self._saved_ids[i] = tx._id
            self._saved_txs[i] = tx

        self._init_editable_rows(self._saved_count, total_rows)
        self._table.blockSignals(False)
        self._renumber()
        self._update_footer()
        self._apply_filters()
        self.edit_state_changed.emit(False, 0)

        if self._pending_highlight:
            term = self._pending_highlight
            self._pending_highlight = ""
            # Small delay so Qt finishes laying out the rows before we scroll.
            QTimer.singleShot(80, lambda: self.scroll_and_highlight(term))

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

        # S/NO — same row background as siblings (Excel-style continuous row)
        sno = saved_item(str(row + 1), Qt.AlignCenter)
        self._table.setItem(row, COL_SNO, sno)

        date_str = tx.date.strftime("%d/%m/%Y") if tx.date else ""
        date_item = saved_item(date_str)
        if tx.date and tx.created_at and tx.date.date() != tx.created_at.date():
            date_item.setBackground(QBrush(MISMATCH_BG))
            date_item.setToolTip(
                f"Transaction dated {tx.date.strftime('%d %b %y')} but submitted on "
                f"{tx.created_at.strftime('%d %b %y')}"
            )
        self._table.setItem(row, COL_DATE, date_item)

        reported = getattr(tx, "reported_date", None)
        reported_str = reported.strftime("%d/%m/%Y") if reported else ""
        self._table.setItem(row, COL_REPORTED, saved_item(reported_str))

        self._table.setItem(row, COL_ITEM, saved_item(tx.item or ""))

        desc_item = saved_item(tx.description)
        if tx.possible_duplicate:
            desc_item.setBackground(QBrush(DUP_BG))
            desc_item.setToolTip("Possible duplicate — similar entry found within the check window")
        self._table.setItem(row, COL_DESC, desc_item)
        self._table.setItem(row, COL_TRUCK, saved_item(tx.truck_number or ""))
        self._table.setItem(row, COL_MEMO,  saved_item(tx.memo or ""))
        self._table.setItem(row, COL_REF,   saved_item(_ref_float_text(tx)))

        # TZS
        tzs_str = f"{tx.amount:,.2f}" if tx.amount else ""
        tzs_it  = saved_item(tzs_str, Qt.AlignRight | Qt.AlignVCenter)
        if tx.amount and tx.amount < 0:
            tzs_it.setForeground(NEG_COLOR)
        self._table.setItem(row, COL_TZS, tzs_it)

        # Receipt
        rcpt_it = saved_item(tx.receipt_status or "pending")
        self._table.setItem(row, COL_RECEIPT, rcpt_it)

        self._table.setItem(row, COL_OWN,    saved_item(tx.ownership or ""))
        self._table.setItem(row, COL_APR,    saved_item(tx.approver or ""))
        self._table.setItem(row, COL_PAYEE,  saved_item(getattr(tx, "payee", "") or ""))
        self._table.setItem(row, COL_CHEQUE, saved_item(getattr(tx, "cheque", "") or ""))

    def _init_editable_rows(self, start: int, end: int) -> None:
        # Preserve caller's blockSignals state — never force-unblock mid bulk load.
        prev = self._table.blockSignals(True)
        for row in range(start, end):
            # S/NO — blank until row is activated by data entry or Tab wrap
            sno = QTableWidgetItem("")
            sno.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            sno.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, COL_SNO, sno)
            # Checkbox items are created lazily in _activate_row
        self._table.blockSignals(prev)

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------

    def _update_footer(self) -> None:
        """Recompute entries / total / refund from the live grid (saved + unsaved)."""
        n, tzs, refund = 0, 0.0, 0.0
        for row in range(self._table.rowCount()):
            tzs_it = self._table.item(row, COL_TZS)
            if not tzs_it:
                continue
            raw = tzs_it.text().strip()
            if not raw:
                continue
            amount = _parse_amount_text(raw)
            # Skip non-numeric leftovers that parse as 0
            if amount == 0.0 and not any(ch.isdigit() for ch in raw):
                continue
            n += 1
            tzs += amount
            ref_it = self._table.item(row, COL_REF)
            if ref_it and _is_refund_float(ref_it.text()):
                refund += amount

        amount_str = f"TZS {tzs:,.0f}" if tzs else "—"
        self._totals_label.setText(
            f"{n} entr{'y' if n == 1 else 'ies'}   ·   {amount_str}"
        )
        self.stats_updated.emit(n, tzs, refund)

    # ------------------------------------------------------------------
    # Row → Transaction
    # ------------------------------------------------------------------

    def _build_transaction_from_row(self, row: int) -> Optional[Transaction]:
        """Read cell values for a single row and return a Transaction, or None
        if the row has no description. Raises ValueError on validation errors
        (bad item / locked description / unregistered truck) so callers can
        distinguish logical from network failures and skip retries."""
        def txt(col: int) -> str:
            it = self._table.item(row, col)
            return it.text().strip() if it else ""

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

        raw_tzs = txt(COL_TZS)
        amount = _parse_amount_text(raw_tzs)

        rcpt_status = _norm_receipt_text(txt(COL_RECEIPT))
        if rcpt_status not in _VALID_RCPT:
            rcpt_status = "pending"

        item_name = txt(COL_ITEM)
        meta = self._pending_row_meta.get(row) or {}
        allow_blank_item = self._defer_item_to_verify or bool(meta.get("daily_import_id"))
        if not item_name and not allow_blank_item:
            raise ValueError("Item is required. Enter an item or ask the accountant to enable description-only entries.")

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

        truck_raw = txt(COL_TRUCK)
        truck_number = ""
        if truck_raw:
            if is_allowed_place_label(truck_raw, self._allowed_truck_labels):
                truck_number = normalize_place_label(truck_raw)
            else:
                matched = try_match_fleet(truck_raw, self._fleet_numbers)
                if matched is None:
                    norm = normalize_truck_number(
                        truck_raw, allowed_labels=self._allowed_truck_labels
                    )
                    label = norm.value if norm.status != "empty" else truck_raw
                    if norm.status == "invalid":
                        raise ValueError(
                            f'"{label}" is not a valid truck number '
                            f"(expected T + number + space + suffix, e.g. T688 EAF)."
                        )
                    if norm.status == "place_label":
                        truck_number = norm.value
                    else:
                        raise ValueError(
                            f'"{norm.value}" is not a registered truck/trailer.'
                        )
                else:
                    truck_number = matched

        ref_text = txt(COL_REF)
        return Transaction(
            date=tx_date,
            reported_date=_parse_optional_date(txt(COL_REPORTED)),
            description=description,
            item=item_name,
            category_name=item_name or None,
            category_id=meta.get("category_id"),
            truck_number=truck_number,
            amount=amount,
            currency=meta.get("currency") or "TZS",
            memo=txt(COL_MEMO),
            receipt_status=rcpt_status,
            ref_float=ref_text,
            notes_flag=_is_refund_float(ref_text),
            ownership=txt(COL_OWN),
            approver=txt(COL_APR),
            payee=txt(COL_PAYEE),
            cheque=txt(COL_CHEQUE),
            cashier_id=self._user._id,
            daily_import_id=meta.get("daily_import_id"),
            daily_import_source=meta.get("daily_import_source"),
            date_discrepancy=bool(meta.get("date_discrepancy")),
            import_primary_date=meta.get("import_primary_date"),
            lpo_do=meta.get("lpo_do") or "",
            do_number=meta.get("do_number") or "",
        )

    # ------------------------------------------------------------------
    # Edit mode
    # ------------------------------------------------------------------

    def toggle_edit_mode(self) -> None:
        """Public entry point for the Edit button: enter edit mode, or exit and
        discard pending changes on a second press."""
        if self._edit_mode:
            if self._dirty_rows:
                resp = QMessageBox.question(
                    self, "Discard changes?",
                    "Exit edit mode and discard your unsaved changes?",
                    QMessageBox.Discard | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if resp == QMessageBox.Cancel:
                    return
            self._exit_edit_mode(discard=True)
        else:
            self._enter_edit_mode()

    def _enter_edit_mode(self) -> None:
        """Unlock every saved row for editing and tint it warm yellow."""
        self._edit_mode = True
        self._dirty_rows = set()
        editable = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
        self._table.blockSignals(True)
        for row in range(self._saved_count):
            for col in range(self._table.columnCount()):
                it = self._table.item(row, col)
                if it is None:
                    continue
                if col not in READONLY_COLS:
                    it.setFlags(editable)
                it.setBackground(QBrush(EDIT_BG))
        self._table.blockSignals(False)
        self.edit_state_changed.emit(True, 0)

    def _exit_edit_mode(self, discard: bool) -> None:
        """Leave edit mode. When discard is True the date is reloaded so the grid
        reverts to the stored values; otherwise the caller reloads after saving."""
        self._reset_edit_state()
        if discard:
            asyncio.ensure_future(self._load_date(self._current_date))

    def _reset_edit_state(self) -> None:
        self._edit_mode = False
        self._dirty_rows = set()
        self.edit_state_changed.emit(False, 0)

    def _mark_dirty(self, row: int) -> None:
        """Flag a saved row as modified and give it a stronger amber background."""
        if row in self._dirty_rows:
            return
        self._dirty_rows.add(row)
        self._table.blockSignals(True)
        for col in range(self._table.columnCount()):
            it = self._table.item(row, col)
            if it is not None:
                it.setBackground(QBrush(DIRTY_BG))
        self._table.blockSignals(False)
        self.edit_state_changed.emit(True, len(self._dirty_rows))

    def _updates_from_row(self, row: int) -> Optional[dict]:
        """Build the $set payload for an edited saved row from its cell values.
        Returns None when the row has no description. Raises ValueError on
        validation errors (bad item / locked description / unregistered truck)."""
        tx = self._build_transaction_from_row(row)
        if tx is None:
            return None
        return {
            "date": tx.date,
            "reported_date": tx.reported_date,
            "description": tx.description,
            "item": tx.item,
            "category_name": tx.category_name,
            "truck_number": tx.truck_number,
            "amount": tx.amount,
            "currency": tx.currency,
            "memo": tx.memo,
            "receipt_status": tx.receipt_status,
            "notes_flag": tx.notes_flag,
            "ref_float": tx.ref_float,
            "ownership": tx.ownership,
            "approver": tx.approver,
            "payee": tx.payee,
            "cheque": tx.cheque,
        }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    _EXPORT_COLS = [
        COL_DATE, COL_REPORTED, COL_ITEM, COL_DESC, COL_TRUCK, COL_MEMO,
        COL_REF, COL_TZS, COL_RECEIPT, COL_OWN, COL_APR, COL_PAYEE, COL_CHEQUE,
    ]

    def export_xlsx(self) -> None:
        """Export the currently-visible rows (respecting search + column filters)
        to an .xlsx (or .csv) file chosen by the user."""
        default_name = f"register_{self._current_date.isoformat()}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export register", default_name,
            "Excel Workbook (*.xlsx);;CSV File (*.csv)",
        )
        if not path:
            return
        rows = self._visible_export_rows()
        if not rows:
            QMessageBox.information(self, "Nothing to export",
                                    "There are no visible rows to export.")
            return
        try:
            if path.lower().endswith(".csv"):
                self._write_csv(path, rows)
            else:
                self._write_xlsx(path, rows)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Export complete",
                               f"{len(rows)} row(s) exported to:\n{path}")

    # Convenience alias — forces the CSV writer regardless of the chosen name.
    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export register",
            f"register_{self._current_date.isoformat()}.csv",
            "CSV File (*.csv)",
        )
        if not path:
            return
        rows = self._visible_export_rows()
        if not rows:
            QMessageBox.information(self, "Nothing to export",
                                    "There are no visible rows to export.")
            return
        try:
            self._write_csv(path, rows)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Export complete",
                               f"{len(rows)} row(s) exported to:\n{path}")

    def _visible_export_rows(self) -> List[list]:
        out: List[list] = []
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            if not (row < self._saved_count or self._row_has_data(row)):
                continue
            rec: list = []
            for col in self._EXPORT_COLS:
                it = self._table.item(row, col)
                if col == COL_RECEIPT:
                    val = (it.text().strip() if it else "")
                    rec.append(_RCPT_LABEL.get(val.lower(), val))
                else:
                    rec.append(it.text().strip() if it else "")
            out.append(rec)
        return out

    def _write_csv(self, path: str, rows: List[list]) -> None:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([HEADERS[c] for c in self._EXPORT_COLS])
            w.writerows(rows)

    def _write_xlsx(self, path: str, rows: List[list]) -> None:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Register"
        ws.append([HEADERS[c] for c in self._EXPORT_COLS])
        for rec in rows:
            ws.append(rec)
        wb.save(path)

    # ------------------------------------------------------------------
    # Search & column filtering
    # ------------------------------------------------------------------

    def set_search(self, text: str) -> None:
        self._search_text = text.strip().lower()
        self._apply_filters()

    def scroll_and_highlight(self, term: str) -> None:
        """Scroll to the first saved row that contains term and flash-highlight it.

        Scans all visible columns for a case-insensitive substring match.
        Applies a 2-second amber highlight then restores the original row background.
        Does NOT change the active search filter.
        """
        if not term:
            return
        needle = term.strip().lower()

        first_match = -1
        for row in range(self._saved_count):
            if self._table.isRowHidden(row):
                continue
            for col in range(self._table.columnCount()):
                it = self._table.item(row, col)
                if it and needle in it.text().lower():
                    first_match = row
                    break
            if first_match >= 0:
                break

        if first_match < 0:
            return

        self._table.scrollTo(self._table.model().index(first_match, COL_DESC))
        self._table.setCurrentCell(first_match, COL_DESC)

        highlight = QBrush(QColor("#FDE68A"))   # amber-200
        saved_bgs: dict = {}
        for col in range(self._table.columnCount()):
            it = self._table.item(first_match, col)
            if it:
                saved_bgs[col] = QBrush(it.background())
                it.setBackground(highlight)

        def _restore() -> None:
            for col, bg in saved_bgs.items():
                it = self._table.item(first_match, col)
                if it:
                    it.setBackground(bg)

        QTimer.singleShot(2000, _restore)

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
                    if not it:
                        continue
                    if col == COL_RECEIPT:
                        label = _RCPT_LABEL.get(it.text().strip().lower(), it.text())
                        if search in label.lower() or search in it.text().lower():
                            matched = True
                            break
                    elif search in it.text().lower():
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
                if col == COL_RECEIPT:
                    raw = it.text().strip().lower() if it else ""
                    val = _RCPT_LABEL.get(raw, raw)
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
                    if not self._table.item(row, COL_RECEIPT):
                        ri = QTableWidgetItem("")
                        ri.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                        self._table.setItem(row, COL_RECEIPT, ri)
        self._table.blockSignals(False)
        # Keep header KPIs (entries / refund / total) in sync after row ops
        # that block itemChanged (delete, clear, paste, import).
        self._update_footer()

    # ------------------------------------------------------------------
    # Dynamic row expansion
    # ------------------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        col = item.column()

        # Saved rows: only mutable while in edit mode. Track them as dirty and
        # uppercase free-text, but skip the new-row activation / expansion logic.
        if row < self._saved_count:
            if not self._edit_mode:
                return
            if col not in _UPPER_SKIP_COLS:
                text = item.text()
                if text and text != text.upper():
                    self._table.blockSignals(True)
                    item.setText(text.upper())
                    self._table.blockSignals(False)
            self._mark_dirty(row)
            if col == COL_TZS:
                self._update_footer()
            return

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

        # Keep Date in sync with whether the row has real entry data.
        # Skip when the Date cell itself is edited so a manual date is not
        # immediately cleared on an otherwise empty row.
        if col not in READONLY_COLS and col not in CHECK_COLS and col not in (COL_DATE, COL_REPORTED):
            self._table.blockSignals(True)
            self._sync_row_date(row)
            self._table.blockSignals(False)

        # Item / Description / Truck validation (canonicalise, restrict, locked lists)
        if col == COL_ITEM and item.text().strip():
            self._validate_item_cell(row, item)
        elif col == COL_DESC and item.text().strip():
            self._validate_locked_description(row, item)
            if self._defer_item_to_verify and not self._bulk_mutating:
                # Defer off the current asyncio task so qasync/Py3.14 does not
                # try to nest _auto_fill inside an active import coroutine.
                desc = item.text().strip()
                QTimer.singleShot(
                    0, lambda r=row, d=desc: self._kick_auto_fill_item(r, d)
                )
        elif col == COL_TRUCK and item.text().strip():
            self._validate_truck_cell(row, item)

        # Dynamic row expansion near the bottom
        if row >= self._table.rowCount() - 5 and item.text().strip():
            self._append_editable_rows(10)

        if col in (COL_TZS, COL_REF):
            self._update_footer()

    def _on_model_data_changed(self, top_left, bottom_right, roles=()) -> None:
        # Kept for receipt/other UserRole updates if added later.
        pass

    def _activate_row(self, row: int) -> None:
        """Make a blank editable row visible: set S/NO number and create input items."""
        if row < self._saved_count:
            return
        sno_it = self._table.item(row, COL_SNO)
        if sno_it and sno_it.text():
            return  # already active
        if sno_it:
            sno_it.setText(str(row + 1))
        prev = self._table.blockSignals(True)
        if not self._table.item(row, COL_RECEIPT):
            ri = QTableWidgetItem("")
            ri.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._table.setItem(row, COL_RECEIPT, ri)
        self._table.blockSignals(prev)

    def _deactivate_row(self, row: int) -> None:
        """Clear S/NO on an emptied editable row so it looks blank again."""
        if row < self._saved_count:
            return
        sno_it = self._table.item(row, COL_SNO)
        if sno_it and sno_it.text():
            sno_it.setText("")

    def _register_date_str(self) -> str:
        d = self._current_date
        return QDate(d.year, d.month, d.day).toString("dd/MM/yyyy")

    def _sync_row_date(self, row: int) -> None:
        """Fill Date when the row gains entry data; clear it when the row is emptied.

        Caller should block itemChanged signals when batching writes. Does not
        overwrite a date that is already set.
        """
        if row < self._saved_count:
            return
        has_data = self._row_has_data(row)
        date_it = self._table.item(row, COL_DATE)
        date_text = date_it.text().strip() if date_it else ""

        if has_data:
            if not date_text:
                new_it = QTableWidgetItem(self._register_date_str())
                new_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                self._table.setItem(row, COL_DATE, new_it)
            self._activate_row(row)
        else:
            if date_text:
                if date_it is not None:
                    date_it.setText("")
                else:
                    self._table.setItem(row, COL_DATE, QTableWidgetItem(""))
            self._deactivate_row(row)

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
        """If the focused cell is an empty Date/Reported Date cell, write the register date."""
        row, col = self._table.currentRow(), self._table.currentColumn()
        if col not in (COL_DATE, COL_REPORTED) or row < self._saved_count:
            return
        it = self._table.item(row, col)
        if it is not None and it.text().strip():
            return
        cur = self._current_date
        today_str = QDate(cur.year, cur.month, cur.day).toString("dd/MM/yyyy")
        new_it = QTableWidgetItem(today_str)
        new_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        self._table.blockSignals(True)
        self._table.setItem(row, col, new_it)
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

        truck_cells: list = []  # (row, raw_text)
        self._bulk_mutating = True
        try:
            # Single clipboard value pasted onto a multi-cell selection: fill every
            # selected editable cell with that value (Excel behaviour).
            if len(lines) == 1 and "\t" not in lines[0] and sel_rows and (
                len(sel_rows) > 1 or len(sel_cols) > 1
            ):
                cell_value = lines[0].strip()
                prev = self._table.blockSignals(True)
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
                            norm = _norm_receipt_text(cell_value)
                            it = self._table.item(row, col) or QTableWidgetItem()
                            it.setText(norm)
                            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                            self._table.setItem(row, col, it)
                        elif col == COL_TZS:
                            amt = _parse_amount_text(cell_value)
                            text = f"{amt:,.2f}" if cell_value.strip() else ""
                            it = QTableWidgetItem(text)
                            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                            if amt < 0:
                                it.setForeground(NEG_COLOR)
                            self._table.setItem(row, col, it)
                        elif col == COL_TRUCK:
                            self._table.setItem(row, col, QTableWidgetItem(cell_value.upper()))
                            if cell_value:
                                truck_cells.append((row, cell_value))
                        else:
                            self._table.setItem(row, col, QTableWidgetItem(_upper_text(col, cell_value)))
                for row in sel_rows:
                    self._sync_row_date(row)
                self._table.blockSignals(prev)
                self._renumber()
                self._finalize_truck_cells(truck_cells)
                return

            # Multi-row / multi-column clipboard: paste starting at anchor (TSV layout).
            touched_rows: set = set()
            prev = self._table.blockSignals(True)
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
                    touched_rows.add(row)
                    if col in CHECK_COLS:
                        it = self._table.item(row, col) or QTableWidgetItem()
                        it.setData(Qt.UserRole, cell.strip() in ("1", "true", "True", "YES"))
                        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                        self._table.setItem(row, col, it)
                    elif col == COL_RECEIPT:
                        norm = _norm_receipt_text(cell)
                        it = self._table.item(row, col) or QTableWidgetItem()
                        it.setText(norm)
                        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                        self._table.setItem(row, col, it)
                    elif col == COL_TZS:
                        amt = _parse_amount_text(cell)
                        text = f"{amt:,.2f}" if cell.strip() else ""
                        it = QTableWidgetItem(text)
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        if amt < 0:
                            it.setForeground(NEG_COLOR)
                        self._table.setItem(row, col, it)
                    elif col == COL_TRUCK:
                        raw = cell.strip()
                        self._table.setItem(row, col, QTableWidgetItem(raw.upper() if raw else ""))
                        if raw:
                            truck_cells.append((row, raw))
                    else:
                        self._table.setItem(row, col, QTableWidgetItem(_upper_text(col, cell.strip())))
            for row in touched_rows:
                self._sync_row_date(row)
            self._table.blockSignals(prev)
            self._renumber()
            self._finalize_truck_cells(truck_cells)
        finally:
            self._bulk_mutating = False

    def _clear_selected(self) -> None:
        cleared_rows: set = set()
        self._table.blockSignals(True)
        for item in self._table.selectedItems():
            row = item.row()
            col = item.column()
            if row < self._saved_count or col in READONLY_COLS:
                continue
            cleared_rows.add(row)
            if col in CHECK_COLS:
                item.setData(Qt.UserRole, False)
            else:
                item.setText("")
        for row in cleared_rows:
            self._sync_row_date(row)
        self._table.blockSignals(False)
        self._renumber()

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
        truck_cells: list = []
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
                elif col == COL_TRUCK:
                    raw = src.text().strip()
                    self._table.setItem(row, col, QTableWidgetItem(raw.upper() if raw else ""))
                    if raw:
                        truck_cells.append((row, raw))
                else:
                    self._table.setItem(row, col, QTableWidgetItem(_upper_text(col, src.text())))
        for row in rows[1:]:
            if row >= self._saved_count:
                self._sync_row_date(row)
        self._table.blockSignals(False)
        self._renumber()
        self._finalize_truck_cells(truck_cells)

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
        for row in rows:
            if row >= self._saved_count:
                self._sync_row_date(row)
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
    # Import from Excel (daily MATUMIZI)
    # ------------------------------------------------------------------

    def import_from_file(self) -> None:
        asyncio.ensure_future(self._run_daily_import())

    async def _run_daily_import(self) -> None:
        from tahmeed.ui.cashier.daily_import_flow import run_daily_import_flow

        preview = await run_daily_import_flow(self)
        if preview is None:
            return
        await self.apply_daily_import_preview(preview)

    async def apply_daily_import_preview(self, preview) -> None:
        """Navigate to the Excel main date and stage rows for the user to Save."""
        from tahmeed.services.daily_import_service import staged_row_payload

        primary = preview.primary_date or self._current_date
        if primary != self._current_date:
            if self.has_unsaved_work():
                ok = await self.confirm_leave()
                if not ok:
                    return
            self._reset_edit_state()
            self._current_date = primary
            await self._load_date(primary)

        payloads = [staged_row_payload(row, preview) for row in preview.rows]
        # Queue truck issues now, but open the correction dialog only after this
        # async import task finishes — nested dialog.exec() + ensure_future crashes
        # under Python 3.14 / qasync.
        self._suppress_truck_dialog = True
        try:
            self._load_staged_import_rows(payloads)
            QMessageBox.information(
                self,
                "Import ready",
                f"Loaded {len(payloads):,} row(s) from \"{preview.source_filename}\" "
                f"onto {primary.strftime('%d/%m/%Y')}.\n\n"
                "Review the Table, make any edits, then click Save.\n"
                "Saved entries go to the accountant Verify inbox.",
            )
        finally:
            self._suppress_truck_dialog = False
        QTimer.singleShot(0, self._flush_truck_correction)

    def _kick_auto_fill_item(self, row: int, description: str) -> None:
        if self._bulk_mutating or not description.strip():
            return
        asyncio.ensure_future(self._auto_fill_item_from_mapping(row, description))

    def _load_staged_import_rows(self, payloads: list) -> None:
        if not payloads:
            return
        self._bulk_mutating = True
        start = self._first_empty_editable_row()
        prev = self._table.blockSignals(True)
        truck_cells: list = []
        loaded_rows: set = set()
        try:
            for r, data in enumerate(payloads):
                target = start + r
                if target >= self._table.rowCount():
                    self._append_editable_rows(max(20, len(payloads) - r + 5))
                if target < self._saved_count:
                    continue
                loaded_rows.add(target)
                self._pending_row_meta[target] = {
                    "daily_import_id": data.get("daily_import_id"),
                    "daily_import_source": data.get("daily_import_source"),
                    "date_discrepancy": data.get("date_discrepancy"),
                    "import_primary_date": data.get("import_primary_date"),
                    "category_id": data.get("category_id"),
                    "currency": data.get("currency") or "TZS",
                    "lpo_do": data.get("lpo_do") or "",
                    "do_number": data.get("do_number") or "",
                }

                dt = data.get("date")
                date_str = dt.strftime("%d/%m/%Y") if dt else ""
                self._table.setItem(target, COL_DATE, QTableWidgetItem(date_str))

                item_name = data.get("item") or data.get("category_name") or ""
                if item_name:
                    self._table.setItem(
                        target, COL_ITEM, QTableWidgetItem(_upper_text(COL_ITEM, item_name))
                    )

                desc = data.get("description") or ""
                self._table.setItem(
                    target, COL_DESC, QTableWidgetItem(_upper_text(COL_DESC, desc))
                )

                truck = data.get("truck_number") or ""
                if truck:
                    self._table.setItem(target, COL_TRUCK, QTableWidgetItem(truck.upper()))
                    truck_cells.append((target, truck))

                memo = data.get("memo") or ""
                if memo:
                    self._table.setItem(
                        target, COL_MEMO, QTableWidgetItem(_upper_text(COL_MEMO, memo))
                    )

                ref = data.get("ref_float") or ""
                if ref:
                    self._table.setItem(target, COL_REF, QTableWidgetItem(ref))

                amount = float(data.get("amount") or 0)
                tzs_it = QTableWidgetItem(f"{amount:,.2f}" if amount else "")
                tzs_it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if amount < 0:
                    tzs_it.setForeground(NEG_COLOR)
                self._table.setItem(target, COL_TZS, tzs_it)

                rcpt = data.get("receipt_status") or "pending"
                rcpt_it = QTableWidgetItem(
                    rcpt if rcpt in _VALID_RCPT else _norm_receipt_text(str(rcpt))
                )
                rcpt_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                self._table.setItem(target, COL_RECEIPT, rcpt_it)

                own = data.get("ownership") or ""
                if own:
                    self._table.setItem(
                        target, COL_OWN, QTableWidgetItem(_upper_text(COL_OWN, own))
                    )
                apr = data.get("approver") or ""
                if apr:
                    self._table.setItem(
                        target, COL_APR, QTableWidgetItem(_upper_text(COL_APR, apr))
                    )

            for row in loaded_rows:
                self._sync_row_date(row)
        finally:
            self._table.blockSignals(prev)
            self._bulk_mutating = False
        self._renumber()
        self._finalize_truck_cells(truck_cells)
        self._update_footer()

    def _load_rows(self, file_rows: List[List]) -> None:
        """Legacy positional loader kept for CSV paste-compat helpers."""
        if not file_rows:
            return
        data = file_rows[1:] if _is_header(file_rows[0]) else file_rows

        FILE_MAP = {
            COL_DATE:     1,
            COL_DESC:     3,
            COL_TRUCK:    4,
            COL_MEMO:     9,
            COL_REF:      10,
            COL_TZS:      11,
            COL_RECEIPT:  13,
            COL_OWN:      14,
            COL_APR:      15,
        }

        start = self._first_empty_editable_row()
        self._table.blockSignals(True)
        loaded_rows: set = set()
        truck_cells: list = []
        for r, row_data in enumerate(data):
            target = start + r
            if target >= self._table.rowCount():
                self._append_editable_rows(20)
            if target < self._saved_count:
                continue
            loaded_rows.add(target)

            for grid_col, file_col in FILE_MAP.items():
                if file_col >= len(row_data):
                    continue
                raw = str(row_data[file_col]).strip() if row_data[file_col] is not None else ""

                if grid_col == COL_RECEIPT:
                    norm = _norm_receipt_text(raw)
                    it = self._table.item(target, grid_col) or QTableWidgetItem()
                    it.setText(norm)
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    self._table.setItem(target, grid_col, it)
                elif grid_col == COL_REF:
                    low = raw.lower()
                    if low in ("1", "true", "yes", "refund to float") or (
                        "refund" in low and "float" in low
                    ):
                        text = "REFUND TO FLOAT"
                    elif raw and raw != "None":
                        text = raw
                    else:
                        text = ""
                    self._table.setItem(target, grid_col, QTableWidgetItem(text))
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
                elif grid_col == COL_TZS:
                    amt = _parse_amount_text(raw if raw != "None" else "")
                    if raw and raw != "None":
                        it = QTableWidgetItem(f"{amt:,.2f}")
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        if amt < 0:
                            it.setForeground(NEG_COLOR)
                        self._table.setItem(target, grid_col, it)
                elif grid_col == COL_TRUCK:
                    if raw and raw != "None":
                        self._table.setItem(target, grid_col, QTableWidgetItem(raw.upper()))
                        truck_cells.append((target, raw))
                else:
                    if raw and raw != "None":
                        self._table.setItem(
                            target, grid_col, QTableWidgetItem(_upper_text(grid_col, raw))
                        )

        for row in loaded_rows:
            self._sync_row_date(row)
        self._table.blockSignals(False)
        self._renumber()
        self._finalize_truck_cells(truck_cells)

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

    def has_unsaved_work(self) -> bool:
        """True when edit-mode dirty rows or typed-but-unsaved new rows exist."""
        if self._dirty_rows:
            return True
        for row in range(self._saved_count, self._table.rowCount()):
            if self._row_has_data(row):
                return True
        return False

    def _commit_open_editor(self) -> None:
        """Flush the active cell editor into the model before save/leave checks."""
        w = QApplication.focusWidget()
        if w is not None and self._table.isAncestorOf(w):
            self._table.commitData(w)
            self._table.closeEditor(w, QAbstractItemDelegate.NoHint)

    async def confirm_leave(self) -> bool:
        """Ask to save/discard before logout or app exit. False = stay put."""
        self._commit_open_editor()
        if not self.has_unsaved_work():
            return True
        resp = QMessageBox.question(
            self, "Unsaved changes",
            "You have unsaved entries in the Daily Register.\n"
            "Save them before leaving?",
            QMessageBox.Yes | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if resp == QMessageBox.Cancel:
            return False
        if resp == QMessageBox.Discard:
            return True
        # Yes — save; if the user cancels mid-save (duplicates / off-date), stay.
        return await self._do_save()

    async def _do_save(self) -> bool:
        """Persist dirty + new rows. Returns False if the user cancelled mid-save."""
        saved, updated, errors = 0, 0, []
        self._commit_open_editor()

        # ── Pass 1: commit edits to already-saved rows (UPDATE) ──────────
        for row in sorted(self._dirty_rows):
            tx_id = self._saved_ids.get(row)
            if tx_id is None:
                continue
            try:
                updates = self._updates_from_row(row)
            except ValueError as exc:
                errors.append(f"Row {row + 1}: {exc}")
                continue
            if updates is None:
                continue
            updates["last_edited_at"] = datetime.utcnow()
            updates["last_edited_by"] = self._user._id
            orig = self._saved_txs.get(row)
            try:
                if orig is not None and orig.verified:
                    # Leave the original in Master Expenses intact; insert a
                    # pending-edit document that the accountant reviews in the
                    # Edited tab. On re-approval the new values cascade to the
                    # original in-place.
                    await insert_pending_edit(tx_id, updates, self._user._id)
                elif orig is not None and getattr(orig, "rejected", False):
                    # Re-editing a rejected entry: clear the rejection so it
                    # returns to the accountant's New inbox tab.
                    updates["rejected"] = False
                    updates["rejection_reason"] = None
                    await update_transaction(tx_id, updates)
                else:
                    await update_transaction(tx_id, updates)
                updated += 1
            except Exception as exc:
                errors.append(f"Row {row + 1}: {exc}")

        # ── Pass 2: insert brand-new rows (INSERT) ───────────────────────
        try:
            dup_days = int(await get_setting("duplicate_check_days") or 5)
        except Exception:
            dup_days = 5

        # ── Pre-scan: warn once if any new rows carry a non-today date ──────
        _off_date = 0
        for _s in range(self._saved_count, self._table.rowCount()):
            if not self._row_has_data(_s):
                continue
            _it = self._table.item(_s, COL_DATE)
            _ds = _it.text().strip() if _it else ""
            try:
                _td = datetime.strptime(_ds, "%d/%m/%Y").date()
            except ValueError:
                _td = self._current_date
            if _td != date.today():
                _off_date += 1
        if _off_date:
            _plural = "s" if _off_date != 1 else ""
            _are    = "are" if _off_date != 1 else "is"
            if QMessageBox.warning(
                self, "Off-date Entries",
                f"{_off_date} row{_plural} {_are} not dated today "
                f"({date.today().strftime('%d %b %Y')}).\n\n"
                "These entries will be flagged in the accountant's verify inbox.\n\n"
                "Proceed with save?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            ) == QMessageBox.No:
                return False

        cancel_all = False
        for row in range(self._saved_count, self._table.rowCount()):
            if cancel_all:
                break
            if not self._row_has_data(row):
                continue

            def txt(col: int, _row: int = row) -> str:
                it = self._table.item(_row, col)
                return it.text().strip() if it else ""

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

                amount = _parse_amount_text(txt(COL_TZS))

                rcpt_status = _norm_receipt_text(txt(COL_RECEIPT))
                if rcpt_status not in _VALID_RCPT:
                    rcpt_status = "pending"

                item_name = txt(COL_ITEM)
                meta_pre = self._pending_row_meta.get(row) or {}
                allow_blank_item = self._defer_item_to_verify or bool(
                    meta_pre.get("daily_import_id")
                )
                if not item_name and not allow_blank_item:
                    errors.append(f"Row {row + 1}: Item is required.")
                    continue

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

                truck_raw = txt(COL_TRUCK)
                truck_number = ""
                if truck_raw:
                    if is_allowed_place_label(truck_raw, self._allowed_truck_labels):
                        truck_number = normalize_place_label(truck_raw)
                    else:
                        matched = try_match_fleet(truck_raw, self._fleet_numbers)
                        if matched is None:
                            norm = normalize_truck_number(
                                truck_raw, allowed_labels=self._allowed_truck_labels
                            )
                            label = norm.value if norm.status != "empty" else truck_raw
                            if norm.status == "invalid":
                                errors.append(
                                    f'Row {row + 1}: "{label}" is not a valid truck number '
                                    f"(expected T + number + space + suffix, e.g. T688 EAF)."
                                )
                                continue
                            if norm.status == "place_label":
                                truck_number = norm.value
                            else:
                                errors.append(
                                    f'Row {row + 1}: "{norm.value}" is not a registered truck/trailer.'
                                )
                                continue
                        else:
                            truck_number = matched
                    # Snap cell to canonical registry / label form
                    it_truck = self._table.item(row, COL_TRUCK)
                    if it_truck and it_truck.text() != truck_number:
                        self._table.blockSignals(True)
                        it_truck.setText(truck_number)
                        self._table.blockSignals(False)

                # ── Duplicate check ──────────────────────────────────────
                is_dup = False
                try:
                    dupes = await check_for_duplicates(
                        truck_number=truck_number,
                        amount=amount,
                        item=item_name,
                        description=description,
                        days=dup_days,
                    )
                except Exception:
                    dupes = []

                if dupes:
                    d = dupes[0]
                    dupe_info = (
                        f"Row {row + 1}  ·  {description or '—'}  ·  "
                        f"Truck {truck_number or '—'}  ·  TZS {amount:,.0f}\n\n"
                        f"A similar entry already exists:\n"
                        f"  Date: {d.date.strftime('%d %b %Y') if d.date else '—'}\n"
                        f"  Item: {d.item or '—'}\n"
                        f"  Description: {d.description or '—'}\n"
                        f"  Amount: TZS {d.amount:,.0f}\n"
                        f"  Truck: {d.truck_number or '—'}\n\n"
                        f"(Checked last {dup_days} day{'s' if dup_days != 1 else ''})"
                    )
                    msg = QMessageBox(self)
                    msg.setWindowTitle("Possible Duplicate Entry")
                    msg.setText(dupe_info)
                    msg.setIcon(QMessageBox.Warning)
                    save_btn   = msg.addButton("Save Anyway", QMessageBox.AcceptRole)
                    skip_btn   = msg.addButton("Skip Row",    QMessageBox.RejectRole)
                    cancel_btn = msg.addButton("Cancel Save", QMessageBox.DestructiveRole)
                    msg.exec()
                    clicked = msg.clickedButton()
                    if clicked is cancel_btn:
                        cancel_all = True
                        break
                    elif clicked is skip_btn:
                        continue
                    else:
                        is_dup = True   # "Save Anyway" — mark as duplicate

                ref_text = txt(COL_REF)
                meta = self._pending_row_meta.get(row) or {}
                tx = Transaction(
                    date=tx_date,
                    reported_date=_parse_optional_date(txt(COL_REPORTED)),
                    description=description,
                    item=item_name,
                    # The chosen item *is* the category — keep them in sync so the
                    # item's sidebar tab (which filters on category_name) shows it.
                    category_name=item_name or None,
                    category_id=meta.get("category_id"),
                    truck_number=truck_number,
                    amount=amount,
                    currency=meta.get("currency") or "TZS",
                    memo=txt(COL_MEMO),
                    receipt_status=rcpt_status,
                    ref_float=ref_text,
                    notes_flag=_is_refund_float(ref_text),
                    ownership=txt(COL_OWN),
                    approver=txt(COL_APR),
                    payee=txt(COL_PAYEE),
                    cheque=txt(COL_CHEQUE),
                    cashier_id=self._user._id,
                    possible_duplicate=is_dup,
                    daily_import_id=meta.get("daily_import_id"),
                    daily_import_source=meta.get("daily_import_source"),
                    date_discrepancy=bool(meta.get("date_discrepancy")),
                    import_primary_date=meta.get("import_primary_date"),
                    lpo_do=meta.get("lpo_do") or "",
                    do_number=meta.get("do_number") or "",
                )
                await save_transaction(tx)
                saved += 1
                self._pending_row_meta.pop(row, None)
            except Exception as exc:
                errors.append(f"Row {row + 1}: {exc}")

        if cancel_all:
            return False

        if errors:
            QMessageBox.warning(
                self, "Save — partial errors",
                f"{saved} added, {updated} updated.\n\nErrors:\n" + "\n".join(errors),
            )
        elif saved == 0 and updated == 0:
            QMessageBox.information(self, "Nothing to save", "No changes to save.")
            return True
        # else: clean save — reload silently, no popup

        self._reset_edit_state()
        self.rows_saved.emit(saved)
        await self._load_date(self._current_date)
        return True

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

    async def _load_cashier_settings(self) -> None:
        try:
            self._restrict_items = bool(await get_setting("restrict_items"))
        except Exception:
            self._restrict_items = False
        try:
            self._defer_item_to_verify = bool(await get_setting("defer_item_to_verify"))
        except Exception:
            self._defer_item_to_verify = False
        try:
            self._restrict_trucks = True
            # Persist intended default so accountant UI / other clients stay in sync.
            current = await get_setting("restrict_trucks")
            if current is not True:
                await set_setting("restrict_trucks", True)
        except Exception:
            self._restrict_trucks = True

    async def _auto_fill_item_from_mapping(self, row: int, description: str) -> None:
        """When description-only mode is on, pre-fill Item from saved mappings."""
        if not self._defer_item_to_verify or not description.strip():
            return
        item_it = self._table.item(row, COL_ITEM)
        if item_it and item_it.text().strip():
            return
        from tahmeed.services.description_mapping_service import resolve_category_for_description

        resolved = await resolve_category_for_description(description)
        if not resolved:
            return
        _, cat_name = resolved
        prev = self._table.blockSignals(True)
        if item_it is None:
            item_it = QTableWidgetItem(cat_name)
            item_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._table.setItem(row, COL_ITEM, item_it)
        else:
            item_it.setText(cat_name)
        self._table.blockSignals(prev)

    async def _load_fleet_numbers(self) -> None:
        try:
            self._fleet_numbers = await get_fleet_numbers()
        except Exception:
            self._fleet_numbers = set()
        try:
            raw = await get_setting("allowed_truck_labels")
            if isinstance(raw, list) and raw:
                self._allowed_truck_labels = merge_allowed_labels(raw, DEFAULT_PLACE_LABELS)
            else:
                self._allowed_truck_labels = set(DEFAULT_PLACE_LABELS)
        except Exception:
            self._allowed_truck_labels = set(DEFAULT_PLACE_LABELS)

    async def _remember_truck_labels(self, labels: list) -> None:
        if not labels:
            return
        try:
            merged = merge_allowed_labels(
                self._allowed_truck_labels, labels, DEFAULT_PLACE_LABELS
            )
            self._allowed_truck_labels = merged
            await set_setting("allowed_truck_labels", sorted(merged))
        except Exception:
            pass

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
            await create_cashier_category(
                data["name"], data["color"],
                data["requires_receipt"], data["requires_truck"],
                data.get("description", ""),
                icon=data.get("icon", "mdi.tag-outline"),
                sidebar_name=data.get("sidebar_name", ""),
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
    # Truck-column validation (format + restrict to truck/trailer registry)
    # ------------------------------------------------------------------

    def _can_add_fleet(self) -> bool:
        return getattr(self._user, "role", "") in ("admin", "accountant")

    def _set_truck_cell(self, row: int, value: str) -> None:
        it = self._table.item(row, COL_TRUCK)
        prev = self._table.blockSignals(True)
        if it is None:
            it = QTableWidgetItem(value)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._table.setItem(row, COL_TRUCK, it)
        else:
            it.setText(value)
        self._table.blockSignals(prev)

    def _resolve_truck_text(self, raw: str) -> tuple[str, Optional[str]]:
        """
        Return (status, value) where status is:
          'empty' | 'ok' | 'invalid_format' | 'not_in_registry'
        and value is the canonical / display string.
        """
        norm = normalize_truck_number(raw, allowed_labels=self._allowed_truck_labels)
        if norm.status == "empty":
            return "empty", ""
        if norm.status == "place_label":
            return "ok", norm.value
        if norm.status == "invalid":
            return "invalid_format", norm.value or raw.strip().upper()
        matched = try_match_fleet(norm.value, self._fleet_numbers)
        if matched is None:
            return "not_in_registry", norm.value
        return "ok", matched

    def _finalize_truck_cells(self, cells: list) -> None:
        """Normalize / validate truck cells; queue one combined correction dialog.

        ``cells`` is a list of (row, raw_text) for truck values just written.
        """
        if not cells:
            return

        for row, raw in cells:
            status, value = self._resolve_truck_text(raw)
            if status == "empty":
                self._set_truck_cell(row, "")
                self._pending_truck_issues.pop(row, None)
            elif status == "ok":
                self._set_truck_cell(row, value)
                self._pending_truck_issues.pop(row, None)
            elif status == "invalid_format":
                self._set_truck_cell(row, value)
                self._pending_truck_issues[row] = TruckIssue(
                    row=row, original=raw, kind="invalid_format"
                )
            else:
                self._set_truck_cell(row, value)
                self._pending_truck_issues[row] = TruckIssue(
                    row=row, original=raw, kind="not_in_registry"
                )

        self._schedule_truck_correction()

    def _schedule_truck_correction(self) -> None:
        """Debounce so paste/import opens one combined dialog, not one per truck."""
        if not self._pending_truck_issues:
            return
        if self._suppress_truck_dialog:
            # Issues stay queued; caller flushes after the enclosing async work.
            return
        # If a dialog is already open, push new issues into it immediately.
        dlg = self._open_truck_dialog
        if dlg is not None and getattr(dlg, "isVisible", lambda: False)():
            batch = list(self._pending_truck_issues.values())
            self._pending_truck_issues.clear()
            try:
                dlg.add_issues(batch)
            except Exception:
                for issue in batch:
                    self._pending_truck_issues[issue.row] = issue
            return
        if self._truck_dialog_scheduled:
            return
        self._truck_dialog_scheduled = True
        QTimer.singleShot(0, self._flush_truck_correction)

    def _flush_truck_correction(self) -> None:
        self._truck_dialog_scheduled = False
        if not self._pending_truck_issues:
            return
        batch = list(self._pending_truck_issues.values())
        self._pending_truck_issues.clear()
        self._show_truck_correction(batch)

    def _show_truck_correction(self, issues: list) -> None:
        # Drop issues whose cell was cleared/changed already
        live: list[TruckIssue] = []
        for issue in issues:
            it = self._table.item(issue.row, COL_TRUCK)
            current = (it.text().strip() if it else "")
            if not current:
                continue
            status, value = self._resolve_truck_text(current)
            if status == "ok":
                self._set_truck_cell(issue.row, value)
                continue
            issue.kind = "invalid_format" if status == "invalid_format" else "not_in_registry"
            issue.original = current
            live.append(issue)
        if not live:
            return

        # Merge with any dialog already open (should be rare after schedule coalesce)
        dlg = self._open_truck_dialog
        if dlg is not None and getattr(dlg, "isVisible", lambda: False)():
            dlg.add_issues(live)
            return

        dlg = TruckCorrectionDialog(
            live,
            self._fleet_numbers,
            can_add=self._can_add_fleet(),
            allowed_labels=self._allowed_truck_labels,
            on_resolved=self._on_truck_issue_resolved_live,
            parent=self,
        )
        self._open_truck_dialog = dlg
        result = dlg.exec()
        self._open_truck_dialog = None

        pending_adds = list(getattr(dlg, "pending_registry_adds", None) or [])
        if pending_adds:
            asyncio.ensure_future(self._persist_truck_registry_adds(pending_adds))
        # Live callback already wrote resolved rows to the grid. On cancel,
        # remaining unresolved issues still need clearing.
        if getattr(dlg, "new_labels", None):
            asyncio.ensure_future(self._remember_truck_labels(dlg.new_labels))
        asyncio.ensure_future(self._load_fleet_numbers())
        if result != QDialog.Accepted:
            resolved_rows = {i.row for i in dlg.issues}
            for issue in live:
                if issue.row not in resolved_rows:
                    self._set_truck_cell(issue.row, "")

    async def _persist_truck_registry_adds(self, adds: list) -> None:
        from tahmeed.services.truck_service import add_trailer, add_truck

        for kind, number in adds:
            try:
                if kind == "trucks":
                    await add_truck(number)
                else:
                    await add_trailer(number)
                self._fleet_numbers.add(number)
            except Exception as exc:
                QMessageBox.critical(
                    self, "Error", f"Failed to add {number} to registry:\n{exc}"
                )
        await self._load_fleet_numbers()

    def _on_truck_issue_resolved_live(self, issue: TruckIssue) -> None:
        """Apply one resolved truck to the grid as soon as it leaves the dialog list."""
        if issue.skip or not issue.corrected:
            self._set_truck_cell(issue.row, "")
            return
        if getattr(issue, "is_place_label", False):
            self._allowed_truck_labels.add(normalize_place_label(issue.corrected))
        else:
            self._fleet_numbers.add(issue.corrected)
        self._set_truck_cell(issue.row, issue.corrected)

    def _validate_truck_cell(self, row: int, item: QTableWidgetItem) -> None:
        raw = item.text().strip()
        if not raw:
            return
        status, value = self._resolve_truck_text(raw)
        if status == "ok":
            if item.text() != value:
                prev = self._table.blockSignals(True)
                item.setText(value)
                self._table.blockSignals(prev)
            self._pending_truck_issues.pop(row, None)
            return
        if status == "empty":
            return
        kind = "invalid_format" if status == "invalid_format" else "not_in_registry"
        if item.text() != value:
            prev = self._table.blockSignals(True)
            item.setText(value)
            self._table.blockSignals(prev)
        self._pending_truck_issues[row] = TruckIssue(
            row=row, original=value or raw, kind=kind
        )
        self._schedule_truck_correction()

    def _reject_truck(self, row: int, number: str) -> None:
        # Kept for compatibility; correction dialog handles messaging.
        it = self._table.item(row, COL_TRUCK)
        if it is None or it.text().strip().upper() != number.upper():
            return
        self._pending_truck_issues[row] = TruckIssue(
            row=row, original=number, kind="not_in_registry"
        )
        self._schedule_truck_correction()

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
