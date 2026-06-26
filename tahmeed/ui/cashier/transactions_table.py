"""
TransactionBrowser — QuickBooks-style "Find" dialog.

Two search modes (pill-toggle in filter panel):
  Simple   — keyword in description + date range
  Advanced — keyword + choose item (category) → sub-item + date range

Results show one row per calendar day with aggregated totals:
  Date | Transaction ID | Entries | Refund to Float | Total Amount

Double-click or "Go To Date" navigates the register to that day.
"""

import asyncio
from datetime import date, timedelta
from typing import List, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QDateEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QWidget, QStackedWidget,
    QComboBox,
)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor, QFont

from tahmeed.services.cashier_service import get_daily_summaries
from tahmeed.services.category_service import get_all_categories, item_key
from tahmeed.services.subtable_service import get_subtables


# ── Styles ────────────────────────────────────────────────────────────────────

_BTN_STYLE = """
QPushButton {
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 5px;
    padding: 5px 16px;
    color: #374151;
    font-size: 12px;
}
QPushButton:hover   { background: #f9fafb; }
QPushButton:pressed { background: #e5e7eb; }
"""

_PRIMARY_BTN = """
QPushButton {
    background: #E85D04;
    border: 1px solid #F48C06;
    border-radius: 5px;
    padding: 5px 18px;
    color: #ffffff;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover   { background: #F48C06; }
QPushButton:pressed { background: #DC2F02; }
QPushButton:disabled { background: #fdba74; border-color: #fdba74; }
"""

_TABLE_SS = """
QTableWidget {
    background: #ffffff;
    gridline-color: #e5e7eb;
    border: none;
    selection-background-color: #fff3e8;
    selection-color: #111827;
}
QHeaderView::section {
    background: #f1f5f9;
    color: #334155;
    font-weight: 600;
    font-size: 11px;
    padding: 5px 8px;
    border: none;
    border-right: 1px solid #cbd5e1;
    border-bottom: 2px solid #cbd5e1;
}
QTableWidget::item { padding: 2px 8px; color: #111827; }
QTableWidget::item:alternate { background: #f8fafc; }
"""

_FIELD_SS = (
    "QLineEdit, QDateEdit, QComboBox {"
    "  border: 1px solid #d1d5db; border-radius: 4px;"
    "  padding: 0 8px; font-size: 12px; color: #111827;"
    "}"
    "QLineEdit:focus, QDateEdit:focus, QComboBox:focus {"
    "  border-color: #0077C5;"
    "}"
)

# Mode segment-control styles  (left / middle / right pill pieces)
def _seg_style(position: str) -> str:
    if position == "left":
        radius = "border-radius: 4px 0 0 4px;"
    elif position == "right":
        radius = "border-radius: 0 4px 4px 0;"
    else:
        radius = "border-radius: 0;"
    return (
        f"QPushButton {{ background:#ffffff; border:1px solid #d1d5db; {radius}"
        "  padding:4px 14px; font-size:11px; font-weight:600; color:#6b7280; }}"
        f"QPushButton:checked {{ background:#0077C5; border-color:#0077C5; color:#ffffff; }}"
        "QPushButton:hover:!checked { background:#f9fafb; }"
    )


# ── Constants ─────────────────────────────────────────────────────────────────

_MODE_SIMPLE   = 0
_MODE_ADVANCED = 1

_COL_DATE    = 0
_COL_TXN_ID  = 1
_COL_ENTRIES = 2
_COL_REFUND  = 3
_COL_TOTAL   = 4

_ONE_MONTH_AGO = date.today() - timedelta(days=30)


# ── Main dialog ───────────────────────────────────────────────────────────────

