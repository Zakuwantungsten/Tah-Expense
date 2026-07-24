"""
TransactionBrowser — embedded browse page (Simple / Advanced / Uploads).

Simple   — daily summary (one row per day): Date | TXN ID | Entries | Refund to Float | Total
           keyword searches description OR truck OR memo across all matched days
Advanced — individual transaction rows with full register columns
           multi-select Item + Description (description = former Sub-Item) filters,
           cascading from entries in the selected date range
Uploads  — Excel daily imports list

Month combo is populated from real DB data (distinct months that have transactions).
Double-click or "Go To Date" navigates the register to that day/transaction.
"""

import asyncio
import calendar as _cal
from datetime import date, timedelta
from typing import List

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QDateEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QWidget, QStackedWidget,
    QComboBox,
)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor, QFont

from tahmeed.models.transaction import Transaction
from tahmeed.services.cashier_service import (
    get_daily_summaries, get_transactions_flat, get_available_months,
    get_browse_items, get_browse_descriptions,
)
from tahmeed.ui.accountant.date_filters import style_calendar_popup
from tahmeed.ui.widgets.checkable_multi_combo import CheckableMultiCombo


# ── Styles ────────────────────────────────────────────────────────────────────

_BTN_STYLE = """
QPushButton {
    background: #ffffff; border: 1px solid #d1d5db; border-radius: 5px;
    padding: 5px 16px; color: #374151; font-size: 12px;
}
QPushButton:hover   { background: #f9fafb; }
QPushButton:pressed { background: #e5e7eb; }
"""

_PRIMARY_BTN = """
QPushButton {
    background: #E85D04; border: 1px solid #F48C06; border-radius: 5px;
    padding: 5px 18px; color: #ffffff; font-size: 12px; font-weight: 600;
}
QPushButton:hover   { background: #F48C06; }
QPushButton:pressed { background: #DC2F02; }
QPushButton:disabled { background: #fdba74; border-color: #fdba74; }
"""

_TABLE_SS = """
QTableWidget {
    background: #ffffff; gridline-color: #e5e7eb; border: none;
    selection-background-color: #fff3e8; selection-color: #111827;
}
QHeaderView::section {
    background: #f1f5f9; color: #334155; font-weight: 600; font-size: 11px;
    padding: 5px 8px; border: none;
    border-right: 1px solid #cbd5e1; border-bottom: 2px solid #cbd5e1;
}
QTableWidget::item { padding: 2px 8px; color: #111827; }
QTableWidget::item:alternate { background: #f8fafc; }
"""

_FIELD_SS = (
    "QLineEdit, QDateEdit, QComboBox {"
    "  border: 1px solid #d1d5db; border-radius: 4px;"
    "  padding: 0 8px; font-size: 12px; color: #111827;"
    "}"
    "QLineEdit:focus, QDateEdit:focus, QComboBox:focus { border-color: #0077C5; }"
)


def _seg_style(position: str) -> str:
    r = {"left": "border-radius:4px 0 0 4px;",
         "right": "border-radius:0 4px 4px 0;"}.get(position, "border-radius:0;")
    return (
        f"QPushButton{{background:#ffffff;border:1px solid #d1d5db;{r}"
        " padding:4px 14px;font-size:11px;font-weight:600;color:#6b7280;}}"
        "QPushButton:checked{background:#0077C5;border-color:#0077C5;color:#ffffff;}"
        "QPushButton:hover:!checked{background:#f9fafb;}"
    )


# ── Column indices — Simple (summary) ─────────────────────────────────────────

_S_COL_DATE    = 0
_S_COL_TXN_ID  = 1
_S_COL_ENTRIES = 2
_S_COL_STATUS  = 3
_S_COL_REFUND  = 4
_S_COL_TOTAL   = 5

# ── Column indices — Advanced (individual) ────────────────────────────────────

_A_COL_DATE      = 0
_A_COL_ITEM      = 1
_A_COL_DESC      = 2
_A_COL_TRUCK     = 3
_A_COL_MEMO      = 4
_A_COL_REFUND    = 5
_A_COL_TZS       = 6
_A_COL_RECEIPT   = 7
_A_COL_OWNERSHIP = 8
_A_COL_APR       = 9
_A_COL_CASHIER   = 10
_A_COL_EDITED_BY = 11

_MODE_SIMPLE   = 0
_MODE_ADVANCED = 1
_MODE_UPLOADS  = 2

_ONE_MONTH_AGO = date.today() - timedelta(days=30)

_U_COL_FILE    = 0
_U_COL_DATE    = 1
_U_COL_COUNT   = 2
_U_COL_CASHIER = 3
_U_COL_ISSUES  = 4
_U_COL_WHEN    = 5
_U_COL_ACT     = 6


# ── Main page ─────────────────────────────────────────────────────────────────

