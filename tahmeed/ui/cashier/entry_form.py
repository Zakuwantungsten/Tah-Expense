import asyncio
from datetime import datetime, date
from typing import List, Optional
from bson import ObjectId

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QDateEdit, QDoubleSpinBox, QComboBox, QCheckBox,
    QPushButton, QLabel, QMessageBox, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QStyledItemDelegate, QSizePolicy, QSplitter,
)
from PySide6.QtCore import Qt, QTimer, QDate, Signal, QSize
from PySide6.QtGui import QColor, QBrush, QPainter

from tahmeed.models.transaction import Transaction
from tahmeed.models.category import Category
from tahmeed.models.user import User
from tahmeed.services.truck_service import search_trucks
from tahmeed.services.rule_service import test_description
from tahmeed.services.category_service import get_all_categories
from tahmeed.services.settings_service import get_setting
from tahmeed.services.cashier_service import save_transaction, get_transactions_by_date
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit


# ─────────────────────────────────────────────────────────────────────────────
# Styles
# ─────────────────────────────────────────────────────────────────────────────

_CARD = "background:#ffffff; border:1px solid #e5e7eb; border-radius:8px;"

_LBL  = "color:#374151; font-size:12px; font-weight:500; margin-bottom:2px;"

# Single consistent height for EVERY input field in the form
_INPUT = """
QLineEdit, QDateEdit, QComboBox, QDoubleSpinBox {
    border: 1px solid #d1d5db;
    border-radius: 6px;
    padding: 0 10px;
    color: #111827;
    background: #ffffff;
    font-size: 13px;
    min-height: 34px;
    max-height: 34px;
}
QLineEdit:focus, QDateEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
    border-color: #E85D04;
}
QComboBox::drop-down  { border: none; width: 24px; }
QComboBox::down-arrow { width: 10px; height: 10px; }
QDateEdit::drop-down  { border: none; width: 24px; }
"""

_BTN_SECONDARY = """
QPushButton {
    background:#ffffff; border:1px solid #d1d5db; border-radius:6px;
    padding:6px 20px; color:#374151; font-size:13px;
}
QPushButton:hover   { background:#f9fafb; }
QPushButton:pressed { background:#e5e7eb; }
"""

_BTN_PRIMARY = """
QPushButton {
    background:#E85D04; border:1px solid #F48C06; border-radius:6px;
    padding:6px 20px; color:#ffffff; font-size:13px; font-weight:600;
}
QPushButton:hover    { background:#F48C06; }
QPushButton:pressed  { background:#DC2F02; }
QPushButton:disabled { background:#fdba74; border-color:#fdba74; }
"""

_TABLE_STYLE = """
QTableWidget {
    background:#ffffff; gridline-color:#e5e7eb; border:none;
    selection-background-color:#fff3e8; selection-color:#111827;
}
QHeaderView::section {
    background:#f1f5f9; color:#334155; font-weight:600; font-size:11px;
    padding:5px 8px; border:none;
    border-right:1px solid #cbd5e1; border-bottom:2px solid #cbd5e1;
}
QTableWidget::item          { padding:2px 6px; color:#111827; font-size:12px; }
QTableWidget::item:selected { color:#111827; }
"""

# Register column indices (identical to excel_grid.py)
_SNO, _DATE, _ITEM, _DESC, _TRUCK = 0, 1, 2, 3, 4
_MEMO, _NOTES                     = 5, 6
_TZS, _RCPT, _APR                 = 7, 8, 9

_HEADERS = [
    "S/NO", "Date", "Item", "Description", "Truck No.",
    "Memo", "Ref_Float", "TZS", "Receipt", "APR BY",
]

_SAVED_BG = QColor("#fff8f0")
_SNO_BG   = QColor("#f1f5f9")
_NEG_COL  = QColor("#dc2626")

_RCPT_COLORS = {
    "received": ("#dcfce7", "#16a34a"),
    "pending":  ("#fff7ed", "#ea580c"),
    "missing":  ("#fef2f2", "#dc2626"),
}
_RCPT_LABEL = {"received": "Received", "pending": "Pending", "missing": "Missing"}


# ─────────────────────────────────────────────────────────────────────────────
# Receipt badge delegate
# ─────────────────────────────────────────────────────────────────────────────

class _ReceiptDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        status = index.data(Qt.UserRole) or "pending"
        label  = _RCPT_LABEL.get(status, status.capitalize())
        bg, fg = _RCPT_COLORS.get(status, ("#f3f4f6", "#6b7280"))
        rect   = option.rect.adjusted(6, 5, -6, -5)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(bg))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, rect.height() // 2, rect.height() // 2)
        painter.setPen(QColor(fg))
        f = painter.font(); f.setPointSize(9); f.setBold(True)
        painter.setFont(f)
        painter.drawText(rect, Qt.AlignCenter, label)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(90, 28)


