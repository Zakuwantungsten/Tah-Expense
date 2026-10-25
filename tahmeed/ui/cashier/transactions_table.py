"""
TransactionBrowser — embedded browse page (Simple / Advanced / Smart Search / Uploads).

Simple       — daily summary (one row per day): Date | TXN ID | Entries | Refund to Float | Total
               Year + Month filters; keyword searches description OR truck OR memo
Advanced     — individual transaction rows with full register columns
               Year + Month + multi-select Item / Description filters
Smart Search — individual rows across every day; Item / Description / Payee /
               Truck contains-filters (AND) + optional open-ended date range
Uploads      — Excel daily imports list

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
    QAbstractItemView, QWidget, QStackedWidget, QStackedLayout,
    QComboBox, QSizePolicy, QBoxLayout, QSpacerItem,
)
from PySide6.QtCore import Qt, QDate, QEvent, QObject, Signal
from PySide6.QtGui import QColor, QFont

from tahmeed.models.transaction import Transaction
from tahmeed.services.cashier_service import (
    get_daily_summaries_page, get_transactions_flat_page, get_browse_bootstrap,
    get_browse_items, get_browse_descriptions, smart_search_page,
)
from tahmeed.ui.accountant.date_filters import style_calendar_popup
from tahmeed.ui.accountant.separate_expenses import (
    _CELL_FONT_PT, _ROW_H, _finish_table_row, _stripe_bg, _table_cell_font,
    _table_style,
)
from tahmeed.ui.widgets.checkable_multi_combo import CheckableMultiCombo
from tahmeed.ui.widgets.combo_styles import with_combo_popup
from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit
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


# Filter rows must fit the narrowest Browse page (1100px window − 220px sidebar).
_QWIDGETSIZE_MAX = 16777215
_DATE_MIN_W = 106
# Filter-panel width below which Advanced / Smart Search split onto two rows.
_ADV_WRAP_BELOW = 1080
_SMART_WRAP_BELOW = 1100
_FILTER_ROW_H = 58
_FILTER_WRAPPED_H = 112
_MODE_ROW_H = 48
_BTN_MIN_W = 54
# Base button styles pad 16–18px a side, which alone exceeds _BTN_MIN_W.
_COMPACT_BTN_PAD = "QPushButton { padding: 4px 8px; }"

_SMART_INPUT_H = 32
_SMART_FIELD_SS = with_combo_popup(
    "QLineEdit, QDateEdit {"
    "  border: 1px solid #d1d5db; border-radius: 4px;"
    "  padding: 0 10px; font-size: 13px; color: #111827;"
    "  background: #FFFFFF;"
    "}"
    "QLineEdit:focus, QDateEdit:focus { border-color: #0077C5; }"
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

# ── Column indices — Smart Search ─────────────────────────────────────────────

_M_COL_DATE    = 0
_M_COL_ITEM    = 1
_M_COL_DESC    = 2
_M_COL_PAYEE   = 3
_M_COL_TRUCK   = 4
_M_COL_MEMO    = 5
_M_COL_REFUND  = 6
_M_COL_TZS     = 7
_M_COL_USD     = 8
_M_COL_RECEIPT = 9
_M_COL_CASHIER = 10

_SMART_HEADERS = [
    "Date", "Item", "Description", "Payee", "Truck No.", "Memo",
    "Ref_Float", "TZS", "USD", "Receipt", "Cashier",
]
_SMART_PAGE_SIZE = 100
_ADV_PAGE_SIZE = 100
_SMART_HINT = "Enter at least one filter (Item, Description, Payee, Truck or a date), then press Find."

_MODE_SIMPLE   = 0
_MODE_ADVANCED = 1
_MODE_SMART    = 2
_MODE_UPLOADS  = 3

_RECEIPT_LABELS = {
    "received": "RECEIPT",
    "pending": "PENDING",
    "missing": "MISSING",
    "no_receipt": "NO RECEIPT",
}
_RECEIPT_COLORS = {
    "received": "#16a34a",
    "missing": "#dc2626",
    "pending": "#d97706",
    "no_receipt": "#6b7280",
}

_MONTH_NAMES = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_U_COL_SN      = 0
_U_COL_FILE    = 1
_U_COL_DATE    = 2
_U_COL_COUNT   = 3
_U_COL_CASHIER = 4
_U_COL_TOTAL   = 5
_U_COL_ISSUES  = 6
_U_COL_WHEN    = 7
_U_COL_ACT     = 8

_UPLOAD_PAGE_SIZE = 50


def _format_upload_total(upload: dict) -> str:
    """Show TZS, USD, or both — whichever the batch actually has."""
    tzs = float(upload.get("total_tzs") or 0.0)
    usd = float(upload.get("total_usd") or 0.0)
    has_tzs = tzs != 0.0
    has_usd = usd != 0.0
    if has_tzs and has_usd:
        return f"TZS {tzs:,.0f} · USD {usd:,.2f}"
    if has_usd:
        return f"USD {usd:,.2f}"
    if has_tzs:
        return f"TZS {tzs:,.0f}"
    return "—"


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
        self._adv_total = 0
        self._adv_params: dict = {}
        self._adv_scroll_loading = False
        self._results_smart: List[Transaction] = []
        self._smart_total = 0
        self._smart_summary = None
        self._smart_names: dict = {}
        self._smart_params: dict = {}
        self._smart_searched = False
        self._smart_scroll_loading = False
        self._smart_suggestions_loaded = False
        self._results_uploads: list = []
        self._uploads_total = 0
        self._uploads_scroll_loading = False
        self._upload_cashier_names: dict = {}
        self._months_loaded = False
        self._years_loaded = False
        self._available_years: List[int] = []
        self._current_mode = _MODE_SIMPLE
        self._cascade_busy = False
        self._find_generation = 0
        self._find_in_flight = False
        self._loading: Optional[LoadingOverlay] = None
        self._wrap_rows: dict = {}
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
        root.addWidget(self._build_filter_panel())
        root.addWidget(self._build_table_stack(), 1)
        root.addWidget(self._build_action_bar())

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
            zip(
                ["Simple", "Advanced", "Smart Search", "Uploads"],
                ["left", "mid", "mid", "right"],
            )
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
        self._filter_stack.addWidget(self._build_smart_panel())
        self._filter_stack.addWidget(self._build_uploads_panel())
        self._filter_stack.setCurrentIndex(_MODE_SIMPLE)
        self._filter_stack.setFixedHeight(_FILTER_ROW_H)
        vl.addWidget(self._filter_stack)

        panel.setFixedHeight(_MODE_ROW_H + _FILTER_ROW_H)
        self._filter_panel = panel
        return panel

    def _apply_responsive(self) -> None:
        """Stack Advanced / Smart Search filters on two rows when the page is narrow."""
        avail = self.width() - 32
        for row in self._wrap_rows.values():
            row.set_wrapped(avail < row.breakpoint)
        current = self._wrap_rows.get(self._current_mode)
        h = _FILTER_WRAPPED_H if current is not None and current.wrapped else _FILTER_ROW_H
        if self._filter_stack.height() != h:
            self._filter_stack.setFixedHeight(h)
            self._filter_panel.setFixedHeight(_MODE_ROW_H + h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive()

    # ── Simple filter panel ───────────────────────────────────────────────────

    def _build_simple_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(8)
        hl.setAlignment(Qt.AlignVCenter)

        today = date.today()
        self._s_year = _combo(88, 66)
        self._s_year.addItem(str(today.year), today.year)
        self._s_month = _combo(120, 86)
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

        self._kw_edit = _lineedit("Search keyword…", 190, 110)
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
        row = _WrapRow(w, breakpoint=_ADV_WRAP_BELOW, first_stretch=1, second_stretch=1)
        self._wrap_rows[_MODE_ADVANCED] = row
        hl = row.first

        today = date.today()
        self._a_year = _combo(88, 64)
        self._a_year.addItem(str(today.year), today.year)
        self._a_month = _combo(120, 84)
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
        row.finish()
        hl = row.second

        self._item_combo = CheckableMultiCombo(
            "All Items", noun_plural="items", parent=self,
        )
        self._item_combo.setFixedHeight(28)
        _squeeze(self._item_combo, 92, 150)
        self._item_combo.setStyleSheet(_FIELD_SS)
        self._item_combo.set_options_loader(self._load_item_filter_options)
        self._item_combo.selectionChanged.connect(self._on_items_changed)
        hl.addLayout(_field("Item", self._item_combo))

        self._subitem_combo = CheckableMultiCombo(
            "All Descriptions", noun_plural="descriptions", parent=self,
        )
        self._subitem_combo.setFixedHeight(28)
        _squeeze(self._subitem_combo, 100, 160)
        self._subitem_combo.setStyleSheet(_FIELD_SS)
        self._subitem_combo.set_options_loader(self._load_desc_filter_options)
        self._subitem_combo.selectionChanged.connect(self._on_descs_changed)
        hl.addLayout(_field("Sub-Item", self._subitem_combo))

        hl.addStretch()

        self._adv_kw_edit = _lineedit("Search keyword…", 170, 92)
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

    # ── Smart Search filter panel ─────────────────────────────────────────────

    def _build_smart_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        row = _WrapRow(w, breakpoint=_SMART_WRAP_BELOW, first_stretch=1, second_stretch=1)
        self._wrap_rows[_MODE_SMART] = row
        hl = row.first

        # Item / Payee suggest from the catalog as you type; Tab accepts.
        self._m_item = CompleterLineEdit([], parent=w, ranked_contains=True)
        self._m_payee = CompleterLineEdit([], parent=w, ranked_contains=True)
        self._m_desc = QLineEdit()
        self._m_truck = QLineEdit()
        self._m_item.setToolTip("Type to see items · Tab accepts the suggestion")
        self._m_payee.setToolTip("Type to see payees · Tab accepts the suggestion")
        # Item gets the widest share, a bit more than Description.
        for edit, placeholder, min_w, stretch, label in (
            (self._m_item, "Type an item…", 104, 6, "Item"),
            (self._m_desc, "e.g. Diesel", 92, 5, "Description"),
            (self._m_payee, "Type a payee…", 92, 4, "Payee"),
            (self._m_truck, "e.g. T103", 72, 3, "Truck No."),
        ):
            edit.setPlaceholderText(placeholder)
            _squeeze(edit, min_w)
            edit.setFixedHeight(_SMART_INPUT_H)
            edit.setStyleSheet(_SMART_FIELD_SS)
            edit.setClearButtonEnabled(True)
            edit.returnPressed.connect(self._do_find)
            hl.addLayout(_field(label, edit), stretch)
        row.finish()
        hl = row.second

        self._m_from = _optional_date_edit()
        self._m_to = _optional_date_edit()
        for de in (self._m_from, self._m_to):
            _squeeze(de, _DATE_MIN_W, 124)
            de.setFixedHeight(_SMART_INPUT_H)
            de.setStyleSheet(_SMART_FIELD_SS)
            style_calendar_popup(de)
        self._m_from.setToolTip("Leave as Any to search from the first entry")
        self._m_to.setToolTip("Leave as Any to search up to the latest entry")
        hl.addLayout(_field("Date From", self._m_from))
        hl.addLayout(_field("Date To", self._m_to))
        chain = (
            self._m_item, self._m_desc, self._m_payee, self._m_truck,
            self._m_from, self._m_to,
        )
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)
        self._m_clear_dates = QPushButton("Clear dates")
        self._m_clear_dates.setFixedHeight(_SMART_INPUT_H)
        self._m_clear_dates.setAutoDefault(False)
        self._m_clear_dates.setToolTip("Set Date From and Date To back to Any")
        self._m_clear_dates.setStyleSheet(_BTN_STYLE + _COMPACT_BTN_PAD)
        _squeeze(self._m_clear_dates, 80, 100)
        self._m_clear_dates.clicked.connect(self._clear_smart_dates)
        clear_col = QVBoxLayout()
        clear_col.setSpacing(3)
        clear_col.addWidget(_lbl(""))
        clear_col.addWidget(self._m_clear_dates)
        hl.addLayout(clear_col)
        row.add_wrap_gap(hl)

        m_find, m_reset = self._make_find_reset()
        m_find.setFixedHeight(_SMART_INPUT_H)
        m_reset.setFixedHeight(_SMART_INPUT_H)
        self._find_btns.append(m_find)
        hl.addLayout(_btn_col(m_find, m_reset))
        return w

    async def _load_smart_suggestions(self) -> None:
        from tahmeed.services.category_service import get_payment_target_categories
        from tahmeed.services.payee_service import get_payee_names

        try:
            cats = await get_payment_target_categories()
            self._m_item.set_values(sorted({c.name for c in cats if c.name}, key=str.lower))
        except Exception:
            self._smart_suggestions_loaded = False
        try:
            self._m_payee.set_values(await get_payee_names())
        except Exception:
            self._smart_suggestions_loaded = False

    def _clear_smart_dates(self) -> None:
        self._m_from.setDate(_ANY_DATE)
        self._m_to.setDate(_ANY_DATE)

    def _smart_form_params(self) -> dict:
        return dict(
            item=self._m_item.text().strip(),
            description=self._m_desc.text().strip(),
            payee=self._m_payee.text().strip(),
            truck=self._m_truck.text().strip(),
            date_from=_optional_date(self._m_from),
            date_to=_optional_date(self._m_to),
        )

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
        find.setFixedHeight(28)
        _squeeze(find, _BTN_MIN_W, 66)
        find.setAutoDefault(False)
        find.setStyleSheet(_PRIMARY_BTN + _COMPACT_BTN_PAD)
        find.clicked.connect(self._do_find)
        reset = QPushButton("Reset")
        reset.setFixedHeight(28)
        _squeeze(reset, _BTN_MIN_W, 66)
        reset.setAutoDefault(False)
        reset.setStyleSheet(_BTN_STYLE + _COMPACT_BTN_PAD)
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
        self._table_stack.addWidget(self._build_smart_page())
        self._table_stack.addWidget(self._uploads_table)
        lay.addWidget(self._table_stack)
        self._loading = LoadingOverlay(wrap, "Loading…")
        return wrap

    def _build_uploads_table(self) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(9)
        t.setHorizontalHeaderLabels([
            "SN", "Filename", "Primary Date", "Rows", "Cashier", "Total",
            "Issues", "Imported", "",
        ])
        t.setStyleSheet(_TABLE_SS)
        t.setAlternatingRowColors(False)
        hh = t.horizontalHeader()
        for i in range(9):
            hh.setSectionResizeMode(i, QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(_U_COL_FILE, QHeaderView.Stretch)
        t.setColumnWidth(_U_COL_SN, 44)
        t.setColumnWidth(_U_COL_DATE, 110)
        t.setColumnWidth(_U_COL_COUNT, 60)
        t.setColumnWidth(_U_COL_CASHIER, 120)
        t.setColumnWidth(_U_COL_TOTAL, 160)
        t.setColumnWidth(_U_COL_ISSUES, 90)
        t.setColumnWidth(_U_COL_WHEN, 140)
        t.setColumnWidth(_U_COL_ACT, 170)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setDefaultSectionSize(_ROW_H)
        t.itemSelectionChanged.connect(self._on_selection_changed)
        t.doubleClicked.connect(self._on_open_upload)
        t.verticalScrollBar().valueChanged.connect(self._on_uploads_scroll)
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
        t.verticalScrollBar().valueChanged.connect(self._on_adv_scroll)
        return t

    def _build_smart_page(self) -> QWidget:
        page = QWidget()
        self._smart_layout = QStackedLayout(page)
        self._smart_hint = QLabel(_SMART_HINT)
        self._smart_hint.setAlignment(Qt.AlignCenter)
        self._smart_hint.setWordWrap(True)
        self._smart_hint.setStyleSheet(
            "color:#6b7280;font-size:13px;background:#ffffff;padding:24px;"
        )
        self._smart_table = self._build_smart_table()
        self._smart_layout.addWidget(self._smart_hint)
        self._smart_layout.addWidget(self._smart_table)
        self._smart_layout.setCurrentWidget(self._smart_hint)
        return page

    def _build_smart_table(self) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(len(_SMART_HEADERS))
        t.setHorizontalHeaderLabels(_SMART_HEADERS)
        t.setStyleSheet(_TABLE_SS)
        t.setAlternatingRowColors(False)
        hh = t.horizontalHeader()
        for i in range(len(_SMART_HEADERS)):
            hh.setSectionResizeMode(i, QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(_M_COL_DESC, QHeaderView.Stretch)
        t.setColumnWidth(_M_COL_DATE,    100)
        t.setColumnWidth(_M_COL_ITEM,    110)
        t.setColumnWidth(_M_COL_DESC,    220)
        t.setColumnWidth(_M_COL_PAYEE,   130)
        t.setColumnWidth(_M_COL_TRUCK,    90)
        t.setColumnWidth(_M_COL_MEMO,     80)
        t.setColumnWidth(_M_COL_REFUND,  105)
        t.setColumnWidth(_M_COL_TZS,     105)
        t.setColumnWidth(_M_COL_USD,      85)
        t.setColumnWidth(_M_COL_RECEIPT,  82)
        t.setColumnWidth(_M_COL_CASHIER, 110)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setDefaultSectionSize(_ROW_H)
        t.doubleClicked.connect(self._on_go_to)
        t.itemSelectionChanged.connect(self._on_selection_changed)
        t.verticalScrollBar().valueChanged.connect(self._on_smart_scroll)
        return t

    def _show_smart_hint(self, text: str = _SMART_HINT) -> None:
        self._smart_hint.setText(text)
        self._smart_layout.setCurrentWidget(self._smart_hint)

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
        self._count_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

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
        self._show_mode(mode)

    def _show_mode(self, mode: int) -> None:
        self._current_mode = mode
        self._filter_stack.setCurrentIndex(mode)
        self._table_stack.setCurrentIndex(mode)
        self._apply_responsive()
        for i, btn in enumerate(self._mode_btns):
            btn.setChecked(i == mode)
        if mode == _MODE_SIMPLE:
            self._count_label.setText("Days shown: —")
        elif mode == _MODE_UPLOADS:
            self._count_label.setText("Uploads: —")
        elif mode == _MODE_SMART:
            self._count_label.setText("Matches: —")
            if not self._smart_suggestions_loaded:
                self._smart_suggestions_loaded = True
                asyncio.ensure_future(self._load_smart_suggestions())
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
        return params

    def _do_find(self) -> None:
        if (
            self._current_mode == _MODE_SMART
            and not _has_smart_filters(self._smart_form_params())
        ):
            self._find_generation += 1
            self._find_in_flight = False
            if self._loading is not None:
                self._loading.hide_loading()
            for btn in self._find_btns:
                btn.setEnabled(True)
            self._clear_smart_results()
            return
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
            elif mode == _MODE_SMART:
                msg = "Searching all days…"
            else:
                msg = "Loading transactions…"
            self._loading.show_loading(msg)
        try:
            if mode not in (_MODE_UPLOADS, _MODE_SMART):
                await self._ensure_period_ready()
                if gen != self._find_generation:
                    return
            if mode == _MODE_UPLOADS:
                await self._load_uploads()
                return
            if mode == _MODE_SMART:
                await self._load_smart(gen)
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
                page = await get_transactions_flat_page(
                    **params, limit=_ADV_PAGE_SIZE,
                )
                if gen != self._find_generation:
                    return
                self._adv_params = params
                self._results_advanced = list(page.items)
                self._adv_total = int(page.total or 0)
                self._user_names = dict(page.cashier_names or {})
                self._populate_advanced(self._results_advanced, reset=True)
                self._schedule_adv_fill()
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

        page = await list_daily_uploads_page(limit=_UPLOAD_PAGE_SIZE, skip=0)
        # Defense: never show groups without a real batch id.
        uploads = [u for u in page.items if u.get("_id")]
        self._results_uploads = uploads
        self._uploads_total = int(page.total or 0)
        self._upload_cashier_names = dict(page.cashier_names or {})
        self._populate_uploads(uploads, reset=True)
        self._fill_uploads_if_needed()

    async def _load_more_uploads(self) -> None:
        if self._uploads_scroll_loading or self._find_in_flight:
            return
        if len(self._results_uploads) >= self._uploads_total:
            return
        from tahmeed.services.daily_import_service import list_daily_uploads_page

        self._uploads_scroll_loading = True
        self._update_uploads_count_label(loading=True)
        try:
            skip = len(self._results_uploads)
            page = await list_daily_uploads_page(
                limit=_UPLOAD_PAGE_SIZE, skip=skip,
            )
            more = [u for u in page.items if u.get("_id")]
            if not more:
                self._uploads_total = len(self._results_uploads)
                return
            self._uploads_total = int(page.total or self._uploads_total)
            names = page.cashier_names or {}
            self._upload_cashier_names.update(names)
            start = len(self._results_uploads)
            self._results_uploads.extend(more)
            self._populate_uploads(more, reset=False, start_row=start)
            self._fill_uploads_if_needed()
        finally:
            self._uploads_scroll_loading = False
            self._update_uploads_count_label()

    def _on_uploads_scroll(self, _value: int) -> None:
        if self._current_mode != _MODE_UPLOADS:
            return
        bar = self._uploads_table.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if bar.value() >= bar.maximum() - 24:
            asyncio.ensure_future(self._load_more_uploads())

    def _fill_uploads_if_needed(self) -> None:
        """Keep loading while the viewport has no scrollbar yet."""
        bar = self._uploads_table.verticalScrollBar()
        if (
            not self._uploads_scroll_loading
            and not self._find_in_flight
            and len(self._results_uploads) < self._uploads_total
            and bar.maximum() <= 0
        ):
            asyncio.ensure_future(self._load_more_uploads())

    def _update_uploads_count_label(self, *, loading: bool = False) -> None:
        loaded = len(self._results_uploads)
        total = self._uploads_total
        if loading or self._uploads_scroll_loading:
            suffix = "  ·  Loading…"
        elif total and loaded < total:
            suffix = "  ·  Scroll for more"
        else:
            suffix = ""
        if total:
            self._count_label.setText(f"Uploads: {loaded:,} of {total:,}{suffix}")
        else:
            self._count_label.setText(f"Uploads: {loaded:,}{suffix}")

    # ── Smart Search loading ──────────────────────────────────────────────────

    def _clear_smart_results(self, hint: str = _SMART_HINT) -> None:
        self._results_smart = []
        self._smart_total = 0
        self._smart_summary = None
        self._smart_names = {}
        self._smart_params = {}
        self._smart_searched = False
        self._smart_table.setRowCount(0)
        self._show_smart_hint(hint)
        self._update_smart_count_label()
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(False)

    async def _load_smart(self, gen: int) -> None:
        params = self._smart_form_params()
        page = await smart_search_page(**params, skip=0, limit=_SMART_PAGE_SIZE)
        if gen != self._find_generation:
            return
        self._smart_params = params
        self._results_smart = list(page.items)
        self._smart_total = int(page.total or 0)
        self._smart_summary = page.summary
        self._smart_names = dict(page.cashier_names or {})
        self._smart_searched = True
        self._populate_smart(self._results_smart, reset=True)
        if not self._results_smart:
            self._show_smart_hint("No entries match these filters.")
        self._schedule_smart_fill()

    async def _load_more_smart(self) -> None:
        if self._smart_scroll_loading or self._find_in_flight:
            return
        if not self._smart_searched or len(self._results_smart) >= self._smart_total:
            return
        gen = self._find_generation
        self._smart_scroll_loading = True
        self._update_smart_count_label()
        try:
            page = await smart_search_page(
                **self._smart_params,
                skip=len(self._results_smart),
                limit=_SMART_PAGE_SIZE,
            )
            if gen != self._find_generation:
                return
            if not page.items:
                self._smart_total = len(self._results_smart)
                return
            self._smart_total = int(page.total or self._smart_total)
            self._smart_names.update(page.cashier_names or {})
            start = len(self._results_smart)
            self._results_smart.extend(page.items)
            self._populate_smart(page.items, reset=False, start_row=start)
        except Exception as exc:
            if gen == self._find_generation:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Search Error", str(exc))
        finally:
            self._smart_scroll_loading = False
            self._update_smart_count_label()
        self._schedule_smart_fill()

    def _on_smart_scroll(self, _value: int) -> None:
        if self._current_mode != _MODE_SMART:
            return
        bar = self._smart_table.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if bar.value() >= bar.maximum() - 24:
            asyncio.ensure_future(self._load_more_smart())

    def _schedule_smart_fill(self) -> None:
        """Keep loading while the viewport has no scrollbar yet."""
        bar = self._smart_table.verticalScrollBar()
        if (
            self._smart_searched
            and not self._smart_scroll_loading
            and len(self._results_smart) < self._smart_total
            and bar.maximum() <= 0
        ):
            asyncio.ensure_future(self._load_more_smart())

    def _update_smart_count_label(self) -> None:
        if not self._smart_searched:
            self._count_label.setText("Matches: —")
            return
        loaded = len(self._results_smart)
        total = self._smart_total
        if loaded < total:
            text = f"Matches: {loaded:,} of {total:,}"
        else:
            text = f"Matches: {total:,}"
        s = self._smart_summary
        if s is not None:
            day_word = "day" if s.days == 1 else "days"
            text += f"  ·  {s.days:,} {day_word}  ·  TZS {s.total_tzs:,.0f}"
            if s.total_usd:
                text += f"  ·  USD {s.total_usd:,.2f}"
        if self._smart_scroll_loading:
            text += "  ·  Loading…"
        elif loaded < total:
            text += "  ·  Scroll for more"
        self._count_label.setText(text)

    def _populate_smart(
        self,
        txs: List[Transaction],
        *,
        reset: bool = True,
        start_row: int = 0,
    ) -> None:
        t = self._smart_table
        if reset:
            start_row = 0
            t.setRowCount(len(txs))
        else:
            t.setRowCount(start_row + len(txs))
        if txs:
            self._smart_layout.setCurrentWidget(t)
        names = self._smart_names or {}

        for offset, tx in enumerate(txs):
            i = start_row + offset
            row_bg = _stripe_bg(i)
            d = tx.date
            date_str = d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d)
            t.setItem(i, _M_COL_DATE, _ro(date_str, bg=row_bg))
            t.setItem(i, _M_COL_ITEM, _ro(tx.item or tx.category_name or "", bg=row_bg))
            t.setItem(i, _M_COL_DESC, _ro(tx.description or "", bg=row_bg))
            t.setItem(i, _M_COL_PAYEE, _ro(tx.payee or "", bg=row_bg))
            t.setItem(i, _M_COL_TRUCK, _ro(tx.truck_number or "", bg=row_bg))
            t.setItem(i, _M_COL_MEMO, _ro(tx.memo or "", bg=row_bg))

            if tx.notes_flag:
                ref_it = _ro("REFUND TO FLOAT", Qt.AlignCenter, bg=row_bg)
                ref_it.setForeground(QColor("#EA580C"))
            else:
                ref_it = _ro("", bg=row_bg)
            t.setItem(i, _M_COL_REFUND, ref_it)

            tzs, usd = tx.money_parts()
            tzs_it = _ro(
                f"TZS {tzs:,.0f}" if tzs else "—",
                Qt.AlignRight | Qt.AlignVCenter,
                bg=row_bg,
            )
            if tzs < 0:
                tzs_it.setForeground(QColor("#dc2626"))
            t.setItem(i, _M_COL_TZS, tzs_it)
            usd_it = _ro(
                f"USD {usd:,.2f}" if usd else "—",
                Qt.AlignRight | Qt.AlignVCenter,
                bg=row_bg,
            )
            if usd < 0:
                usd_it.setForeground(QColor("#dc2626"))
            t.setItem(i, _M_COL_USD, usd_it)

            t.setItem(i, _M_COL_RECEIPT, _receipt_item(tx.receipt_status, row_bg))
            cashier = names.get(tx.cashier_id, "—") if tx.cashier_id else "—"
            t.setItem(i, _M_COL_CASHIER, _ro(cashier, bg=row_bg))
            _finish_table_row(t, i, row_bg)

        self._update_smart_count_label()
        self._goto_btn.setEnabled(bool(t.selectedItems()))
        self._export_btn.setEnabled(bool(self._results_smart))

    def _populate_uploads(
        self,
        uploads: list,
        *,
        reset: bool = True,
        start_row: int = 0,
    ) -> None:
        names = self._upload_cashier_names or {}
        t = self._uploads_table
        if reset:
            t.setRowCount(len(uploads))
            start_row = 0
        else:
            t.setRowCount(start_row + len(uploads))

        for offset, u in enumerate(uploads):
            i = start_row + offset
            row_bg = _stripe_bg(i)
            upload_id = str(u.get("_id") or "")
            fname = u.get("source_filename") or "—"
            t.setItem(
                i, _U_COL_SN,
                _ro(str(i + 1), Qt.AlignCenter, bg=row_bg),
            )
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

            total_txt = _format_upload_total(u)
            tot_it = _ro(
                total_txt,
                Qt.AlignRight | Qt.AlignVCenter,
                bg=row_bg,
            )
            tzs = float(u.get("total_tzs") or 0.0)
            if tzs < 0 or float(u.get("total_usd") or 0.0) < 0:
                tot_it.setForeground(QColor("#dc2626"))
            t.setItem(i, _U_COL_TOTAL, tot_it)

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

        self._update_uploads_count_label()
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(len(self._results_uploads) > 0)

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
        self,
        txs: List[Transaction],
        *,
        reset: bool = True,
        start_row: int = 0,
    ) -> None:
        t = self._adv_table
        if reset:
            start_row = 0
            t.setRowCount(len(txs))
        else:
            t.setRowCount(start_row + len(txs))
        # Names filled by composite list payload in _async_find / scroll pages.
        names = getattr(self, "_user_names", {}) or {}

        for offset, tx in enumerate(txs):
            i = start_row + offset
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

            t.setItem(i, _A_COL_RECEIPT, _receipt_item(tx.receipt_status, row_bg))
            t.setItem(i, _A_COL_OWNERSHIP, _ro(tx.ownership or "", bg=row_bg))
            t.setItem(i, _A_COL_APR, _ro(tx.approver or "", bg=row_bg))
            cashier = names.get(tx.cashier_id, "—") if tx.cashier_id else "—"
            edited = names.get(tx.last_edited_by, "—") if tx.last_edited_by else "—"
            t.setItem(i, _A_COL_CASHIER, _ro(cashier, bg=row_bg))
            t.setItem(i, _A_COL_EDITED_BY, _ro(edited, bg=row_bg))
            _finish_table_row(t, i, row_bg)

        self._update_adv_count_label()
        self._export_btn.setEnabled(bool(self._results_advanced))

    def _update_adv_count_label(self) -> None:
        loaded = len(self._results_advanced)
        total = max(self._adv_total, loaded)
        if loaded < total:
            text = f"Transactions: {loaded:,} of {total:,}"
        else:
            text = f"Transactions: {loaded:,}"
        if self._adv_scroll_loading:
            text += "  ·  Loading…"
        elif loaded < total:
            text += "  ·  Scroll for more"
        self._count_label.setText(text)

    async def _load_more_advanced(self) -> None:
        if self._adv_scroll_loading or self._find_in_flight:
            return
        if len(self._results_advanced) >= self._adv_total:
            return
        gen = self._find_generation
        self._adv_scroll_loading = True
        self._update_adv_count_label()
        try:
            page = await get_transactions_flat_page(
                **self._adv_params,
                skip=len(self._results_advanced),
                limit=_ADV_PAGE_SIZE,
            )
            if gen != self._find_generation:
                return
            if not page.items:
                self._adv_total = len(self._results_advanced)
                return
            self._adv_total = int(page.total or self._adv_total)
            names = getattr(self, "_user_names", None) or {}
            names.update(page.cashier_names or {})
            self._user_names = names
            start = len(self._results_advanced)
            self._results_advanced.extend(page.items)
            self._populate_advanced(page.items, reset=False, start_row=start)
        except Exception as exc:
            if gen == self._find_generation:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Search Error", str(exc))
        finally:
            self._adv_scroll_loading = False
            if gen == self._find_generation and self._current_mode == _MODE_ADVANCED:
                self._update_adv_count_label()
        self._schedule_adv_fill()

    def _on_adv_scroll(self, _value: int) -> None:
        if self._current_mode != _MODE_ADVANCED:
            return
        bar = self._adv_table.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if bar.value() >= bar.maximum() - 24:
            asyncio.ensure_future(self._load_more_advanced())

    def _schedule_adv_fill(self) -> None:
        """Keep loading while the viewport has no scrollbar yet."""
        bar = self._adv_table.verticalScrollBar()
        if (
            not self._adv_scroll_loading
            and len(self._results_advanced) < self._adv_total
            and bar.maximum() <= 0
        ):
            asyncio.ensure_future(self._load_more_advanced())

    # ── Reset ─────────────────────────────────────────────────────────────────

    def _reset_filters(self) -> None:
        if self._current_mode == _MODE_UPLOADS:
            self._do_find()
            return
        if self._current_mode == _MODE_SMART:
            for edit in (self._m_item, self._m_desc, self._m_payee, self._m_truck):
                edit.clear()
            self._clear_smart_dates()
            self._do_find()
            return
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
        active = {
            _MODE_SIMPLE: self._simple_table,
            _MODE_SMART: self._smart_table,
        }.get(self._current_mode, self._adv_table)
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
            return
        if self._current_mode == _MODE_SMART:
            row = self._smart_table.currentRow()
            if row < 0 or row >= len(self._results_smart):
                return
            tx = self._results_smart[row]
            # Imported rows live on their upload's register day, not the Excel date.
            d = _as_py_date(tx.import_primary_date or tx.date)
            if d is None:
                return
            term = (tx.description or tx.truck_number or "").strip()
            self.go_to_date.emit(d, term)

    # ── Export ────────────────────────────────────────────────────────────────

    def _on_export(self) -> None:
        if self._current_mode == _MODE_UPLOADS:
            lines = [
                "SN\tFile\tPrimary date\tEntries\tCashier\tTotal\tIssues\tUploaded"
            ]
            name_map = getattr(self, "_upload_cashier_names", {}) or {}
            for sn, u in enumerate(self._results_uploads, start=1):
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
                    str(sn),
                    u.get("source_filename") or "",
                    date_str,
                    str(int(u.get("count") or 0)),
                    name_map.get(cid, "") if cid else "",
                    _format_upload_total(u),
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
        elif self._current_mode == _MODE_SMART:
            lines = ["\t".join(_SMART_HEADERS)]
            names = self._smart_names or {}
            for tx in self._results_smart:
                d = tx.date
                tzs, usd = tx.money_parts()
                lines.append("\t".join([
                    d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d),
                    tx.item or tx.category_name or "",
                    tx.description or "",
                    tx.payee or "",
                    tx.truck_number or "",
                    tx.memo or "",
                    "REFUND TO FLOAT" if tx.notes_flag else "",
                    f"{tzs:.0f}",
                    f"{usd:.2f}" if usd else "",
                    (tx.receipt_status or "").capitalize(),
                    names.get(tx.cashier_id, "") if tx.cashier_id else "",
                ]))
            n = len(self._results_smart)
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


def _receipt_item(receipt_status: Optional[str], bg: str) -> QTableWidgetItem:
    status = (receipt_status or "pending").strip().lower()
    label = _RECEIPT_LABELS.get(status, status.upper() if status else "—")
    it = _ro(label, Qt.AlignCenter, bg=bg)
    it.setForeground(QColor(_RECEIPT_COLORS.get(status, "#6b7280")))
    return it


def _has_smart_filters(params: dict) -> bool:
    return any(
        (str(params.get(key) or "").strip())
        for key in ("item", "description", "payee", "truck")
    ) or bool(params.get("date_from") or params.get("date_to"))


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


class _WrapRow:
    """Two field groups: side by side on wide pages, stacked below ``breakpoint``."""

    def __init__(
        self,
        host: QWidget,
        *,
        breakpoint: int,
        first_stretch: int = 0,
        second_stretch: int = 1,
    ) -> None:
        self.breakpoint = breakpoint
        self.wrapped = False
        self.layout = QBoxLayout(QBoxLayout.LeftToRight, host)
        self.layout.setContentsMargins(0, 4, 0, 4)
        self.layout.setSpacing(8)
        self.first = QHBoxLayout()
        self.second = QHBoxLayout()
        for row in (self.first, self.second):
            row.setSpacing(8)
        self.layout.addLayout(self.first, first_stretch)
        self.layout.addLayout(self.second, second_stretch)
        self._wrap_gaps: list[QSpacerItem] = []

    def add_wrap_gap(self, row: QHBoxLayout) -> None:
        """Spacer that only expands once the groups are stacked.

        On one row it must stay zero-width, or it soaks up the space the
        fields need to reach their full width.
        """
        gap = QSpacerItem(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self._wrap_gaps.append(gap)
        row.addSpacerItem(gap)

    def finish(self) -> None:
        self.add_wrap_gap(self.first)

    def set_wrapped(self, wrapped: bool) -> None:
        if wrapped == self.wrapped:
            return
        self.wrapped = wrapped
        self.layout.setDirection(
            QBoxLayout.TopToBottom if wrapped else QBoxLayout.LeftToRight
        )
        self.layout.setSpacing(6 if wrapped else 8)
        policy = QSizePolicy.Expanding if wrapped else QSizePolicy.Fixed
        for gap in self._wrap_gaps:
            gap.changeSize(0, 0, policy, QSizePolicy.Minimum)
        self.layout.invalidate()


def _squeeze(widget: QWidget, min_w: int, max_w: Optional[int] = None) -> QWidget:
    """Full ``max_w`` on wide screens; shrink toward ``min_w`` instead of overlapping."""
    widget.setMinimumWidth(min_w)
    widget.setMaximumWidth(max_w if max_w else _QWIDGETSIZE_MAX)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return widget


def _combo(width: int, min_width: int) -> QComboBox:
    c = QComboBox()
    c.setFixedHeight(28)
    _squeeze(c, min_width, width)
    c.setStyleSheet(_FIELD_SS)
    return c


def _lineedit(placeholder: str, width: int, min_width: int) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFixedHeight(28)
    _squeeze(e, min_width, width)
    e.setStyleSheet(_FIELD_SS)
    return e


def _date_edit(d: date) -> QDateEdit:
    de = QDateEdit()
    de.setCalendarPopup(True)
    de.setDisplayFormat("dd/MM/yyyy")
    _squeeze(de, _DATE_MIN_W, 115)
    de.setFixedHeight(28)
    de.setDate(QDate(d.year, d.month, d.day))
    de.setStyleSheet(_FIELD_SS)
    style_calendar_popup(de)
    return de


_ANY_DATE = QDate(2000, 1, 1)


class _AnyDateCalendarFilter(QObject):
    """Open the popup on today's month while the edit still shows "Any"."""

    def __init__(self, edit: QDateEdit) -> None:
        super().__init__(edit)
        self._edit = edit

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show and self._edit.date() == _ANY_DATE:
            today = QDate.currentDate()
            watched.setCurrentPage(today.year(), today.month())
        return False


def _optional_date_edit() -> QDateEdit:
    """Date edit whose minimum value reads "Any" (no bound)."""
    de = QDateEdit()
    de.setCalendarPopup(True)
    de.setDisplayFormat("dd/MM/yyyy")
    _squeeze(de, _DATE_MIN_W, 115)
    de.setFixedHeight(28)
    de.setMinimumDate(_ANY_DATE)
    de.setSpecialValueText("Any")
    de.setDate(_ANY_DATE)
    de.setStyleSheet(_FIELD_SS)
    style_calendar_popup(de)
    cal = de.calendarWidget()
    cal.installEventFilter(_AnyDateCalendarFilter(de))
    return de


def _optional_date(de: QDateEdit) -> Optional[date]:
    qd = de.date()
    if qd == _ANY_DATE:
        return None
    return _qdate_to_py(qd)


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
