"""Shared DailyRegister column constants, colors, and cell delegates."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtWidgets import (
    QApplication, QAbstractItemDelegate, QStyledItemDelegate, QStyleOptionViewItem,
    QDateEdit, QLineEdit, QStyle,
)
from PySide6.QtCore import Qt, QDate, QEvent, QSize, QTimer
from PySide6.QtGui import QColor, QBrush, QPen, QPainter

from tahmeed.services.truck_format import normalize_truck_number
from tahmeed.services.cashier_service import (
    search_descriptions,
    search_descriptions_sync,
)
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit
from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit, accept_completion
from tahmeed.ui.accountant.date_filters import style_calendar_popup

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
COL_CASHIER  = 14

HEADERS = [
    "S/NO", "Date", "Reported Date", "Item", "Description", "Truck No.",
    "Memo", "Ref_Float", "TZS", "Receipt", "Ownership", "APR BY",
    "Payee", "Cheque", "Cashier",
]

CHECK_COLS       = set()   # legacy; Ref_Float is free text now
READONLY_COLS    = {COL_SNO, COL_CASHIER}
# Date / reported date alone do not count as entry data.
_DATA_SKIP_COLS  = READONLY_COLS | {COL_RECEIPT, COL_DATE, COL_REPORTED}
# Columns that should NOT be auto-uppercased
_UPPER_SKIP_COLS = READONLY_COLS | {COL_RECEIPT, COL_DATE, COL_REPORTED}
DEFAULT_EDITABLE_ROWS = 20

_REF_FLOAT_OPTS = ["REFUND TO FLOAT"]

# Preferred column widths — Description stretches to fill leftover viewport space.
_COL_PREFERRED = {
    COL_SNO: 48,
    COL_DATE: 70,
    COL_REPORTED: 70,
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
    COL_CASHIER: 100,
}
# Columns that shrink first when the viewport is tighter than the preferred sum.
_COL_FLEX = (
    COL_DESC, COL_MEMO, COL_PAYEE, COL_ITEM, COL_REF,
    COL_OWN, COL_APR, COL_CHEQUE, COL_RECEIPT, COL_CASHIER,
)
_COL_MIN = {
    COL_SNO: 40,
    COL_DATE: 62,
    COL_REPORTED: 62,
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
    COL_CASHIER: 70,
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


def format_register_date(value) -> str:
    """Short register date for Date / Reported Date cells: ``18 Jul``."""
    if value is None:
        return ""
    if hasattr(value, "date") and not isinstance(value, date):
        try:
            value = value.date()
        except Exception:
            pass
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return ""
    return value.strftime("%d %b")


def _parse_optional_date(text: str, default_year: int = None):
    """Parse short ``18 Jul`` / ``18 Jul 2026`` / legacy ``dd/MM/yyyy`` → datetime."""
    raw = (text or "").strip()
    if not raw:
        return None
    year = default_year or date.today().year
    for fmt in ("%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%d %b", "%d %B"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt in ("%d %b", "%d %B"):
                dt = dt.replace(year=year)
            return dt
        except ValueError:
            continue
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
      - cut marquee         → dashed QB-blue border (Excel cut)
    All specialised delegates inherit from this.
    """
    _ACTIVE_PEN  = QColor("#0077C5")
    _SELECT_FILL = QColor("#cde0f5")
    _CUT_PEN     = QColor("#0077C5")

    def _is_current(self, index) -> bool:
        t = self.parent()
        return t is not None and t.currentIndex() == index

    def _is_cut_cell(self, index) -> bool:
        table = self.parent()
        if table is None:
            return False
        owner = getattr(table, "_grid_owner", None)
        cut = getattr(owner, "_cut_cells", None) if owner is not None else None
        if not cut:
            return False
        return (index.row(), index.column()) in cut

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
        """Draw thick current-cell border and/or Excel cut marquee."""
        painter.save()
        if self._is_cut_cell(index):
            pen = QPen(self._CUT_PEN, 1.5, Qt.DashLine)
            pen.setJoinStyle(Qt.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
        elif self._is_current(index) and bool(option.state & QStyle.State_Selected):
            pen = QPen(self._ACTIVE_PEN, 2)
            pen.setJoinStyle(Qt.MiterJoin)
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
                    reg = getattr(table, "_grid_owner", None) or table.parent()
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
                    reg = getattr(table, "_grid_owner", None) or table.parent()
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
        # System-wide description history (all days) with Excel inline preview + Tab.
        ed = TruckLineEdit(
            fetch_fn=search_descriptions,
            sync_fn=search_descriptions_sync,
            parent=parent,
        )
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
            suggestion = format_register_date(cur)
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
        ed.setDisplayFormat("d MMM")
        ed.lineEdit().setReadOnly(True)  # calendar-only — no manual typing
        ed.setStyleSheet(
            "QDateEdit { color: #111827; background: #ffffff; }"
            "QDateEdit::drop-down { width: 20px; }"
        )
        style_calendar_popup(ed)
        return ed

    def setEditorData(self, editor, index):
        text = index.data() or ""
        cur = self._get_current_date()
        year = cur.year if cur is not None else date.today().year
        dt = _parse_optional_date(text, default_year=year)
        if dt is not None:
            editor.setDate(QDate(dt.year, dt.month, dt.day))
        else:
            editor.setDate(QDate(cur.year, cur.month, cur.day))

    def setModelData(self, editor, model, index):
        model.setData(index, format_register_date(editor.date().toPython()))

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
    if not key:
        return ""
    if key in _RCPT_NORM:
        return _RCPT_NORM[key]
    if "no receipt" in key:
        return "no_receipt"
    if key == "receipt" or "received" in key:
        return "received"
    return "pending"


def _receipt_paste_value(raw: str) -> str:
    """Preserve clipboard receipt as-is when valid; do not invent pending for blank."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    norm = _norm_receipt_text(raw)
    return norm if norm else raw


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