# ─────────────────────────────────────────────────────────────────────────────
# EntryForm
# ─────────────────────────────────────────────────────────────────────────────

class EntryForm(QWidget):
    """
    Form view — day's transaction table (all register columns) +
    Add New Transaction form.

    Form layout (3 equal columns, every input the same height):

      Left              Middle                    Right
      ─────             ──────────────────────    ─────────────────
      Date              Category  (autocomplete)  Amount (TZS)
      Item              hint label                Receipt ▼
      Description       Memo                      Notes ☐
      Truck No.                                   Ownership
                                                  APR BY

    • Category is a live-search autocomplete (type to filter categories).
    • Description auto-triggers category detection in the background.
    • The table scrolls to the bottom after every save.
    """

    transaction_saved = Signal(object)

    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        self._user        = user
        self._categories: List[Category] = []
        self._confidence_threshold: int  = 75
        self._rcpt_delegate = _ReceiptDelegate()

        self._build_ui()

        self._cat_timer = QTimer(self)
        self._cat_timer.setSingleShot(True)
        self._cat_timer.setInterval(450)
        self._cat_timer.timeout.connect(self._trigger_categorize)

        asyncio.ensure_future(self._load_config())

    # ──────────────────────────────────────────────────────────────────
    # Config / categories
    # ──────────────────────────────────────────────────────────────────

    async def _load_config(self) -> None:
        try:
            self._confidence_threshold = int(
                await get_setting("confidence_threshold") or 75
            )
            self._categories = await get_all_categories()
        except Exception:
            pass
        await self._refresh_table()

    def update_categories(self, cats: List[Category]) -> None:
        self._categories = cats

    async def _fetch_cat_names(self, prefix: str) -> List[str]:
        """Autocomplete fetch_fn for the Category TruckLineEdit."""
        pl = prefix.strip().lower()
        if not pl:
            return [c.name for c in self._categories[:12]]
        return [c.name for c in self._categories if pl in c.name.lower()][:12]

    # ──────────────────────────────────────────────────────────────────
    # UI construction
    # ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet("EntryForm { background:#f3f4f6; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(7)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #d1d5db; border-radius: 2px; }"
        )
        splitter.addWidget(self._build_table_card())
        splitter.addWidget(self._build_form_card())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        outer.addWidget(splitter)

        # Reload table when date picker changes
        self._date.dateChanged.connect(
            lambda _: asyncio.ensure_future(self._refresh_table())
        )
        self._update_day_label()

    # ── Transactions table card ───────────────────────────────────────

    def _build_table_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("tc")
        card.setStyleSheet(f"#tc {{ {_CARD} }}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Header bar
        hdr_w = QWidget()
        hdr_w.setStyleSheet("background:transparent;")
        hdr_l = QHBoxLayout(hdr_w)
        hdr_l.setContentsMargins(16, 12, 16, 10)
        hdr_l.setSpacing(8)

        self._day_label = QLabel()
        self._day_label.setStyleSheet("font-size:14px; font-weight:600; color:#111827;")
        hdr_l.addWidget(self._day_label)
        hdr_l.addStretch()

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color:#6b7280; font-size:12px;")
        hdr_l.addWidget(self._count_label)

        lay.addWidget(hdr_w)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#e5e7eb;")
        lay.addWidget(sep)

        # Table — all 14 register columns
        self._day_table = QTableWidget(0, len(_HEADERS))
        self._day_table.setHorizontalHeaderLabels(_HEADERS)
        self._day_table.setStyleSheet(_TABLE_STYLE)
        self._day_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._day_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._day_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._day_table.verticalHeader().setVisible(False)
        self._day_table.verticalHeader().setDefaultSectionSize(28)
        self._day_table.setSortingEnabled(False)
        self._day_table.setItemDelegateForColumn(_RCPT, self._rcpt_delegate)

        hh = self._day_table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(_DESC, QHeaderView.Stretch)

        self._day_table.setColumnWidth(_SNO,   38)
        self._day_table.setColumnWidth(_DATE,  95)
        self._day_table.setColumnWidth(_ITEM,  100)
        self._day_table.setColumnWidth(_TRUCK, 90)
        self._day_table.setColumnWidth(_MEMO,  120)
        self._day_table.setColumnWidth(_NOTES, 52)
        self._day_table.setColumnWidth(_TZS,   110)
        self._day_table.setColumnWidth(_RCPT,  90)
        self._day_table.setColumnWidth(_APR,   75)

        lay.addWidget(self._day_table)
        return card

    # ── Form card ─────────────────────────────────────────────────────

    def _build_form_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("fc")
        card.setStyleSheet(f"#fc {{ {_CARD} }}")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        card.setMinimumHeight(260)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 14, 20, 16)
        lay.setSpacing(10)

        title = QLabel("Add New Transaction")
        title.setStyleSheet("font-size:14px; font-weight:600; color:#111827;")
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#f3f4f6;")
        lay.addWidget(sep)

        cols = QHBoxLayout()
        cols.setSpacing(20)
        cols.setContentsMargins(0, 0, 0, 0)
        cols.addWidget(self._build_left_col(),  stretch=1)
        cols.addWidget(self._build_mid_col(),   stretch=1)
        cols.addWidget(self._build_right_col(), stretch=1)
        lay.addLayout(cols)

        # Tab navigates across columns row-by-row (not down each column)
        QWidget.setTabOrder(self._date,        self._cat_input)
        QWidget.setTabOrder(self._cat_input,   self._amount)
        QWidget.setTabOrder(self._amount,      self._item)
        QWidget.setTabOrder(self._item,        self._memo)
        QWidget.setTabOrder(self._memo,        self._receipt)
        QWidget.setTabOrder(self._receipt,     self._description)
        QWidget.setTabOrder(self._description, self._notes)
        QWidget.setTabOrder(self._notes,       self._truck)
        QWidget.setTabOrder(self._truck,       self._approver)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setStyleSheet(_BTN_SECONDARY)
        clear_btn.setFixedWidth(90)
        clear_btn.clicked.connect(self._clear_form)
        self._submit_btn = QPushButton("Add Entry")
        self._submit_btn.setStyleSheet(_BTN_PRIMARY)
        self._submit_btn.setFixedWidth(110)
        self._submit_btn.setDefault(True)
        self._submit_btn.clicked.connect(self._on_submit)
        btn_row.addWidget(clear_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(self._submit_btn)
        lay.addLayout(btn_row)

        return card

    # ── Left column: Date → Item → Description → Truck No. ────────────

    def _build_left_col(self) -> QWidget:
        w, lay = _col()

        lay.addWidget(_lbl("Date"))
        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDate(QDate.currentDate())
        self._date.setDisplayFormat("dd/MM/yyyy")
        self._date.setStyleSheet(_INPUT)
        lay.addWidget(self._date)

        lay.addSpacing(10)
        lay.addWidget(_lbl("Item"))
        self._item = QLineEdit()
        self._item.setPlaceholderText("e.g., Fuel, Parts, Tolls")
        self._item.setStyleSheet(_INPUT)
        lay.addWidget(self._item)

        lay.addSpacing(10)
        lay.addWidget(_lbl("Description"))
        self._description = QLineEdit()
        self._description.setPlaceholderText("e.g., KAPIRI COUNCIL GOING")
        self._description.setStyleSheet(_INPUT)
        self._description.textEdited.connect(self._on_description_edited)
        lay.addWidget(self._description)

        lay.addSpacing(10)
        lay.addWidget(_lbl("Truck No."))
        self._truck = TruckLineEdit(fetch_fn=search_trucks)
        self._truck.setPlaceholderText("e.g., T688 EAF")
        self._truck.setStyleSheet(_INPUT)
        lay.addWidget(self._truck)

        lay.addStretch()
        return w

    # ── Middle column: Category → Memo ────────────────────────────────

    def _build_mid_col(self) -> QWidget:
        w, lay = _col()

        lay.addWidget(_lbl("Category"))
        self._cat_input = TruckLineEdit(fetch_fn=self._fetch_cat_names)
        self._cat_input.setPlaceholderText("Type to search categories…")
        self._cat_input.setStyleSheet(_INPUT)
        lay.addWidget(self._cat_input)

        self._cat_hint = QLabel("Auto-detect — start typing a description.")
        self._cat_hint.setStyleSheet(
            "color:#9ca3af; font-size:11px; font-style:italic; padding:1px 0 6px 0;"
        )
        self._cat_hint.setWordWrap(True)
        lay.addWidget(self._cat_hint)

        lay.addWidget(_lbl("Memo"))
        self._memo = QLineEdit()
        self._memo.setPlaceholderText("Optional note")
        self._memo.setStyleSheet(_INPUT)
        lay.addWidget(self._memo)

        lay.addStretch()
        return w

    # ── Right column: Amount → Receipt → Notes → Ownership → APR BY ───

    def _build_right_col(self) -> QWidget:
        w, lay = _col()

        lay.addWidget(_lbl("Amount (TZS)"))
        self._amount = QDoubleSpinBox()
        self._amount.setRange(0, 99_999_999)
        self._amount.setDecimals(2)
        self._amount.setGroupSeparatorShown(True)
        self._amount.setStyleSheet(_INPUT)
        lay.addWidget(self._amount)

        lay.addSpacing(10)
        lay.addWidget(_lbl("Receipt"))
        self._receipt = QComboBox()
        self._receipt.addItems(["Pending", "Received", "Missing"])
        self._receipt.setStyleSheet(_INPUT)
        lay.addWidget(self._receipt)

        lay.addSpacing(10)
        lay.addWidget(_lbl("Notes"))
        self._notes = QCheckBox("Flag this entry")
        self._notes.setStyleSheet("color:#374151; font-size:13px;")
        lay.addWidget(self._notes)

        lay.addSpacing(10)
        lay.addWidget(_lbl("Ownership"))
        self._ownership = QLineEdit()
        self._ownership.setPlaceholderText("Owner / department")
        self._ownership.setStyleSheet(_INPUT)
        lay.addWidget(self._ownership)

        lay.addSpacing(10)
        lay.addWidget(_lbl("APR BY"))
        self._approver = QLineEdit()
        self._approver.setPlaceholderText("Approver name")
        self._approver.setStyleSheet(_INPUT)
        lay.addWidget(self._approver)

        lay.addStretch()
        return w

    # ──────────────────────────────────────────────────────────────────
    # Day transactions table
    # ──────────────────────────────────────────────────────────────────

    def _selected_date(self) -> date:
        qd = self._date.date()
        return date(qd.year(), qd.month(), qd.day())

    def _update_day_label(self) -> None:
        d = self._selected_date()
        self._day_label.setText(
            "Transactions — " + datetime(d.year, d.month, d.day).strftime("%A, %d %B %Y")
        )

    async def _refresh_table(self) -> None:
        self._update_day_label()
        try:
            txs = await get_transactions_by_date(self._selected_date(), cashier_id=self._user._id)
            self._fill_table(txs)
        except Exception:
            pass

    def _fill_table(self, txs: List[Transaction]) -> None:
        self._day_table.setRowCount(0)
        self._day_table.setRowCount(len(txs))

        for row, tx in enumerate(txs):
            bg = QBrush(_SAVED_BG)

            def _it(text: str, align=Qt.AlignVCenter | Qt.AlignLeft) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setBackground(bg)
                it.setTextAlignment(align)
                return it

            sno = _it(str(row + 1), Qt.AlignCenter)
            sno.setBackground(QBrush(_SNO_BG))
            self._day_table.setItem(row, _SNO, sno)

            self._day_table.setItem(row, _DATE,  _it(tx.date.strftime("%d/%m/%Y") if tx.date else ""))
            self._day_table.setItem(row, _ITEM,  _it(tx.item or ""))
            self._day_table.setItem(row, _DESC,  _it(tx.description or ""))
            self._day_table.setItem(row, _TRUCK, _it(tx.truck_number or ""))
            self._day_table.setItem(row, _MEMO,  _it(tx.memo or ""))

            notes_it = _it("✓" if tx.notes_flag else "", Qt.AlignCenter)
            if tx.notes_flag:
                notes_it.setForeground(QColor("#E85D04"))
            self._day_table.setItem(row, _NOTES, notes_it)

            tzs_str = f"{tx.amount:,.2f}" if tx.amount else ""
            tzs_it  = _it(tzs_str, Qt.AlignRight | Qt.AlignVCenter)
            if tx.amount and tx.amount < 0:
                tzs_it.setForeground(_NEG_COL)
            self._day_table.setItem(row, _TZS, tzs_it)

            rcpt_it = QTableWidgetItem("")
            rcpt_it.setData(Qt.UserRole, tx.receipt_status)
            rcpt_it.setBackground(bg)
            rcpt_it.setTextAlignment(Qt.AlignCenter)
            self._day_table.setItem(row, _RCPT, rcpt_it)

            self._day_table.setItem(row, _OWN, _it(tx.ownership or ""))
            self._day_table.setItem(row, _APR, _it(tx.approver or ""))

        n = len(txs)
        self._count_label.setText(f"{n} entr{'y' if n == 1 else 'ies'}")
        if n > 0:
            self._day_table.scrollToBottom()

    # ──────────────────────────────────────────────────────────────────
    # Category auto-detection (triggered by description typing)
    # ──────────────────────────────────────────────────────────────────

    def _on_description_edited(self, text: str) -> None:
        self._cat_timer.stop()
        if len(text.strip()) >= 3:
            self._cat_timer.start()
        else:
            self._set_cat_waiting()

    def _trigger_categorize(self) -> None:
        asyncio.ensure_future(self._categorize(self._description.text().strip()))

    async def _categorize(self, description: str) -> None:
        try:
            result = await test_description(description)
            if result:
                cat_name, cat_id, confidence = result
                if int(confidence * 100) >= self._confidence_threshold:
                    self._set_cat_matched(cat_name, confidence)
                    return
            self._set_cat_needs_selection()
        except Exception:
            pass

    def _set_cat_waiting(self) -> None:
        self._cat_hint.setStyleSheet(
            "color:#9ca3af; font-size:11px; font-style:italic; padding:1px 0 6px 0;"
        )
        self._cat_hint.setText("Auto-detect — start typing a description.")

    def _set_cat_matched(self, cat_name: str, confidence: float) -> None:
        # Auto-fill the typeable category field without triggering autocomplete
        self._cat_input.blockSignals(True)
        self._cat_input.setText(cat_name)
        self._cat_input.blockSignals(False)
        self._cat_hint.setStyleSheet(
            "color:#16a34a; font-size:11px; font-weight:500; padding:1px 0 6px 0;"
        )
        self._cat_hint.setText(f"✓  Auto-detected ({int(confidence * 100)}% confidence)")

    def _set_cat_needs_selection(self) -> None:
        self._cat_hint.setStyleSheet(
            "color:#ea580c; font-size:11px; font-weight:500; padding:1px 0 6px 0;"
        )
        self._cat_hint.setText("⚠  No match — type or select a category above.")

    def _resolve_category(self) -> Optional[tuple]:
        """Return (cat_name, cat_id) from the typed category name, or None."""
        typed = self._cat_input.text().strip()
        if not typed:
            return None
        typed_lower = typed.lower()
        for cat in self._categories:
            if cat.name.lower() == typed_lower:
                return cat.name, cat._id
        # Partial match fallback
        for cat in self._categories:
            if typed_lower in cat.name.lower():
                return cat.name, cat._id
        return None

    # ──────────────────────────────────────────────────────────────────
    # Submit
    # ──────────────────────────────────────────────────────────────────

    def _on_submit(self) -> None:
        asyncio.ensure_future(self._do_submit())

    async def _do_submit(self) -> None:
        self._submit_btn.setEnabled(False)

        description = self._description.text().strip()
        if not description:
            QMessageBox.warning(self, "Missing field", "Description is required.")
            self._submit_btn.setEnabled(True)
            return

        cat_result = self._resolve_category()
        if cat_result is None:
            QMessageBox.warning(
                self, "No category",
                "Category not recognised. Type a category name or pick one from the dropdown.",
            )
            self._submit_btn.setEnabled(True)
            return

        cat_name, cat_id = cat_result
        qdate   = self._date.date()
        tx_date = datetime(qdate.year(), qdate.month(), qdate.day())

        tx = Transaction(
            date=tx_date,
            description=description,
            item=self._item.text().strip(),
            truck_number=self._truck.text().strip().upper(),
            amount=self._amount.value(),
            currency="TZS",
            category_id=cat_id,
            category_name=cat_name,
            memo=self._memo.text().strip(),
            receipt_status=self._receipt.currentText().lower(),
            notes_flag=self._notes.isChecked(),
            ownership=self._ownership.text().strip(),
            approver=self._approver.text().strip(),
            cashier_id=self._user._id,
        )

        try:
            saved = await save_transaction(tx)
            self.transaction_saved.emit(saved)
            self._clear_form()
            await self._refresh_table()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{exc}")

        self._submit_btn.setEnabled(True)

    # ──────────────────────────────────────────────────────────────────
    # Clear
    # ──────────────────────────────────────────────────────────────────

    def _clear_form(self) -> None:
        self._item.clear()
        self._description.clear()
        self._truck.clear()
        self._amount.setValue(0)
        self._memo.clear()
        self._receipt.setCurrentIndex(0)
        self._notes.setChecked(False)
        self._ownership.clear()
        self._approver.clear()
        self._cat_input.clear()
        self._set_cat_waiting()
        self._description.setFocus()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _col():
    """Return (QWidget, QVBoxLayout) for a form column."""
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    return w, lay


def _lbl(text: str) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(_LBL)
    return l