class TransactionBrowser(QWidget):
    go_to_date = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QWidget { background: #ffffff; }")
        self._results_simple: List[dict] = []
        self._results_advanced: List[Transaction] = []
        self._results_uploads: list = []
        self._filter_opts_loaded = False
        self._months_loaded = False
        self._current_mode = _MODE_SIMPLE
        self._cascade_busy = False
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_filter_panel())
        root.addWidget(self._build_table_stack(), 1)
        root.addWidget(self._build_action_bar())

    def _build_header(self) -> QWidget:
        h = QWidget()
        h.setFixedHeight(48)
        h.setStyleSheet("background:#1c1917;")
        hl = QHBoxLayout(h)
        hl.setContentsMargins(16, 0, 16, 0)
        t = QLabel("Transaction Browser")
        t.setStyleSheet("color:#ffffff;font-size:14px;font-weight:700;")
        hl.addWidget(t)
        s = QLabel("Daily summary · Individual rows · Excel uploads")
        s.setStyleSheet("color:#a8a29e;font-size:11px;margin-left:12px;")
        hl.addWidget(s)
        hl.addStretch()
        return h

    # ── Filter panel ──────────────────────────────────────────────────────────

    def _build_filter_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("filterPanel")
        panel.setStyleSheet(
            "QWidget#filterPanel{background:#f9fafb;border-bottom:1px solid #e5e7eb;}"
        )
        vl = QVBoxLayout(panel)
        vl.setContentsMargins(16, 8, 16, 8)
        vl.setSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        self._mode_btns: List[QPushButton] = []
        for i, (txt, pos) in enumerate(
            zip(["Simple", "Advanced", "Uploads"], ["left", "mid", "right"])
        ):
            btn = QPushButton(txt)
            btn.setCheckable(True)
            btn.setAutoDefault(False)  # prevent Enter key from triggering mode switch
            btn.setFixedHeight(26)
            btn.setStyleSheet(_seg_style(pos))
            btn.clicked.connect(lambda _c, m=i: self._switch_mode(m))
            self._mode_btns.append(btn)
            mode_row.addWidget(btn)
        self._mode_btns[_MODE_SIMPLE].setChecked(True)
        mode_row.addStretch()
        vl.addLayout(mode_row)

        self._find_btns: List[QPushButton] = []
        self._filter_stack = QStackedWidget()
        self._filter_stack.addWidget(self._build_simple_panel())
        self._filter_stack.addWidget(self._build_advanced_panel())
        self._filter_stack.addWidget(self._build_uploads_panel())
        self._filter_stack.setCurrentIndex(_MODE_SIMPLE)
        self._filter_stack.setFixedHeight(52)
        vl.addWidget(self._filter_stack)

        panel.setFixedHeight(100)
        return panel

    # ── Simple filter panel ───────────────────────────────────────────────────

    def _build_simple_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(8)
        hl.setAlignment(Qt.AlignVCenter)

        # Month
        self._s_month = _combo(120)
        self._s_month.addItem("All Months", None)
        self._s_from = _date_edit(_ONE_MONTH_AGO)
        self._s_to   = _date_edit(date.today())
        self._s_month.currentIndexChanged.connect(
            lambda idx: _apply_month(self._s_month, self._s_from, self._s_to, idx)
        )
        hl.addLayout(_field("Month", self._s_month))

        hl.addLayout(_field("Date From", self._s_from))
        hl.addLayout(_field("Date To",   self._s_to))
        hl.addStretch()

        # Search / Find / Reset at right end
        self._kw_edit = _lineedit("Search keyword…", 190)
        self._kw_edit.returnPressed.connect(self._do_find)
        hl.addLayout(_field("Search", self._kw_edit))

        s_find, s_reset = self._make_find_reset()
        self._find_btns.append(s_find)
        hl.addLayout(_btn_col(s_find, s_reset))
        return w

    # ── Advanced filter panel ─────────────────────────────────────────────────

    def _build_advanced_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(8)
        hl.setAlignment(Qt.AlignVCenter)

        # Month — default to current month
        _today = date.today()
        self._a_month = _combo(120)
        self._a_month.addItem("All Months", None)
        self._a_from = _date_edit(_today.replace(day=1))
        self._a_to   = _date_edit(_today)
        self._a_month.currentIndexChanged.connect(
            lambda idx: _apply_month(self._a_month, self._a_from, self._a_to, idx)
        )
        self._a_from.dateChanged.connect(lambda *_: self._on_adv_dates_changed())
        self._a_to.dateChanged.connect(lambda *_: self._on_adv_dates_changed())
        hl.addLayout(_field("Month", self._a_month))

        hl.addLayout(_field("Date From", self._a_from))
        hl.addLayout(_field("Date To",   self._a_to))

        # Item — multi-select from entries in the date window
        self._item_combo = CheckableMultiCombo(
            "All Items", noun_plural="items", parent=self,
        )
        self._item_combo.setFixedHeight(28)
        self._item_combo.setFixedWidth(160)
        self._item_combo.setStyleSheet(_FIELD_SS)
        self._item_combo.selectionChanged.connect(self._on_items_changed)
        hl.addLayout(_field("Item", self._item_combo))

        # Sub-Item = description name — multi-select, cascades with Item
        self._subitem_combo = CheckableMultiCombo(
            "All Descriptions", noun_plural="descriptions", parent=self,
        )
        self._subitem_combo.setFixedHeight(28)
        self._subitem_combo.setFixedWidth(180)
        self._subitem_combo.setStyleSheet(_FIELD_SS)
        self._subitem_combo.selectionChanged.connect(self._on_descs_changed)
        hl.addLayout(_field("Sub-Item", self._subitem_combo))

        hl.addStretch()

        # Search / Find / Reset at right end
        self._adv_kw_edit = _lineedit("Search keyword…", 190)
        self._adv_kw_edit.returnPressed.connect(self._do_find)
        hl.addLayout(_field("Search", self._adv_kw_edit))

        a_find, a_reset = self._make_find_reset()
        self._find_btns.append(a_find)
        hl.addLayout(_btn_col(a_find, a_reset))
        return w

    def _build_uploads_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(8)
        tip = QLabel("Excel daily imports from all cashiers — delete a wrong upload here.")
        tip.setStyleSheet("color:#6b7280;font-size:12px;")
        hl.addWidget(tip)
        hl.addStretch()
        refresh = QPushButton("Refresh")
        refresh.setFixedSize(80, 28)
        refresh.setAutoDefault(False)
        refresh.setStyleSheet(_PRIMARY_BTN)
        refresh.clicked.connect(self._do_find)
        self._find_btns.append(refresh)
        hl.addWidget(refresh)
        return w

    def _make_find_reset(self):
        find = QPushButton("Find")
        find.setFixedSize(66, 28)
        find.setAutoDefault(False)
        find.setStyleSheet(_PRIMARY_BTN)
        find.clicked.connect(self._do_find)
        reset = QPushButton("Reset")
        reset.setFixedSize(66, 28)
        reset.setAutoDefault(False)
        reset.setStyleSheet(_BTN_STYLE)
        reset.clicked.connect(self._reset_filters)
        return find, reset

    # ── Table stack ───────────────────────────────────────────────────────────

    def _build_table_stack(self) -> QStackedWidget:
        self._table_stack = QStackedWidget()
        self._simple_table  = self._build_simple_table()
        self._adv_table     = self._build_adv_table()
        self._uploads_table = self._build_uploads_table()
        self._table_stack.addWidget(self._simple_table)
        self._table_stack.addWidget(self._adv_table)
        self._table_stack.addWidget(self._uploads_table)
        return self._table_stack

    def _build_uploads_table(self) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(7)
        t.setHorizontalHeaderLabels([
            "Filename", "Primary Date", "Rows", "Cashier",
            "Issues", "Imported", "",
        ])
        t.setStyleSheet(_TABLE_SS)
        t.setAlternatingRowColors(True)
        hh = t.horizontalHeader()
        for i in range(7):
            hh.setSectionResizeMode(i, QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(_U_COL_FILE, QHeaderView.Stretch)
        t.setColumnWidth(_U_COL_DATE, 110)
        t.setColumnWidth(_U_COL_COUNT, 60)
        t.setColumnWidth(_U_COL_CASHIER, 120)
        t.setColumnWidth(_U_COL_ISSUES, 90)
        t.setColumnWidth(_U_COL_WHEN, 140)
        t.setColumnWidth(_U_COL_ACT, 110)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setDefaultSectionSize(32)
        t.itemSelectionChanged.connect(self._on_selection_changed)
        return t

    def _build_simple_table(self) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(6)
        t.setHorizontalHeaderLabels(
            ["Date", "Transaction ID", "Entries", "Merged / Status",
             "Refund to Float", "Total Amount"]
        )
        t.setStyleSheet(_TABLE_SS)
        t.setAlternatingRowColors(True)
        hh = t.horizontalHeader()
        for i in range(6):
            hh.setSectionResizeMode(i, QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(_S_COL_STATUS, QHeaderView.Stretch)
        t.setColumnWidth(_S_COL_DATE,    130)
        t.setColumnWidth(_S_COL_TXN_ID,  150)
        t.setColumnWidth(_S_COL_ENTRIES,  70)
        t.setColumnWidth(_S_COL_STATUS,  260)
        t.setColumnWidth(_S_COL_REFUND,  140)
        t.setColumnWidth(_S_COL_TOTAL,   130)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setDefaultSectionSize(30)
        t.doubleClicked.connect(self._on_go_to)
        t.itemSelectionChanged.connect(self._on_selection_changed)
        return t

    def _build_adv_table(self) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(12)
        t.setHorizontalHeaderLabels([
            "Date", "Item", "Description", "Truck No.",
            "Memo", "Ref_Float", "TZS", "Receipt", "Ownership", "APR BY",
            "Cashier", "Edited by",
        ])
        t.setStyleSheet(_TABLE_SS)
        t.setAlternatingRowColors(True)
        hh = t.horizontalHeader()
        for i in range(12):
            hh.setSectionResizeMode(i, QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(_A_COL_DESC, QHeaderView.Stretch)
        t.setColumnWidth(_A_COL_DATE,      108)
        t.setColumnWidth(_A_COL_ITEM,      110)
        t.setColumnWidth(_A_COL_DESC,      220)
        t.setColumnWidth(_A_COL_TRUCK,      88)
        t.setColumnWidth(_A_COL_MEMO,       80)
        t.setColumnWidth(_A_COL_REFUND,    105)
        t.setColumnWidth(_A_COL_TZS,       105)
        t.setColumnWidth(_A_COL_RECEIPT,    82)
        t.setColumnWidth(_A_COL_OWNERSHIP,  95)
        t.setColumnWidth(_A_COL_APR,        90)
        t.setColumnWidth(_A_COL_CASHIER,   110)
        t.setColumnWidth(_A_COL_EDITED_BY, 110)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setDefaultSectionSize(28)
        t.doubleClicked.connect(self._on_go_to)
        t.itemSelectionChanged.connect(self._on_selection_changed)
        return t

    # ── Action bar ────────────────────────────────────────────────────────────

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet("background:#f9fafb;border-top:1px solid #e5e7eb;")
        al = QHBoxLayout(bar)
        al.setContentsMargins(16, 0, 16, 0)
        al.setSpacing(8)

        self._count_label = QLabel("Days shown: —")
        self._count_label.setStyleSheet("color:#6b7280;font-size:12px;")

        self._goto_btn = QPushButton("Go To Date")
        self._goto_btn.setFixedWidth(110)
        self._goto_btn.setAutoDefault(False)
        self._goto_btn.setStyleSheet(_PRIMARY_BTN)
        self._goto_btn.setEnabled(False)
        self._goto_btn.clicked.connect(self._on_go_to)

        self._export_btn = QPushButton("Export…")
        self._export_btn.setFixedWidth(90)
        self._export_btn.setAutoDefault(False)
        self._export_btn.setStyleSheet(_BTN_STYLE)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export)

        al.addWidget(self._count_label)
        al.addStretch()
        al.addWidget(self._goto_btn)
        al.addWidget(self._export_btn)
        return bar

    # ── Mode switching ────────────────────────────────────────────────────────

    def _switch_mode(self, mode: int) -> None:
        self._current_mode = mode
        self._filter_stack.setCurrentIndex(mode)
        self._table_stack.setCurrentIndex(mode)
        for i, btn in enumerate(self._mode_btns):
            btn.setChecked(i == mode)
        if not self._months_loaded and mode != _MODE_UPLOADS:
            asyncio.ensure_future(self._load_months())
        if mode == _MODE_ADVANCED:
            asyncio.ensure_future(self._ensure_filter_options())
            self._do_find()
        elif mode == _MODE_UPLOADS:
            self._do_find()
        # update count label phrasing
        if mode == _MODE_SIMPLE:
            self._count_label.setText("Days shown: —")
        elif mode == _MODE_UPLOADS:
            self._count_label.setText("Uploads: —")
        else:
            self._count_label.setText("Transactions: —")
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(mode != _MODE_UPLOADS and False)

    # ── Async loaders ─────────────────────────────────────────────────────────

    async def _load_months(self) -> None:
        try:
            months = await get_available_months()
            self._months_loaded = True
            for combo in (self._s_month, self._a_month):
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("All Months", None)
                for y, m in months:
                    combo.addItem(f"{_cal.month_abbr[m]} {y}", (y, m))
                combo.blockSignals(False)

            # Auto-select current month in the Advanced combo and fill date range
            today = date.today()
            for idx in range(1, self._a_month.count()):
                if self._a_month.itemData(idx) == (today.year, today.month):
                    self._a_month.setCurrentIndex(idx)  # triggers _apply_month
                    break
        except Exception:
            pass

    async def _ensure_filter_options(self) -> None:
        await self._reload_cascade_options(from_items=True, from_descs=True)
        self._filter_opts_loaded = True

    def _adv_date_range(self):
        return _qdate_to_py(self._a_from.date()), _qdate_to_py(self._a_to.date())

    def _on_adv_dates_changed(self) -> None:
        if self._current_mode != _MODE_ADVANCED:
            return
        asyncio.ensure_future(self._reload_cascade_options(from_items=True, from_descs=True))

    def _on_items_changed(self) -> None:
        if self._cascade_busy:
            return
        asyncio.ensure_future(self._cascade_from_items())

    def _on_descs_changed(self) -> None:
        if self._cascade_busy:
            return
        asyncio.ensure_future(self._cascade_from_descs())

    async def _cascade_from_items(self) -> None:
        await self._reload_cascade_options(from_items=False, from_descs=True)
        self._do_find()

    async def _cascade_from_descs(self) -> None:
        await self._reload_cascade_options(from_items=True, from_descs=False)
        self._do_find()

    async def _reload_cascade_options(
        self, *, from_items: bool, from_descs: bool,
    ) -> None:
        date_from, date_to = self._adv_date_range()
        items_sel = self._item_combo.selected_values()
        self._cascade_busy = True
        try:
            if from_descs:
                descs = await get_browse_descriptions(
                    date_from=date_from,
                    date_to=date_to,
                    category_name=items_sel or None,
                )
                self._subitem_combo.set_options(descs, keep_selected=True, emit=False)
            if from_items:
                descs_sel = self._subitem_combo.selected_values()
                items = await get_browse_items(
                    date_from=date_from,
                    date_to=date_to,
                    descriptions=descs_sel or None,
                )
                self._item_combo.set_options(items, keep_selected=True, emit=False)
        except Exception:
            pass
        finally:
            self._cascade_busy = False

    # ── Search ────────────────────────────────────────────────────────────────

    def _get_search_params(self) -> dict:
        if self._current_mode == _MODE_SIMPLE:
            return dict(
                keyword=self._kw_edit.text().strip(),
                date_from=_qdate_to_py(self._s_from.date()),
                date_to=_qdate_to_py(self._s_to.date()),
            )
        return dict(
            keyword=self._adv_kw_edit.text().strip(),
            category_name=self._item_combo.selected_values(),
            descriptions=self._subitem_combo.selected_values(),
            date_from=_qdate_to_py(self._a_from.date()),
            date_to=_qdate_to_py(self._a_to.date()),
        )

    def _do_find(self) -> None:
        for btn in self._find_btns:
            btn.setEnabled(False)
        asyncio.ensure_future(self._async_find())

    async def _async_find(self) -> None:
        try:
            if self._current_mode == _MODE_UPLOADS:
                await self._load_uploads()
                return
            params = self._get_search_params()
            if self._current_mode == _MODE_SIMPLE:
                self._results_simple = await get_daily_summaries(**params)
                from tahmeed.services.accountant_service import get_cashier_names
                ids = []
                for s in self._results_simple:
                    ids.extend(s.get("cashier_ids") or [])
                self._user_names = await get_cashier_names(ids) if ids else {}
                self._populate_simple(self._results_simple)
            else:
                self._results_advanced = await get_transactions_flat(**params)
                from tahmeed.services.accountant_service import get_cashier_names
                ids = []
                for tx in self._results_advanced:
                    if tx.cashier_id:
                        ids.append(tx.cashier_id)
                    if tx.last_edited_by:
                        ids.append(tx.last_edited_by)
                self._user_names = await get_cashier_names(ids) if ids else {}
                self._populate_advanced(self._results_advanced)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Search Error", str(exc))
        finally:
            for btn in self._find_btns:
                btn.setEnabled(True)

    async def _load_uploads(self) -> None:
        from tahmeed.services.daily_import_service import list_daily_uploads
        from tahmeed.services.accountant_service import get_cashier_names

        uploads = await list_daily_uploads(limit=200)
        self._results_uploads = uploads
        cashier_ids = [u.get("cashier_id") for u in uploads if u.get("cashier_id")]
        names = await get_cashier_names(cashier_ids) if cashier_ids else {}
        self._populate_uploads(uploads, names)

    def _populate_uploads(self, uploads: list, names: dict) -> None:
        from PySide6.QtWidgets import QMessageBox

        t = self._uploads_table
        t.setRowCount(len(uploads))
        for i, u in enumerate(uploads):
            upload_id = str(u.get("_id") or "")
            fname = u.get("source_filename") or "—"
            t.setItem(i, _U_COL_FILE, _ro(fname))

            primary = u.get("primary_date") or u.get("min_date")
            if hasattr(primary, "strftime"):
                date_str = primary.strftime("%d/%m/%Y")
            else:
                date_str = "—"
            t.setItem(i, _U_COL_DATE, _ro(date_str))

            count = int(u.get("count") or 0)
            t.setItem(i, _U_COL_COUNT, _ro(str(count), Qt.AlignCenter))

            cid = u.get("cashier_id")
            t.setItem(i, _U_COL_CASHIER, _ro(names.get(cid, "—") if cid else "—"))

            outliers = int(u.get("outlier_count") or 0)
            dupes = int(u.get("duplicate_count") or 0)
            issue_bits = []
            if outliers:
                issue_bits.append(f"{outliers} date")
            if dupes:
                issue_bits.append(f"{dupes} dup")
            t.setItem(i, _U_COL_ISSUES, _ro(", ".join(issue_bits) if issue_bits else "—"))

            created = u.get("created_at")
            when = created.strftime("%d/%m/%Y %H:%M") if hasattr(created, "strftime") else "—"
            t.setItem(i, _U_COL_WHEN, _ro(when))

            btn = QPushButton("Delete")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:#fff;color:#b91c1c;border:1px solid #fecaca;"
                "border-radius:4px;padding:2px 10px;font-size:11px;font-weight:600;}"
                "QPushButton:hover{background:#fef2f2;}"
            )
            btn.clicked.connect(
                lambda _c=False, uid=upload_id, fn=fname, n=count: self._confirm_delete_upload(uid, fn, n)
            )
            t.setCellWidget(i, _U_COL_ACT, btn)

        self._count_label.setText(f"Uploads: {len(uploads)}")
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(False)

    def _confirm_delete_upload(self, upload_id: str, filename: str, count: int) -> None:
        from PySide6.QtWidgets import QMessageBox
        if not upload_id:
            return
        resp = QMessageBox.warning(
            self,
            "Delete Upload",
            f'Delete upload "{filename}" and all {count:,} saved transaction(s)?\n\n'
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp == QMessageBox.Yes:
            asyncio.ensure_future(self._delete_upload(upload_id))

    async def _delete_upload(self, upload_id: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        from tahmeed.services.daily_import_service import delete_daily_upload
        try:
            deleted = await delete_daily_upload(upload_id)
            QMessageBox.information(
                self, "Upload Deleted", f"Removed {deleted:,} transaction(s)."
            )
            await self._load_uploads()
        except Exception as exc:
            QMessageBox.critical(self, "Delete Failed", str(exc))

    # ── Populate ──────────────────────────────────────────────────────────────

    def _populate_simple(self, rows: List[dict]) -> None:
        t = self._simple_table
        t.setRowCount(len(rows))
        names = getattr(self, "_user_names", {}) or {}
        for i, s in enumerate(rows):
            d = s["date"]
            t.setItem(i, _S_COL_DATE, _ro(d.strftime("%a, %d %b %Y")))

            txn = _ro(f"TXN-{d.strftime('%Y%m%d')}")
            txn.setFont(QFont("Consolas", 10))
            txn.setForeground(QColor("#0077C5"))
            t.setItem(i, _S_COL_TXN_ID, txn)

            t.setItem(i, _S_COL_ENTRIES, _ro(str(s["entries_count"]), Qt.AlignCenter))

            status_it = _ro(self._simple_status_text(s, names))
            t.setItem(i, _S_COL_STATUS, status_it)

            refund = s["total_refund"]
            ref_it = _ro(f"TZS {refund:,.0f}" if refund else "—", Qt.AlignRight | Qt.AlignVCenter)
            if refund:
                ref_it.setForeground(QColor("#EA580C"))
            t.setItem(i, _S_COL_REFUND, ref_it)

            total = s["total_tzs"]
            tot_it = _ro(f"TZS {total:,.0f}" if total else "—", Qt.AlignRight | Qt.AlignVCenter)
            if total and total < 0:
                tot_it.setForeground(QColor("#dc2626"))
            t.setItem(i, _S_COL_TOTAL, tot_it)

        n = len(rows)
        self._count_label.setText(f"Days shown: {n}")
        self._export_btn.setEnabled(n > 0)

    @staticmethod
    def _simple_status_text(summary: dict, names: dict) -> str:
        """e.g. 'Merged by Aisha, John · All submitted' or 'Aisha · 2 draft, 3 submitted'."""
        cashier_ids = summary.get("cashier_ids") or []
        people = []
        for cid in cashier_ids:
            label = names.get(cid)
            if label:
                people.append(label)
            else:
                people.append(str(cid)[:8])
        people = sorted(set(people), key=str.lower)

        draft = int(summary.get("draft_count") or 0)
        submitted = int(summary.get("submitted_count") or 0)
        total = int(summary.get("entries_count") or 0)
        # Legacy rows without register_status count as submitted in the pipeline.
        if draft + submitted < total:
            submitted = total - draft

        if draft and submitted:
            state = f"{submitted} submitted, {draft} draft"
        elif draft:
            state = "All draft" if draft == total else f"{draft} draft"
        else:
            state = "All submitted"

        if not people:
            return state
        if len(people) == 1:
            return f"{people[0]} · {state}"
        if len(people) <= 3:
            return f"Merged by {', '.join(people)} · {state}"
        return f"Merged by {len(people)} users · {state}"

    def _populate_advanced(self, txs: List[Transaction]) -> None:
        t = self._adv_table
        t.setRowCount(len(txs))

        ids = []
        for tx in txs:
            if tx.cashier_id:
                ids.append(tx.cashier_id)
            if tx.last_edited_by:
                ids.append(tx.last_edited_by)
        # Names filled async in _async_find; use cached map when present.
        names = getattr(self, "_user_names", {}) or {}

        for i, tx in enumerate(txs):
            d = tx.date
            date_str = d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d)
            t.setItem(i, _A_COL_DATE, _ro(date_str))
            t.setItem(i, _A_COL_ITEM, _ro(tx.item or tx.category_name or ""))
            t.setItem(i, _A_COL_DESC, _ro(tx.description or ""))
            t.setItem(i, _A_COL_TRUCK, _ro(tx.truck_number or ""))
            t.setItem(i, _A_COL_MEMO, _ro(tx.memo or ""))

            if tx.notes_flag:
                ref_it = _ro("REFUND TO FLOAT", Qt.AlignCenter)
                ref_it.setForeground(QColor("#EA580C"))
                t.setItem(i, _A_COL_REFUND, ref_it)
            else:
                t.setItem(i, _A_COL_REFUND, _ro(""))

            amt = tx.amount or 0
            amt_it = _ro(f"TZS {amt:,.0f}" if amt else "—", Qt.AlignRight | Qt.AlignVCenter)
            if amt < 0:
                amt_it.setForeground(QColor("#dc2626"))
            t.setItem(i, _A_COL_TZS, amt_it)

            status = (tx.receipt_status or "pending").strip().lower()
            _rcpt_lbl = {
                "received": "RECEIPT",
                "pending": "PENDING",
                "missing": "MISSING",
                "no_receipt": "NO RECEIPT",
            }.get(status, status.upper() if status else "—")
            rec_it = _ro(_rcpt_lbl, Qt.AlignCenter)
            rec_it.setForeground(QColor(
                {
                    "received": "#16a34a",
                    "missing": "#dc2626",
                    "pending": "#d97706",
                    "no_receipt": "#6b7280",
                }.get(status, "#6b7280")
            ))
            t.setItem(i, _A_COL_RECEIPT, rec_it)
            t.setItem(i, _A_COL_OWNERSHIP, _ro(tx.ownership or ""))
            t.setItem(i, _A_COL_APR, _ro(tx.approver or ""))
            cashier = names.get(tx.cashier_id, "—") if tx.cashier_id else "—"
            edited = names.get(tx.last_edited_by, "—") if tx.last_edited_by else "—"
            t.setItem(i, _A_COL_CASHIER, _ro(cashier))
            t.setItem(i, _A_COL_EDITED_BY, _ro(edited))

        n = len(txs)
        self._count_label.setText(f"Transactions: {n}")
        self._export_btn.setEnabled(n > 0)

    # ── Reset ─────────────────────────────────────────────────────────────────

    def _reset_filters(self) -> None:
        if self._current_mode == _MODE_UPLOADS:
            self._do_find()
            return
        if self._current_mode == _MODE_SIMPLE:
            self._kw_edit.clear()
            self._s_month.setCurrentIndex(0)
            self._s_from.setDate(QDate(_ONE_MONTH_AGO.year, _ONE_MONTH_AGO.month, _ONE_MONTH_AGO.day))
            self._s_to.setDate(QDate.currentDate())
        else:
            self._adv_kw_edit.clear()
            self._a_month.setCurrentIndex(0)
            self._cascade_busy = True
            try:
                self._item_combo.reset_to_all(emit=False)
                self._subitem_combo.reset_to_all(emit=False)
            finally:
                self._cascade_busy = False
            self._a_from.setDate(QDate(_ONE_MONTH_AGO.year, _ONE_MONTH_AGO.month, _ONE_MONTH_AGO.day))
            self._a_to.setDate(QDate.currentDate())
            asyncio.ensure_future(self._reload_cascade_options(from_items=True, from_descs=True))
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._do_find()

    # ── Selection / navigation ────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        if self._current_mode == _MODE_UPLOADS:
            self._goto_btn.setEnabled(False)
            return
        active = self._simple_table if self._current_mode == _MODE_SIMPLE else self._adv_table
        self._goto_btn.setEnabled(bool(active.selectedItems()))

    def _on_go_to(self) -> None:
        if self._current_mode == _MODE_UPLOADS:
            return
        if self._current_mode == _MODE_SIMPLE:
            row = self._simple_table.currentRow()
            if row < 0 or row >= len(self._results_simple):
                return
            d    = self._results_simple[row]["date"]
            term = self._kw_edit.text().strip()
        else:
            row = self._adv_table.currentRow()
            if row < 0 or row >= len(self._results_advanced):
                return
            tx = self._results_advanced[row]
            d  = tx.date
            if hasattr(d, "date"):
                d = d.date()
            descs = self._subitem_combo.selected_values()
            term = descs[0] if len(descs) == 1 else self._adv_kw_edit.text().strip()
        self.go_to_date.emit(d, term)

    # ── Export ────────────────────────────────────────────────────────────────

    def _on_export(self) -> None:
        if self._current_mode == _MODE_SIMPLE:
            names = getattr(self, "_user_names", {}) or {}
            lines = [
                "Date\tTransaction ID\tEntries\tMerged / Status"
                "\tRefund to Float (TZS)\tTotal Amount (TZS)"
            ]
            for s in self._results_simple:
                d = s["date"]
                lines.append("\t".join([
                    d.strftime("%d/%m/%Y"),
                    f"TXN-{d.strftime('%Y%m%d')}",
                    str(s["entries_count"]),
                    self._simple_status_text(s, names),
                    f"{s['total_refund']:.0f}" if s["total_refund"] else "0",
                    f"{s['total_tzs']:.0f}"    if s["total_tzs"]    else "0",
                ]))
            n = len(self._results_simple)
        else:
            lines = ["\t".join([
                "Date", "Item", "Description", "Truck No.", "Memo",
                "Ref Float", "TZS", "Receipt", "Ownership", "APR BY",
                "Cashier", "Edited by",
            ])]
            names = getattr(self, "_user_names", {}) or {}
            for tx in self._results_advanced:
                d = tx.date
                lines.append("\t".join([
                    d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d),
                    tx.item or tx.category_name or "",
                    tx.description or "",
                    tx.truck_number or "",
                    tx.memo or "",
                    "REFUND TO FLOAT" if tx.notes_flag else "",
                    f"{tx.amount:.0f}" if tx.amount else "0",
                    (tx.receipt_status or "").capitalize(),
                    tx.ownership or "",
                    tx.approver or "",
                    names.get(tx.cashier_id, "") if tx.cashier_id else "",
                    names.get(tx.last_edited_by, "") if tx.last_edited_by else "",
                ]))
            n = len(self._results_advanced)

        from PySide6.QtWidgets import QApplication, QMessageBox
        QApplication.clipboard().setText("\n".join(lines))
        QMessageBox.information(self, "Exported",
            f"{n} rows copied to clipboard.\nPaste directly into Excel.")

    # ── Public ────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Called when the Browse sidebar page is shown."""
        if not self._months_loaded:
            asyncio.ensure_future(self._load_months())
        if self._current_mode == _MODE_ADVANCED:
            asyncio.ensure_future(self._ensure_filter_options())
        self._do_find()

    def show_and_search(self) -> None:
        """Backward-compatible alias for embedded refresh."""
        self.refresh()


# ── Module helpers ────────────────────────────────────────────────────────────

def _ro(text: str, align=Qt.AlignVCenter | Qt.AlignLeft) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    it.setTextAlignment(align)
    return it


def _lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-size:11px;color:#374151;font-weight:500;background:transparent;")
    return lbl


def _field(label: str, widget: QWidget) -> QVBoxLayout:
    vl = QVBoxLayout()
    vl.setSpacing(3)
    vl.addWidget(_lbl(label))
    vl.addWidget(widget)
    return vl


def _btn_col(find: QPushButton, reset: QPushButton) -> QVBoxLayout:
    vl = QVBoxLayout()
    vl.setSpacing(3)
    vl.addWidget(_lbl(""))
    row = QHBoxLayout()
    row.setSpacing(4)
    row.addWidget(find)
    row.addWidget(reset)
    vl.addLayout(row)
    return vl


def _combo(width: int) -> QComboBox:
    c = QComboBox()
    c.setFixedHeight(28)
    c.setFixedWidth(width)
    c.setStyleSheet(_FIELD_SS)
    return c


def _lineedit(placeholder: str, width: int) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFixedHeight(28)
    e.setFixedWidth(width)
    e.setStyleSheet(_FIELD_SS)
    return e


def _date_edit(d: date) -> QDateEdit:
    de = QDateEdit()
    de.setCalendarPopup(True)
    de.setDisplayFormat("dd/MM/yyyy")
    de.setFixedWidth(115)
    de.setFixedHeight(28)
    de.setDate(QDate(d.year, d.month, d.day))
    de.setStyleSheet(_FIELD_SS)
    style_calendar_popup(de)
    return de


def _qdate_to_py(qd: QDate) -> date:
    return date(qd.year(), qd.month(), qd.day())


def _apply_month(combo: QComboBox, from_edit: QDateEdit, to_edit: QDateEdit, idx: int) -> None:
    data = combo.itemData(idx)
    if data is None:
        return
    y, m = data
    _, last_day = _cal.monthrange(y, m)
    from_edit.setDate(QDate(y, m, 1))
    to_edit.setDate(QDate(y, m, last_day))


# Backward-compat alias
TransactionsTable = TransactionBrowser