class TransactionBrowser(QDialog):
    """
    Modeless dialog — one row per calendar day with aggregated totals.
    Emits go_to_date(date) when the user navigates to a day.
    """

    go_to_date = Signal(object, str)  # (date, highlight_term)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transaction Browser")
        self.setMinimumSize(860, 600)
        self.setWindowFlags(
            Qt.Dialog
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.setStyleSheet("QDialog { background: #ffffff; }")
        self._results: List[dict] = []
        self._cats_loaded = False
        self._current_mode = _MODE_SIMPLE
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_filter_panel())
        root.addWidget(self._build_table())
        root.addWidget(self._build_action_bar())

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background: #1c1917;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        title = QLabel("Transaction Browser")
        title.setStyleSheet("color:#ffffff; font-size:14px; font-weight:700;")
        hl.addWidget(title)
        sub = QLabel("Daily summary — one row per day")
        sub.setStyleSheet("color:#a8a29e; font-size:11px; margin-left:12px;")
        hl.addWidget(sub)
        hl.addStretch()
        return header

    # ── Filter panel ──────────────────────────────────────────────────────────

    def _build_filter_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("filterPanel")
        panel.setStyleSheet(
            "QWidget#filterPanel { background:#f9fafb; border-bottom:1px solid #e5e7eb; }"
        )

        vl = QVBoxLayout(panel)
        vl.setContentsMargins(16, 8, 16, 8)
        vl.setSpacing(6)

        # ── Mode toggle (segmented control) ───────────────────────────────────
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)

        labels    = ["Simple", "Advanced"]
        positions = ["left", "right"]
        self._mode_btns: List[QPushButton] = []
        for i, (txt, pos) in enumerate(zip(labels, positions)):
            btn = QPushButton(txt)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setStyleSheet(_seg_style(pos))
            btn.clicked.connect(lambda _checked, m=i: self._switch_mode(m))
            self._mode_btns.append(btn)
            mode_row.addWidget(btn)
        self._mode_btns[_MODE_SIMPLE].setChecked(True)
        mode_row.addStretch()
        vl.addLayout(mode_row)

        self._find_btns: List[QPushButton] = []

        self._filter_stack = QStackedWidget()
        self._filter_stack.addWidget(self._build_simple_panel())
        self._filter_stack.addWidget(self._build_advanced_panel())
        self._filter_stack.setCurrentIndex(_MODE_SIMPLE)
        self._filter_stack.setFixedHeight(52)
        vl.addWidget(self._filter_stack)

        # mode(26) + spacing(6) + stack(52) + margins(8+8) = 100
        panel.setFixedHeight(100)
        return panel

    # ── Simple mode panel ─────────────────────────────────────────────────────

    def _build_simple_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(8)
        hl.setAlignment(Qt.AlignVCenter)

        # Keyword
        kw_vl = QVBoxLayout()
        kw_vl.setSpacing(3)
        kw_vl.addWidget(_lbl("Search"))
        self._kw_edit = QLineEdit()
        self._kw_edit.setPlaceholderText("Search keyword…")
        self._kw_edit.setFixedHeight(28)
        self._kw_edit.setStyleSheet(_FIELD_SS)
        self._kw_edit.returnPressed.connect(self._do_find)
        kw_vl.addWidget(self._kw_edit)
        hl.addLayout(kw_vl, 2)

        # Find / Reset right after search
        s_find = QPushButton("Find")
        s_find.setFixedSize(70, 28)
        s_find.setStyleSheet(_PRIMARY_BTN)
        s_find.clicked.connect(self._do_find)
        s_reset = QPushButton("Reset")
        s_reset.setFixedSize(70, 28)
        s_reset.setStyleSheet(_BTN_STYLE)
        s_reset.clicked.connect(self._reset_filters)
        btn_vl = QVBoxLayout()
        btn_vl.setSpacing(3)
        btn_vl.addWidget(_lbl(""))
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_row.addWidget(s_find)
        btn_row.addWidget(s_reset)
        btn_vl.addLayout(btn_row)
        hl.addLayout(btn_vl)
        self._find_btns.append(s_find)

        # Date From
        sfrom_vl = QVBoxLayout()
        sfrom_vl.setSpacing(3)
        sfrom_vl.addWidget(_lbl("Date From"))
        self._s_from = _date_edit(_ONE_MONTH_AGO)
        sfrom_vl.addWidget(self._s_from)
        hl.addLayout(sfrom_vl)

        # Date To
        sto_vl = QVBoxLayout()
        sto_vl.setSpacing(3)
        sto_vl.addWidget(_lbl("Date To"))
        self._s_to = _date_edit(date.today())
        sto_vl.addWidget(self._s_to)
        hl.addLayout(sto_vl)

        return w

    # ── Advanced mode panel ───────────────────────────────────────────────────

    def _build_advanced_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(8)
        hl.setAlignment(Qt.AlignVCenter)

        # Keyword search
        adv_kw_vl = QVBoxLayout()
        adv_kw_vl.setSpacing(3)
        adv_kw_vl.addWidget(_lbl("Search"))
        self._adv_kw_edit = QLineEdit()
        self._adv_kw_edit.setPlaceholderText("Search keyword…")
        self._adv_kw_edit.setFixedHeight(28)
        self._adv_kw_edit.setStyleSheet(_FIELD_SS)
        self._adv_kw_edit.returnPressed.connect(self._do_find)
        adv_kw_vl.addWidget(self._adv_kw_edit)
        hl.addLayout(adv_kw_vl, 2)

        # Find / Reset right after search
        a_find = QPushButton("Find")
        a_find.setFixedSize(70, 28)
        a_find.setStyleSheet(_PRIMARY_BTN)
        a_find.clicked.connect(self._do_find)
        a_reset = QPushButton("Reset")
        a_reset.setFixedSize(70, 28)
        a_reset.setStyleSheet(_BTN_STYLE)
        a_reset.clicked.connect(self._reset_filters)
        adv_btn_vl = QVBoxLayout()
        adv_btn_vl.setSpacing(3)
        adv_btn_vl.addWidget(_lbl(""))
        adv_btn_row = QHBoxLayout()
        adv_btn_row.setSpacing(4)
        adv_btn_row.addWidget(a_find)
        adv_btn_row.addWidget(a_reset)
        adv_btn_vl.addLayout(adv_btn_row)
        hl.addLayout(adv_btn_vl)
        self._find_btns.append(a_find)

        # Item (category) combo
        item_vl = QVBoxLayout()
        item_vl.setSpacing(3)
        item_vl.addWidget(_lbl("Item"))
        self._item_combo = QComboBox()
        self._item_combo.setFixedHeight(28)
        self._item_combo.setFixedWidth(160)
        self._item_combo.setStyleSheet(_FIELD_SS)
        self._item_combo.addItem("— Loading… —", None)
        self._item_combo.currentIndexChanged.connect(self._on_item_changed)
        item_vl.addWidget(self._item_combo)
        hl.addLayout(item_vl)

        # Sub-Item combo
        sub_vl = QVBoxLayout()
        sub_vl.setSpacing(3)
        sub_vl.addWidget(_lbl("Sub-Item"))
        self._subitem_combo = QComboBox()
        self._subitem_combo.setFixedHeight(28)
        self._subitem_combo.setFixedWidth(180)
        self._subitem_combo.setStyleSheet(_FIELD_SS)
        self._subitem_combo.addItem("— Any Sub-Item —", None)
        self._subitem_combo.setEnabled(False)
        sub_vl.addWidget(self._subitem_combo)
        hl.addLayout(sub_vl)

        # Date From
        afrom_vl = QVBoxLayout()
        afrom_vl.setSpacing(3)
        afrom_vl.addWidget(_lbl("Date From"))
        self._a_from = _date_edit(_ONE_MONTH_AGO)
        afrom_vl.addWidget(self._a_from)
        hl.addLayout(afrom_vl)

        # Date To
        ato_vl = QVBoxLayout()
        ato_vl.setSpacing(3)
        ato_vl.addWidget(_lbl("Date To"))
        self._a_to = _date_edit(date.today())
        ato_vl.addWidget(self._a_to)
        hl.addLayout(ato_vl)

        return w

    # ── Results table ─────────────────────────────────────────────────────────

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Transaction ID", "Entries", "Refund to Float", "Total Amount"]
        )
        self._table.setStyleSheet(_TABLE_SS)
        self._table.setAlternatingRowColors(True)

        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(True)
        for i in range(5):
            hh.setSectionResizeMode(i, QHeaderView.Interactive)
        self._table.setColumnWidth(_COL_DATE,    130)
        self._table.setColumnWidth(_COL_TXN_ID,  150)
        self._table.setColumnWidth(_COL_ENTRIES,  70)
        self._table.setColumnWidth(_COL_REFUND,  155)
        self._table.setColumnWidth(_COL_TOTAL,   155)

        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setDefaultSectionSize(30)
        self._table.doubleClicked.connect(self._on_go_to)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        return self._table

    # ── Action bar ────────────────────────────────────────────────────────────

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet("background:#f9fafb; border-top:1px solid #e5e7eb;")
        al = QHBoxLayout(bar)
        al.setContentsMargins(16, 0, 16, 0)
        al.setSpacing(8)

        self._count_label = QLabel("Days shown: —")
        self._count_label.setStyleSheet("color:#6b7280; font-size:12px;")

        self._goto_btn = QPushButton("Go To Date")
        self._goto_btn.setFixedWidth(110)
        self._goto_btn.setStyleSheet(_PRIMARY_BTN)
        self._goto_btn.setEnabled(False)
        self._goto_btn.clicked.connect(self._on_go_to)

        self._export_btn = QPushButton("Export…")
        self._export_btn.setFixedWidth(90)
        self._export_btn.setStyleSheet(_BTN_STYLE)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export)

        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(80)
        close_btn.setStyleSheet(_BTN_STYLE)
        close_btn.clicked.connect(self.close)

        al.addWidget(self._count_label)
        al.addStretch()
        al.addWidget(self._goto_btn)
        al.addWidget(self._export_btn)
        al.addWidget(close_btn)
        return bar

    # ── Mode switching ────────────────────────────────────────────────────────

    def _switch_mode(self, mode: int) -> None:
        self._current_mode = mode
        self._filter_stack.setCurrentIndex(mode)
        for i, btn in enumerate(self._mode_btns):
            btn.setChecked(i == mode)
        if mode == _MODE_ADVANCED and not self._cats_loaded:
            asyncio.ensure_future(self._load_categories())

    # ── Async data loaders (Advanced mode) ────────────────────────────────────

    async def _load_categories(self) -> None:
        try:
            cats = await get_all_categories()
            self._cats_loaded = True
            self._item_combo.blockSignals(True)
            self._item_combo.clear()
            self._item_combo.addItem("— Any Item —", None)
            for cat in cats:
                self._item_combo.addItem(cat.name, cat)
            self._item_combo.blockSignals(False)
            self._subitem_combo.setEnabled(False)
        except Exception:
            self._item_combo.clear()
            self._item_combo.addItem("— Load error —", None)

    def _on_item_changed(self, _index: int) -> None:
        cat = self._item_combo.currentData()
        self._subitem_combo.clear()
        self._subitem_combo.addItem("— Any Sub-Item —", None)
        self._subitem_combo.setEnabled(False)
        if cat is not None:
            asyncio.ensure_future(self._load_subtables(cat))

    async def _load_subtables(self, cat) -> None:
        try:
            key  = item_key(cat.name)
            subs = await get_subtables(key)
            self._subitem_combo.blockSignals(True)
            self._subitem_combo.clear()
            self._subitem_combo.addItem("— Any Sub-Item —", None)
            for sub in subs:
                self._subitem_combo.addItem(sub.name, sub)
            self._subitem_combo.blockSignals(False)
            self._subitem_combo.setEnabled(len(subs) > 0)
        except Exception:
            self._subitem_combo.setEnabled(False)

    # ── Search params collector ───────────────────────────────────────────────

    def _get_search_params(self) -> dict:
        mode = self._current_mode

        if mode == _MODE_SIMPLE:
            return dict(
                keyword=self._kw_edit.text().strip(),
                date_from=_qdate_to_py(self._s_from.date()),
                date_to=_qdate_to_py(self._s_to.date()),
            )

        # Advanced
        cat = self._item_combo.currentData()
        sub = self._subitem_combo.currentData()
        return dict(
            keyword=self._adv_kw_edit.text().strip(),
            category_name=cat.name if cat else "",
            sub_item_match=sub.match if sub else "",
            date_from=_qdate_to_py(self._a_from.date()),
            date_to=_qdate_to_py(self._a_to.date()),
        )

    # ── Actions ───────────────────────────────────────────────────────────────

    def _do_find(self) -> None:
        for btn in self._find_btns:
            btn.setEnabled(False)
        asyncio.ensure_future(self._async_find())

    async def _async_find(self) -> None:
        try:
            params = self._get_search_params()
            self._results = await get_daily_summaries(**params)
            self._populate_results(self._results)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Search Error", str(exc))
        finally:
            for btn in self._find_btns:
                btn.setEnabled(True)

    def _populate_results(self, summaries: List[dict]) -> None:
        self._table.setRowCount(len(summaries))
        for i, s in enumerate(summaries):
            d = s["date"]

            date_str = d.strftime("%a, %d %b %Y")
            self._table.setItem(i, _COL_DATE, _ro(date_str))

            txn_it = _ro(f"TXN-{d.strftime('%Y%m%d')}")
            txn_it.setFont(QFont("Consolas", 10))
            txn_it.setForeground(QColor("#0077C5"))
            self._table.setItem(i, _COL_TXN_ID, txn_it)

            self._table.setItem(i, _COL_ENTRIES, _ro(str(s["entries_count"]), Qt.AlignCenter))

            refund = s["total_refund"]
            refund_it = _ro(f"TZS {refund:,.0f}" if refund else "—", Qt.AlignRight | Qt.AlignVCenter)
            if refund:
                refund_it.setForeground(QColor("#EA580C"))
            self._table.setItem(i, _COL_REFUND, refund_it)

            total = s["total_tzs"]
            total_it = _ro(f"TZS {total:,.0f}" if total else "—", Qt.AlignRight | Qt.AlignVCenter)
            if total and total < 0:
                total_it.setForeground(QColor("#dc2626"))
            self._table.setItem(i, _COL_TOTAL, total_it)

        n = len(summaries)
        self._count_label.setText(f"Days shown: {n}")
        self._export_btn.setEnabled(n > 0)

    def _reset_filters(self) -> None:
        mode = self._current_mode
        if mode == _MODE_SIMPLE:
            self._kw_edit.clear()
            self._s_from.setDate(QDate(_ONE_MONTH_AGO.year, _ONE_MONTH_AGO.month, _ONE_MONTH_AGO.day))
            self._s_to.setDate(QDate.currentDate())
        elif mode == _MODE_ADVANCED:
            self._adv_kw_edit.clear()
            self._item_combo.setCurrentIndex(0)
            self._subitem_combo.clear()
            self._subitem_combo.addItem("— Any Sub-Item —", None)
            self._subitem_combo.setEnabled(False)
            self._a_from.setDate(QDate(_ONE_MONTH_AGO.year, _ONE_MONTH_AGO.month, _ONE_MONTH_AGO.day))
            self._a_to.setDate(QDate.currentDate())
        self._table.setRowCount(0)
        self._results = []
        self._count_label.setText("Days shown: —")
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(False)

    def _on_selection_changed(self) -> None:
        self._goto_btn.setEnabled(bool(self._table.selectedItems()))

    def _on_go_to(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._results):
            return

        # Build a highlight term for the register so it can scroll-to the match.
        # Simple mode: keyword or truck.
        # Advanced mode: sub-item match text (matches description); skip category
        #   name because it isn't stored in any text column the register searches.
        # Date mode: no term.
        term = ""
        if self._current_mode == _MODE_SIMPLE:
            term = self._kw_edit.text().strip()
        elif self._current_mode == _MODE_ADVANCED:
            sub = self._subitem_combo.currentData()
            term = sub.match.strip() if sub else ""

        self.go_to_date.emit(self._results[row]["date"], term)
        self.close()

    def _on_export(self) -> None:
        lines = ["Date\tTransaction ID\tEntries\tRefund to Float (TZS)\tTotal Amount (TZS)"]
        for s in self._results:
            d = s["date"]
            lines.append("\t".join([
                d.strftime("%d/%m/%Y"),
                f"TXN-{d.strftime('%Y%m%d')}",
                str(s["entries_count"]),
                f"{s['total_refund']:.0f}" if s["total_refund"] else "0",
                f"{s['total_tzs']:.0f}"    if s["total_tzs"]    else "0",
            ]))
        from PySide6.QtWidgets import QApplication, QMessageBox
        QApplication.clipboard().setText("\n".join(lines))
        QMessageBox.information(
            self, "Exported",
            f"{len(self._results)} days copied to clipboard.\nPaste directly into Excel.",
        )

    # ── Public ────────────────────────────────────────────────────────────────

    def show_and_search(self) -> None:
        """Open the dialog and immediately run a search in Simple mode (last 30 days)."""
        self.show()
        self.raise_()
        self._do_find()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ro(text: str, align=Qt.AlignVCenter | Qt.AlignLeft) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    it.setTextAlignment(align)
    return it


def _lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-size:11px; color:#374151; font-weight:500; background:transparent;")
    return lbl


def _date_edit(d: date) -> QDateEdit:
    de = QDateEdit()
    de.setCalendarPopup(True)
    de.setDisplayFormat("dd/MM/yyyy")
    de.setFixedWidth(115)
    de.setFixedHeight(28)
    de.setDate(QDate(d.year, d.month, d.day))
    de.setStyleSheet(_FIELD_SS)
    return de


def _qdate_to_py(qd: QDate) -> date:
    return date(qd.year(), qd.month(), qd.day())


# Backward-compat alias
TransactionsTable = TransactionBrowser
