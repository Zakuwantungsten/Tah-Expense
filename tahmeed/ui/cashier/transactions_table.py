"""
TransactionBrowser — embedded browse page (Simple / Advanced / Uploads).

Simple   — daily summary (one row per day): Date | TXN ID | Entries | Refund to Float | Total
           Year + Month filters; keyword searches description OR truck OR memo
Advanced — individual transaction rows with full register columns
           Year + Month + multi-select Item / Description filters
Uploads  — Excel daily imports list

Year/Month combos are populated from real DB data. A loading overlay covers the
table while searches run so the first open never looks empty mid-fetch.
Double-click a Simple day, Open an upload, or "Go To Date" jumps the live
register table to that day / upload batch.
"""

import asyncio
import calendar as _cal
from datetime import date, datetime
from typing import List, Optional

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
    get_daily_summaries_page, get_transactions_flat_page, get_browse_bootstrap,
    get_browse_items, get_browse_descriptions,
)
from tahmeed.ui.accountant.date_filters import style_calendar_popup
from tahmeed.ui.accountant.separate_expenses import (
    _CELL_FONT_PT, _ROW_H, _finish_table_row, _stripe_bg, _table_cell_font,
    _table_style,
)
from tahmeed.ui.widgets.checkable_multi_combo import CheckableMultiCombo
from tahmeed.ui.widgets.combo_styles import with_combo_popup
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay


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
    background: #0077C5; border: 1px solid #0077C5; border-radius: 5px;
    padding: 5px 18px; color: #ffffff; font-size: 12px; font-weight: 600;
}
QPushButton:hover   { background: #0369A1; border-color: #0369A1; }
QPushButton:pressed { background: #075985; border-color: #075985; }
QPushButton:disabled { background: #93C5FD; border-color: #93C5FD; }
"""

# Same compact density as Separate Expenses (10px cells / 24px rows).
_TABLE_SS = _table_style()

_FIELD_SS = with_combo_popup(
    "QLineEdit, QDateEdit, QComboBox {"
    "  border: 1px solid #d1d5db; border-radius: 4px;"
    "  padding: 0 8px; font-size: 12px; color: #111827;"
    "  background: #FFFFFF;"
    "}"
    "QLineEdit:focus, QDateEdit:focus, QComboBox:focus { border-color: #0077C5; }"
)


def _seg_style(position: str) -> str:
    # Qt stylesheets reject multi-value border-radius shorthands.
    if position == "left":
        r = (
            "border-top-left-radius:4px;border-bottom-left-radius:4px;"
            "border-top-right-radius:0;border-bottom-right-radius:0;"
        )
    elif position == "right":
        r = (
            "border-top-left-radius:0;border-bottom-left-radius:0;"
            "border-top-right-radius:4px;border-bottom-right-radius:4px;"
        )
    else:
        r = "border-radius:0;"
    return (
        f"QPushButton{{background:#ffffff;border:1px solid #d1d5db;{r}"
        " padding:4px 14px;font-size:11px;font-weight:600;color:#6b7280;}"
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

_MONTH_NAMES = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

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
    go_to_upload = Signal(str, object)

    def __init__(
        self,
        parent=None,
        *,
        user=None,
        can_manage_uploads: bool = True,
    ):
        super().__init__(parent)
        self.setStyleSheet("QWidget { background: #ffffff; }")
        self._user = user
        # True (accountant): delete any upload. False (cashier): delete own only.
        self._can_manage_uploads = bool(can_manage_uploads)
        self._results_simple: List[dict] = []
        self._results_advanced: List[Transaction] = []
        self._results_uploads: list = []
        self._months_loaded = False
        self._years_loaded = False
        self._available_years: List[int] = []
        self._current_mode = _MODE_SIMPLE
        self._cascade_busy = False
        self._filter_upload_id: str = ""
        self._find_generation = 0
        self._find_in_flight = False
        self._loading: Optional[LoadingOverlay] = None
        self._build_ui()

    def _owns_upload(self, upload: dict) -> bool:
        """True when the logged-in user owns this upload batch."""
        me = getattr(self._user, "_id", None) if self._user is not None else None
        if me is None:
            return False
        owner = upload.get("cashier_id")
        if owner is None:
            return False
        return str(owner) == str(me)

    def _can_delete_upload(self, upload: dict) -> bool:
        if self._can_manage_uploads:
            return True
        return self._owns_upload(upload)

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
        s = QLabel("Daily days · Individual rows · Excel uploads")
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

        today = date.today()
        self._s_year = _combo(88)
        self._s_year.addItem(str(today.year), today.year)
        self._s_month = _combo(120)
        self._fill_month_combo(self._s_month)
        self._s_from = _date_edit(today.replace(day=1))
        self._s_to = _date_edit(date(today.year, today.month, _cal.monthrange(today.year, today.month)[1]))
        self._s_year.currentIndexChanged.connect(lambda *_: self._on_period_changed("simple"))
        self._s_month.currentIndexChanged.connect(lambda *_: self._on_period_changed("simple"))
        hl.addLayout(_field("Year", self._s_year))
        hl.addLayout(_field("Month", self._s_month))

        hl.addLayout(_field("Date From", self._s_from))
        hl.addLayout(_field("Date To",   self._s_to))
        hl.addStretch()

        self._kw_edit = _lineedit("Search keyword…", 190)
        self._kw_edit.returnPressed.connect(self._do_find)
        hl.addLayout(_field("Search", self._kw_edit))

        s_find, s_reset = self._make_find_reset()
        self._find_btns.append(s_find)
        hl.addLayout(_btn_col(s_find, s_reset))
        # Default: current month
        self._s_month.blockSignals(True)
        idx = self._s_month.findData(today.month)
        if idx >= 0:
            self._s_month.setCurrentIndex(idx)
        self._s_month.blockSignals(False)
        return w

    # ── Advanced filter panel ─────────────────────────────────────────────────

    def _build_advanced_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(8)
        hl.setAlignment(Qt.AlignVCenter)

        today = date.today()
        self._a_year = _combo(88)
        self._a_year.addItem(str(today.year), today.year)
        self._a_month = _combo(120)
        self._fill_month_combo(self._a_month)
        self._a_from = _date_edit(today.replace(day=1))
        self._a_to = _date_edit(date(today.year, today.month, _cal.monthrange(today.year, today.month)[1]))
        self._a_year.currentIndexChanged.connect(lambda *_: self._on_period_changed("advanced"))
        self._a_month.currentIndexChanged.connect(lambda *_: self._on_period_changed("advanced"))
        self._a_from.dateChanged.connect(lambda *_: self._on_adv_dates_changed())
        self._a_to.dateChanged.connect(lambda *_: self._on_adv_dates_changed())
        hl.addLayout(_field("Year", self._a_year))
        hl.addLayout(_field("Month", self._a_month))

        hl.addLayout(_field("Date From", self._a_from))
        hl.addLayout(_field("Date To",   self._a_to))

        self._item_combo = CheckableMultiCombo(
            "All Items", noun_plural="items", parent=self,
        )
        self._item_combo.setFixedHeight(28)
        self._item_combo.setFixedWidth(150)
        self._item_combo.setStyleSheet(_FIELD_SS)
        self._item_combo.set_options_loader(self._load_item_filter_options)
        self._item_combo.selectionChanged.connect(self._on_items_changed)
        hl.addLayout(_field("Item", self._item_combo))

        self._subitem_combo = CheckableMultiCombo(
            "All Descriptions", noun_plural="descriptions", parent=self,
        )
        self._subitem_combo.setFixedHeight(28)
        self._subitem_combo.setFixedWidth(160)
        self._subitem_combo.setStyleSheet(_FIELD_SS)
        self._subitem_combo.set_options_loader(self._load_desc_filter_options)
        self._subitem_combo.selectionChanged.connect(self._on_descs_changed)
        hl.addLayout(_field("Sub-Item", self._subitem_combo))

        hl.addStretch()

        self._adv_kw_edit = _lineedit("Search keyword…", 170)
        self._adv_kw_edit.returnPressed.connect(self._do_find)
        hl.addLayout(_field("Search", self._adv_kw_edit))

        a_find, a_reset = self._make_find_reset()
        self._find_btns.append(a_find)
        hl.addLayout(_btn_col(a_find, a_reset))
        self._a_month.blockSignals(True)
        idx = self._a_month.findData(today.month)
        if idx >= 0:
            self._a_month.setCurrentIndex(idx)
        self._a_month.blockSignals(False)
        return w

    def _build_uploads_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(8)
        tip = QLabel(
            "Excel daily imports — double-click or Open to view the batch on the register."
        )
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

    def _build_table_stack(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("browserTableWrap")
        wrap.setStyleSheet("QWidget#browserTableWrap{background:#ffffff;}")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._table_stack = QStackedWidget()
        self._simple_table  = self._build_simple_table()
        self._adv_table     = self._build_adv_table()
        self._uploads_table = self._build_uploads_table()
        self._table_stack.addWidget(self._simple_table)
        self._table_stack.addWidget(self._adv_table)
        self._table_stack.addWidget(self._uploads_table)
        lay.addWidget(self._table_stack)
        self._loading = LoadingOverlay(wrap, "Loading…")
        return wrap

    def _build_uploads_table(self) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(7)
        t.setHorizontalHeaderLabels([
            "Filename", "Primary Date", "Rows", "Cashier",
            "Issues", "Imported", "",
        ])
        t.setStyleSheet(_TABLE_SS)
        t.setAlternatingRowColors(False)
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
        t.setColumnWidth(_U_COL_ACT, 170)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setDefaultSectionSize(_ROW_H)
        t.itemSelectionChanged.connect(self._on_selection_changed)
        t.doubleClicked.connect(self._on_open_upload)
        return t

    def _build_simple_table(self) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(6)
        t.setHorizontalHeaderLabels(
            ["Date", "Transaction ID", "Entries", "Merged / Status",
             "Refund to Float", "Total Amount"]
        )
        t.setStyleSheet(_TABLE_SS)
        t.setAlternatingRowColors(False)
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
        t.verticalHeader().setDefaultSectionSize(_ROW_H)
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
        # Manual Master Expenses zebra (white / slate) — not Qt AlternateBase.
        t.setAlternatingRowColors(False)
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
        t.verticalHeader().setDefaultSectionSize(_ROW_H)
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
        if mode != _MODE_ADVANCED:
            self._filter_upload_id = ""
        self._show_mode(mode)

    def _show_mode(self, mode: int) -> None:
        self._current_mode = mode
        self._filter_stack.setCurrentIndex(mode)
        self._table_stack.setCurrentIndex(mode)
        for i, btn in enumerate(self._mode_btns):
            btn.setChecked(i == mode)
        if mode == _MODE_SIMPLE:
            self._count_label.setText("Days shown: —")
        elif mode == _MODE_UPLOADS:
            self._count_label.setText("Uploads: —")
        else:
            self._count_label.setText("Transactions: —")
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        # Always reload so first open is never a blank table mid-fetch.
        self._do_find()

    def _on_open_upload(self, index) -> None:
        self._open_upload_at(index.row())

    def _open_upload_at(self, row: int) -> None:
        """Open one Excel upload on the Daily Register table."""
        if row < 0 or row >= len(self._results_uploads):
            return
        u = self._results_uploads[row]
        upload_id = str(u.get("_id") or "").strip()
        if not upload_id:
            return
        primary = u.get("primary_date") or u.get("min_date")
        self.go_to_upload.emit(upload_id, primary)

    # ── Async loaders ─────────────────────────────────────────────────────────

    async def _load_months(self) -> None:
        try:
            # Phase 3: years + months in one bootstrap call.
            boot = await get_browse_bootstrap()
            self._available_years = boot.years
            self._years_loaded = True
            self._months_loaded = True
            self._populate_year_combos(boot.years)
            self._db_months = boot.months
        except Exception:
            self._months_loaded = True
            self._years_loaded = True

    def _populate_year_combos(self, years: List[int]) -> None:
        if not years:
            years = [date.today().year]
        for cb in (self._s_year, self._a_year):
            cur = cb.currentData()
            cb.blockSignals(True)
            cb.clear()
            for y in years:
                cb.addItem(str(y), y)
            idx = cb.findData(cur if cur is not None else date.today().year)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)

    @staticmethod
    def _fill_month_combo(combo: QComboBox) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All Months", None)
        for m in range(1, 13):
            combo.addItem(_MONTH_NAMES[m], m)
        combo.blockSignals(False)

    def _on_period_changed(self, which: str) -> None:
        self._apply_period_to_dates(which)
        if which == "advanced":
            self._on_adv_dates_changed()
        self._do_find()

    def _apply_period_to_dates(self, which: str) -> None:
        if which == "simple":
            year_cb, month_cb, from_ed, to_ed = (
                self._s_year, self._s_month, self._s_from, self._s_to,
            )
        else:
            year_cb, month_cb, from_ed, to_ed = (
                self._a_year, self._a_month, self._a_from, self._a_to,
            )
        y = year_cb.currentData()
        m = month_cb.currentData()
        if y is None:
            y = date.today().year
        if m is None:
            from_ed.setDate(QDate(int(y), 1, 1))
            to_ed.setDate(QDate(int(y), 12, 31))
        else:
            last = _cal.monthrange(int(y), int(m))[1]
            from_ed.setDate(QDate(int(y), int(m), 1))
            to_ed.setDate(QDate(int(y), int(m), last))

    async def _ensure_period_ready(self) -> None:
        if not self._years_loaded or not self._months_loaded:
            await self._load_months()

    # ── Async loaders ─────────────────────────────────────────────────────────

    def _adv_date_range(self):
        return _qdate_to_py(self._a_from.date()), _qdate_to_py(self._a_to.date())

    def _on_adv_dates_changed(self) -> None:
        if self._current_mode != _MODE_ADVANCED:
            return
        # Phase 2: invalidate Item/Sub-Item distincts; reload on next ▾ open.
        self._item_combo.mark_options_stale()
        self._subitem_combo.mark_options_stale()

    def _on_items_changed(self) -> None:
        if self._cascade_busy:
            return
        asyncio.ensure_future(self._cascade_from_items())

    def _on_descs_changed(self) -> None:
        if self._cascade_busy:
            return
        asyncio.ensure_future(self._cascade_from_descs())

    async def _load_item_filter_options(self):
        """Distinct Item values for the current Advanced date / Sub-Item scope."""
        date_from, date_to = self._adv_date_range()
        descs_sel = self._subitem_combo.selected_values()
        return await get_browse_items(
            date_from=date_from,
            date_to=date_to,
            descriptions=descs_sel or None,
        )

    async def _load_desc_filter_options(self):
        """Distinct Sub-Item values for the current Advanced date / Item scope."""
        date_from, date_to = self._adv_date_range()
        items_sel = self._item_combo.selected_values()
        return await get_browse_descriptions(
            date_from=date_from,
            date_to=date_to,
            category_name=items_sel or None,
        )

    async def _cascade_from_items(self) -> None:
        # Selection changed — refresh related Sub-Item list now, then Find.
        await self._reload_cascade_options(from_items=False, from_descs=True)
        self._do_find()

    async def _cascade_from_descs(self) -> None:
        await self._reload_cascade_options(from_items=True, from_descs=False)
        self._do_find()

    async def _reload_cascade_options(
        self, *, from_items: bool, from_descs: bool,
    ) -> None:
        self._cascade_busy = True
        try:
            if from_descs:
                descs = await self._load_desc_filter_options()
                self._subitem_combo.set_options(descs, keep_selected=True, emit=False)
            if from_items:
                items = await self._load_item_filter_options()
                self._item_combo.set_options(items, keep_selected=True, emit=False)
        except Exception:
            pass
        finally:
            self._cascade_busy = False

    # ── Search ────────────────────────────────────────────────────────────────

    def _get_search_params(self) -> dict:
        if self._current_mode == _MODE_SIMPLE:
            params = dict(
                keyword=self._kw_edit.text().strip(),
                date_from=_qdate_to_py(self._s_from.date()),
                date_to=_qdate_to_py(self._s_to.date()),
            )
        else:
            params = dict(
                keyword=self._adv_kw_edit.text().strip(),
                category_name=self._item_combo.selected_values(),
                descriptions=self._subitem_combo.selected_values(),
                date_from=_qdate_to_py(self._a_from.date()),
                date_to=_qdate_to_py(self._a_to.date()),
            )
        if self._filter_upload_id and self._current_mode == _MODE_ADVANCED:
            params["daily_import_id"] = self._filter_upload_id
        return params

    def _do_find(self) -> None:
        for btn in self._find_btns:
            btn.setEnabled(False)
        asyncio.ensure_future(self._async_find())

    async def _async_find(self) -> None:
        self._find_generation += 1
        gen = self._find_generation
        self._find_in_flight = True
        mode = self._current_mode
        if self._loading is not None:
            if mode == _MODE_UPLOADS:
                msg = "Loading uploads…"
            elif mode == _MODE_SIMPLE:
                msg = "Loading daily transactions…"
            else:
                msg = "Loading transactions…"
            self._loading.show_loading(msg)
        try:
            if mode != _MODE_UPLOADS:
                await self._ensure_period_ready()
                if gen != self._find_generation:
                    return
            if mode == _MODE_UPLOADS:
                await self._load_uploads()
                return
            # Advanced Item/Sub-Item distincts load lazily on ▾ open (Phase 2).
            params = self._get_search_params()
            if mode == _MODE_SIMPLE:
                page = await get_daily_summaries_page(**params, limit=400)
                if gen != self._find_generation:
                    return
                self._results_simple = page.items
                self._user_names = page.cashier_names
                self._populate_simple(self._results_simple)
            else:
                limit = 10000 if params.get("daily_import_id") else 1000
                page = await get_transactions_flat_page(**params, limit=limit)
                if gen != self._find_generation:
                    return
                self._results_advanced = page.items
                self._user_names = page.cashier_names
                self._populate_advanced(
                    self._results_advanced, total=page.total,
                )
                if params.get("daily_import_id"):
                    self._count_label.setText(
                        f"Upload rows: {len(self._results_advanced):,}"
                    )
                    self._export_btn.setEnabled(bool(self._results_advanced))
                    return
        except Exception as exc:
            if gen == self._find_generation:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Search Error", str(exc))
        finally:
            self._find_in_flight = False
            if gen == self._find_generation and self._loading is not None:
                self._loading.hide_loading()
            if gen == self._find_generation:
                for btn in self._find_btns:
                    btn.setEnabled(True)

    async def _resolve_cashier_names(self, cashier_ids: list) -> dict:
        if not cashier_ids:
            return {}
        try:
            from tahmeed.services.accountant_service import get_cashier_names
            return await get_cashier_names(cashier_ids)
        except Exception:
            return {}

    async def _load_uploads(self) -> None:
        from tahmeed.services.daily_import_service import list_daily_uploads_page

        page = await list_daily_uploads_page(limit=200)
        # Defense: never show groups without a real batch id.
        uploads = [u for u in page.items if u.get("_id")]
        self._results_uploads = uploads
        self._populate_uploads(uploads, page.cashier_names)

    def _populate_uploads(self, uploads: list, names: dict) -> None:
        self._upload_cashier_names = names or {}
        t = self._uploads_table
        t.setRowCount(len(uploads))
        for i, u in enumerate(uploads):
            row_bg = _stripe_bg(i)
            upload_id = str(u.get("_id") or "")
            fname = u.get("source_filename") or "—"
            t.setItem(i, _U_COL_FILE, _ro(fname, bg=row_bg))

            primary = u.get("primary_date") or u.get("min_date")
            if hasattr(primary, "strftime"):
                date_str = primary.strftime("%d/%m/%Y")
            else:
                date_str = "—"
            t.setItem(i, _U_COL_DATE, _ro(date_str, bg=row_bg))

            count = int(u.get("count") or 0)
            t.setItem(i, _U_COL_COUNT, _ro(str(count), Qt.AlignCenter, bg=row_bg))

            cid = u.get("cashier_id")
            t.setItem(
                i, _U_COL_CASHIER,
                _ro(names.get(cid, "—") if cid else "—", bg=row_bg),
            )

            outliers = int(u.get("outlier_count") or 0)
            dupes = int(u.get("duplicate_count") or 0)
            issue_bits = []
            if outliers:
                issue_bits.append(f"{outliers} date")
            if dupes:
                issue_bits.append(f"{dupes} dup")
            t.setItem(
                i, _U_COL_ISSUES,
                _ro(", ".join(issue_bits) if issue_bits else "—", bg=row_bg),
            )

            created = u.get("created_at")
            when = created.strftime("%d/%m/%Y %H:%M") if hasattr(created, "strftime") else "—"
            t.setItem(i, _U_COL_WHEN, _ro(when, bg=row_bg))

            acts = QWidget()
            acts.setStyleSheet(f"background:{row_bg};")
            hl = QHBoxLayout(acts)
            hl.setContentsMargins(2, 0, 2, 0)
            hl.setSpacing(4)

            open_btn = QPushButton("Open")
            open_btn.setCursor(Qt.PointingHandCursor)
            open_btn.setFixedHeight(_ROW_H - 4)
            open_btn.setStyleSheet(
                "QPushButton{background:#fff;color:#0077C5;border:1px solid #BFDBFE;"
                f"border-radius:3px;padding:0 8px;font-size:{_CELL_FONT_PT}pt;font-weight:600;}}"
                "QPushButton:hover{background:#E8F4FD;}"
            )
            open_btn.clicked.connect(lambda _c=False, r=i: self._open_upload_at(r))
            hl.addWidget(open_btn)

            if self._can_delete_upload(u):
                btn = QPushButton("Delete")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFixedHeight(_ROW_H - 4)
                btn.setStyleSheet(
                    "QPushButton{background:#fff;color:#b91c1c;border:1px solid #fecaca;"
                    f"border-radius:3px;padding:0 8px;font-size:{_CELL_FONT_PT}pt;font-weight:600;}}"
                    "QPushButton:hover{background:#fef2f2;}"
                )
                btn.clicked.connect(
                    lambda _c=False, uid=upload_id, fn=fname, n=count:
                    self._confirm_delete_upload(uid, fn, n)
                )
                hl.addWidget(btn)
            t.setCellWidget(i, _U_COL_ACT, acts)
            _finish_table_row(t, i, row_bg)

        self._count_label.setText(f"Uploads: {len(uploads)}")
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(len(uploads) > 0)

    def _confirm_delete_upload(self, upload_id: str, filename: str, count: int) -> None:
        from PySide6.QtWidgets import QMessageBox
        if not upload_id:
            resp = QMessageBox.warning(
                self,
                "Invalid Upload",
                f'Upload "{filename}" has no valid batch id (often left by an '
                "older buggy save).\n\n"
                "Clean these faulty tags from the database so they stop "
                "appearing in Uploads?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if resp == QMessageBox.Yes:
                asyncio.ensure_future(self._cleanup_faulty_uploads())
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

    async def _cleanup_faulty_uploads(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from tahmeed.services.daily_import_service import cleanup_null_daily_import_ids
        try:
            n = await cleanup_null_daily_import_ids()
            QMessageBox.information(
                self,
                "Cleaned",
                f"Removed faulty upload tags from {n:,} transaction(s).\n"
                "Normal entries were kept; only the bad upload grouping was cleared.",
            )
            await self._load_uploads()
        except Exception as exc:
            QMessageBox.critical(self, "Cleanup Failed", str(exc))

    async def _delete_upload(self, upload_id: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        from tahmeed.services.daily_import_service import delete_daily_upload
        from tahmeed.ui.widgets.upload_busy import UploadBusy
        try:
            with UploadBusy(self, "Deleting upload…", title="Uploads"):
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
            row_bg = _stripe_bg(i)
            d = s["date"]
            t.setItem(i, _S_COL_DATE, _ro(d.strftime("%a, %d %b %Y"), bg=row_bg))

            txn = _ro(f"TXN-{d.strftime('%Y%m%d')}", bg=row_bg)
            txn_font = QFont("Consolas")
            txn_font.setPointSize(_CELL_FONT_PT)
            txn.setFont(txn_font)
            txn.setForeground(QColor("#0077C5"))
            t.setItem(i, _S_COL_TXN_ID, txn)

            t.setItem(
                i, _S_COL_ENTRIES,
                _ro(str(s["entries_count"]), Qt.AlignCenter, bg=row_bg),
            )

            status_it = _ro(self._simple_status_text(s, names), bg=row_bg)
            t.setItem(i, _S_COL_STATUS, status_it)

            refund = s["total_refund"]
            ref_it = _ro(
                f"TZS {refund:,.0f}" if refund else "—",
                Qt.AlignRight | Qt.AlignVCenter,
                bg=row_bg,
            )
            if refund:
                ref_it.setForeground(QColor("#EA580C"))
            t.setItem(i, _S_COL_REFUND, ref_it)

            total = s["total_tzs"]
            tot_it = _ro(
                f"TZS {total:,.0f}" if total else "—",
                Qt.AlignRight | Qt.AlignVCenter,
                bg=row_bg,
            )
            if total and total < 0:
                tot_it.setForeground(QColor("#dc2626"))
            t.setItem(i, _S_COL_TOTAL, tot_it)
            _finish_table_row(t, i, row_bg)

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

    def _populate_advanced(
        self, txs: List[Transaction], *, total: int | None = None,
    ) -> None:
        t = self._adv_table
        t.setRowCount(len(txs))
        # Names filled by composite list payload in _async_find.
        names = getattr(self, "_user_names", {}) or {}

        for i, tx in enumerate(txs):
            row_bg = _stripe_bg(i)
            d = tx.date
            date_str = d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d)
            t.setItem(i, _A_COL_DATE, _ro(date_str, bg=row_bg))
            t.setItem(i, _A_COL_ITEM, _ro(tx.item or tx.category_name or "", bg=row_bg))
            t.setItem(i, _A_COL_DESC, _ro(tx.description or "", bg=row_bg))
            t.setItem(i, _A_COL_TRUCK, _ro(tx.truck_number or "", bg=row_bg))
            t.setItem(i, _A_COL_MEMO, _ro(tx.memo or "", bg=row_bg))

            if tx.notes_flag:
                ref_it = _ro("REFUND TO FLOAT", Qt.AlignCenter, bg=row_bg)
                ref_it.setForeground(QColor("#EA580C"))
                t.setItem(i, _A_COL_REFUND, ref_it)
            else:
                t.setItem(i, _A_COL_REFUND, _ro("", bg=row_bg))

            amt = tx.amount or 0
            amt_it = _ro(
                f"TZS {amt:,.0f}" if amt else "—",
                Qt.AlignRight | Qt.AlignVCenter,
                bg=row_bg,
            )
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
            rec_it = _ro(_rcpt_lbl, Qt.AlignCenter, bg=row_bg)
            rec_it.setForeground(QColor(
                {
                    "received": "#16a34a",
                    "missing": "#dc2626",
                    "pending": "#d97706",
                    "no_receipt": "#6b7280",
                }.get(status, "#6b7280")
            ))
            t.setItem(i, _A_COL_RECEIPT, rec_it)
            t.setItem(i, _A_COL_OWNERSHIP, _ro(tx.ownership or "", bg=row_bg))
            t.setItem(i, _A_COL_APR, _ro(tx.approver or "", bg=row_bg))
            cashier = names.get(tx.cashier_id, "—") if tx.cashier_id else "—"
            edited = names.get(tx.last_edited_by, "—") if tx.last_edited_by else "—"
            t.setItem(i, _A_COL_CASHIER, _ro(cashier, bg=row_bg))
            t.setItem(i, _A_COL_EDITED_BY, _ro(edited, bg=row_bg))
            _finish_table_row(t, i, row_bg)

        n = len(txs)
        matched = int(total) if total is not None else n
        if matched > n:
            self._count_label.setText(f"Transactions: {n:,} of {matched:,}")
        else:
            self._count_label.setText(f"Transactions: {n:,}")
        self._export_btn.setEnabled(n > 0)

    # ── Reset ─────────────────────────────────────────────────────────────────

    def _reset_filters(self) -> None:
        if self._current_mode == _MODE_UPLOADS:
            self._do_find()
            return
        self._filter_upload_id = ""
        today = date.today()
        if self._current_mode == _MODE_SIMPLE:
            self._kw_edit.clear()
            self._s_year.blockSignals(True)
            self._s_month.blockSignals(True)
            y_idx = self._s_year.findData(today.year)
            if y_idx >= 0:
                self._s_year.setCurrentIndex(y_idx)
            m_idx = self._s_month.findData(today.month)
            if m_idx >= 0:
                self._s_month.setCurrentIndex(m_idx)
            self._s_year.blockSignals(False)
            self._s_month.blockSignals(False)
            self._apply_period_to_dates("simple")
        else:
            self._adv_kw_edit.clear()
            self._cascade_busy = True
            try:
                self._item_combo.reset_to_all(emit=False)
                self._subitem_combo.reset_to_all(emit=False)
            finally:
                self._cascade_busy = False
            self._a_year.blockSignals(True)
            self._a_month.blockSignals(True)
            y_idx = self._a_year.findData(today.year)
            if y_idx >= 0:
                self._a_year.setCurrentIndex(y_idx)
            m_idx = self._a_month.findData(today.month)
            if m_idx >= 0:
                self._a_month.setCurrentIndex(m_idx)
            self._a_year.blockSignals(False)
            self._a_month.blockSignals(False)
            self._apply_period_to_dates("advanced")
            self._item_combo.mark_options_stale()
            self._subitem_combo.mark_options_stale()
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._do_find()

    # ── Selection / navigation ────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        if self._current_mode == _MODE_UPLOADS:
            self._goto_btn.setEnabled(self._uploads_table.currentRow() >= 0)
            return
        active = self._simple_table if self._current_mode == _MODE_SIMPLE else self._adv_table
        self._goto_btn.setEnabled(bool(active.selectedItems()))

    def _on_go_to(self) -> None:
        if self._current_mode == _MODE_UPLOADS:
            self._open_upload_at(self._uploads_table.currentRow())
            return
        if self._current_mode == _MODE_SIMPLE:
            row = self._simple_table.currentRow()
            if row < 0 or row >= len(self._results_simple):
                return
            d = _as_py_date(self._results_simple[row].get("date"))
            if d is None:
                return
            self.go_to_date.emit(d, self._kw_edit.text().strip())
            return
        if self._current_mode == _MODE_ADVANCED:
            row = self._adv_table.currentRow()
            if row < 0 or row >= len(self._results_advanced):
                return
            tx = self._results_advanced[row]
            d = _as_py_date(tx.date)
            if d is None:
                return
            descs = self._subitem_combo.selected_values()
            term = descs[0] if len(descs) == 1 else self._adv_kw_edit.text().strip()
            self.go_to_date.emit(d, term)

    # ── Export ────────────────────────────────────────────────────────────────

    def _on_export(self) -> None:
        if self._current_mode == _MODE_UPLOADS:
            lines = [
                "File\tPrimary date\tEntries\tCashier\tIssues\tUploaded"
            ]
            name_map = getattr(self, "_upload_cashier_names", {}) or {}
            for u in self._results_uploads:
                primary = u.get("primary_date") or u.get("min_date")
                if hasattr(primary, "strftime"):
                    date_str = primary.strftime("%d/%m/%Y")
                else:
                    date_str = ""
                created = u.get("created_at")
                when = (
                    created.strftime("%d/%m/%Y %H:%M")
                    if hasattr(created, "strftime")
                    else ""
                )
                cid = u.get("cashier_id")
                outliers = int(u.get("outlier_count") or 0)
                dupes = int(u.get("duplicate_count") or 0)
                issue_bits = []
                if outliers:
                    issue_bits.append(f"{outliers} date")
                if dupes:
                    issue_bits.append(f"{dupes} dup")
                lines.append("\t".join([
                    u.get("source_filename") or "",
                    date_str,
                    str(int(u.get("count") or 0)),
                    name_map.get(cid, "") if cid else "",
                    ", ".join(issue_bits),
                    when,
                ]))
            n = len(self._results_uploads)
        elif self._current_mode == _MODE_SIMPLE:
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
        self._do_find()

    def show_and_search(self) -> None:
        """Backward-compatible alias for embedded refresh."""
        self.refresh()


# ── Module helpers ────────────────────────────────────────────────────────────

def _ro(
    text: str,
    align=Qt.AlignVCenter | Qt.AlignLeft,
    *,
    bg: str = "",
) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    it.setTextAlignment(align)
    it.setFont(_table_cell_font())
    if bg:
        it.setBackground(QColor(bg))
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


def _as_py_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return None


def _qdate_to_py(qd: QDate) -> date:
    return date(qd.year(), qd.month(), qd.day())


# Backward-compat alias
TransactionsTable = TransactionBrowser
