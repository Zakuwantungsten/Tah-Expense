"""Master Expenses ledger table — Excel-like inline edit (cashier register style).

Contiguous cell selection, edit mode, copy/paste/fill-down/fill-right/undo,
and dirty-row tracking. Persistence is owned by MasterExpensesWidget.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtWidgets import (
    QApplication, QFrame, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QMenu, QAbstractItemDelegate, QDialog, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QObject, QEvent, QTimer
from PySide6.QtGui import QBrush, QKeyEvent, QAction, QColor

from tahmeed.models.transaction import Transaction, pack_money
from tahmeed.services.truck_format import (
    normalize_truck_number, try_match_fleet, normalize_place_label,
    DEFAULT_PLACE_LABELS, merge_allowed_labels,
)
from tahmeed.ui.dialogs.truck_correction_dialog import TruckCorrectionDialog, TruckIssue
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.excel_column_filter import (
    ExcelFilterHeaderView, SORT_ASC, cascade_column_values,
)
from tahmeed.ui.accountant.separate_expenses import (
    _make_table, _cell, _finish_table_row, _stripe_bg, _fmt_num,
)
from tahmeed.ui.cashier.register_delegates import (
    EDIT_BG, DIRTY_BG,
    _ExcelCellDelegate, _ItemDelegate, _DescriptionDelegate, _TruckDelegate,
    _DateDelegate, _RefFloatDelegate, _TZSDelegate, _ReceiptDelegate,
    _parse_optional_date, _parse_amount_text, _parse_optional_amount_text,
    _receipt_paste_value,
    _norm_receipt_text, _VALID_RCPT, format_register_date,
)

# ── Column layout (must match master_expenses._COLS) ─────────────────────────
_COLS = [
    ("S/NO", "center", None),
    ("DATE", "left", "date"),
    ("ITEM", "left", "category_name"),
    ("DESCRIPTION", "left", "description"),
    ("TRUCK NO", "left", "truck_number"),
    ("MEMO", "left", "memo"),
    ("REF_FLOAT", "left", "ref_float"),
    ("TZS", "right", "amount"),
    ("USD", "right", "amount"),
    ("RECEIPT", "center", "receipt_status"),
    ("OWNERSHIP", "left", "ownership"),
    ("APPROVED BY", "left", "approver"),
    ("CASHIER", "left", None),
]

_COL_DEFAULTS = [52, 72, 110, 0, 95, 120, 110, 110, 100, 100, 90, 100, 100]
_DESC_COL = 3
_COL_DATE = 1
_COL_ITEM = 2
_COL_DESC = 3
_COL_TRUCK = 4
_COL_MEMO = 5
_COL_REF = 6
_COL_TZS = 7
_COL_USD = 8
_COL_RCPT = 9
_COL_OWN = 10
_COL_APP = 11
_COL_CASH = 12

_EDITABLE_COLS: Set[int] = {
    _COL_DATE, _COL_ITEM, _COL_DESC, _COL_TRUCK, _COL_MEMO, _COL_REF,
    _COL_TZS, _COL_USD, _COL_RCPT, _COL_OWN, _COL_APP,
}
_READONLY_COLS: Set[int] = {0, _COL_CASH}
_UPPER_SKIP: Set[int] = {0, _COL_DATE, _COL_RCPT, _COL_TZS, _COL_USD, _COL_CASH}
_FILTERABLE_COLS: Set[int] = set(range(len(_COLS))) - {0}
_SORT_KINDS = {1: "date", _COL_TZS: "number", _COL_USD: "number", _COL_TRUCK: "truck"}
# Excel ▾ col index → accountant_service column_filters key
_COL_FIELD: Dict[int, str] = {
    _COL_DATE: "date",
    _COL_ITEM: "item",
    _COL_DESC: "description",
    _COL_TRUCK: "truck_number",
    _COL_MEMO: "memo",
    _COL_REF: "ref_float",
    _COL_TZS: "tzs",
    _COL_USD: "usd",
    _COL_RCPT: "receipt_status",
    _COL_OWN: "ownership",
    _COL_APP: "approver",
    _COL_CASH: "cashier",
}
_TX_ID_ROLE = Qt.UserRole
_UNDO_LIMIT = 40

_WHITE = "#FFFFFF"
_BORDER = "#E5E7EB"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"
_AMBER = "#D97706"
_RED = "#DC2626"
_GREEN = "#16A34A"
_HDR_BG = "#F1F5F9"

_RECEIPT_MAP = {
    "received": ("Received", _GREEN),
    "pending": ("Pending", _AMBER),
    "missing": ("No Receipt", _RED),
    "no_receipt": ("No Receipt", _RED),
}

_TABLE_SS = (
    f"QTableWidget {{"
    f"  background: {_WHITE};"
    f"  gridline-color: {_BORDER};"
    f"  font-size: 11px;"
    f"  font-family:'Segoe UI';"
    f"  color: {_T1};"
    f"  border: none;"
    f"  selection-background-color: #cde0f5;"
    f"  selection-color: #1B2B4B;"
    f"}}"
    f"QTableWidget::item {{"
    f"  padding: 2px 8px;"
    f"  border: none;"
    f"}}"
    f"QHeaderView::section {{"
    f"  background: {_HDR_BG};"
    f"  color: {_T2};"
    f"  font-size: 10px;"
    f"  font-weight: 600;"
    f"  font-family:'Segoe UI';"
    f"  border: none;"
    f"  border-bottom: 1px solid {_BORDER};"
    f"  padding: 0 18px 0 8px;"
    f"  min-height: 28px;"
    f"}}"
    f"QHeaderView::section:hover {{ background: #E2E8F0; }}"
    f"QScrollBar:vertical {{"
    f"  background: transparent; width: 8px; margin: 0;"
    f"}}"
    f"QScrollBar::handle:vertical {{"
    f"  background: #D1D5DB; border-radius: 4px; min-height: 24px;"
    f"}}"
    f"QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}"
)


def _normalize_receipt(status: str) -> str:
    s = (status or "pending").strip().lower().replace(" ", "_")
    if s in ("no_receipt", "no", "n/a", "none", "missing"):
        return "no_receipt"
    if s in _RECEIPT_MAP:
        return s
    if "received" in s or s in ("yes", "receipt", "rcvd"):
        return "received"
    return "pending"


def _ref_float_display(tx: Transaction) -> str:
    text = (getattr(tx, "ref_float", None) or "").strip()
    if text:
        return text.upper()
    if tx.notes_flag:
        return "REFUND TO FLOAT"
    return ""


def _short_name(name: str) -> str:
    parts = (name or "").strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1][0]}."
    return name or "—"


def _currency_key(tx: Transaction) -> str:
    return (tx.currency or "TZS").upper()


def _is_tzs(currency: str) -> bool:
    return currency in {"TZS", "TSH", "TZ"}


def _amount_cells(tx: Transaction) -> tuple[str, str]:
    tzs, usd = tx.money_parts()
    tzs_txt = _fmt_num(tzs, "", 0) if tzs else ""
    usd_txt = _fmt_num(usd, "", 2) if usd else ""
    cur = _currency_key(tx)
    if cur not in {"TZS", "TSH", "TZ", "USD"} and tx.amount_usd is None:
        return _fmt_num(tx.amount, f"{cur} ", 2), ""
    return tzs_txt, usd_txt


def _plain(text: str) -> str:
    t = (text or "").strip()
    return "" if t in {"", "—", "-"} else t


def _master_upper(col: int, text: str) -> str:
    return text.upper() if col not in _UPPER_SKIP else text


class _TableKeyFilter(QObject):
    def __init__(self, handler):
        super().__init__()
        self._handler = handler

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.KeyPress:
            self._handler(event)
            return True
        return False


class MasterLedgerTable(QFrame):
    """Scrolling Master ledger with Excel ▾ filters and register-style edit mode."""

    sort_changed = Signal(str, bool)
    col_filter_changed = Signal()
    edit_state_changed = Signal(bool, int)  # (edit_mode, dirty_count)
    bulk_set_item_requested = Signal()
    save_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background: {_WHITE}; border: none; }}")
        self._sort_field = "date"
        self._sort_asc = False
        self._col_filters: Dict[int, Set[str]] = {}
        # Distinct values for the whole selected range (from DB), keyed by col.
        self._col_value_cache: Dict[int, Set[str]] = {}
        self._txs: List[Optional[Transaction]] = []
        self._edit_mode = False
        self._dirty_rows: Set[int] = set()
        self._bulk_mutating = False
        self._undo_stack: List[List[Tuple[int, int, str]]] = []
        # No cut marquee until the user copies/cuts — then dashed outline marks clipboard source.
        self._cut_cells: Set[Tuple[int, int]] = set()
        self._item_names: List[str] = []
        self._fleet_numbers: Set[str] = set()
        self._fleet_kinds: Dict[str, str] = {}
        self._allowed_truck_labels: Set[str] = set(DEFAULT_PLACE_LABELS)
        self._people_names: List[str] = []
        self._default_year = date.today().year
        self._can_add_fleet = True
        self._pending_truck_issues: Dict[int, TruckIssue] = {}
        self._truck_dialog_scheduled = False
        self._open_truck_dialog = None
        self._build()

    def _build(self) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._tbl = _make_table([c[0] for c in _COLS])
        self._tbl.setStyleSheet(_TABLE_SS)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectItems)
        self._tbl.setSelectionMode(QAbstractItemView.ContiguousSelection)
        self._tbl.setShowGrid(True)
        self._tbl.setTabKeyNavigation(False)
        self._tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tbl.customContextMenuRequested.connect(self._show_context_menu)
        self._tbl.itemChanged.connect(self._on_item_changed)
        self._tbl._grid_owner = self

        self._install_delegates()

        filter_hdr = ExcelFilterHeaderView(
            self._tbl,
            filterable_columns=_FILTERABLE_COLS,
            sort_kinds=_SORT_KINDS,
        )
        filter_hdr.set_value_provider(self._filter_menu_values)
        filter_hdr.set_label_provider(
            lambda c: _COLS[c][0] if 0 <= c < len(_COLS) else "",
        )
        filter_hdr.filter_changed.connect(self._on_col_filter_changed)
        filter_hdr.sort_requested.connect(self._on_excel_sort)
        self._tbl.setHorizontalHeader(filter_hdr)

        hdr = self._tbl.horizontalHeader()
        hdr.setSortIndicatorShown(True)
        hdr.setSectionsMovable(False)
        hdr.setMinimumSectionSize(40)
        bind_column_width_persistence(
            self._tbl,
            "master_expenses_v2",
            _COL_DEFAULTS,
            stretch_columns=[_DESC_COL],
        )
        hdr.sectionClicked.connect(self._on_header_click)
        self._update_sort_indicator()

        self._key_filter = _TableKeyFilter(self._table_key_press)
        self._tbl.installEventFilter(self._key_filter)

        vl.addWidget(self._tbl)

    def _install_delegates(self) -> None:
        t = self._tbl
        t.setItemDelegate(_ExcelCellDelegate(t))
        t.setItemDelegateForColumn(
            _COL_ITEM, _ItemDelegate(lambda: list(self._item_names), t),
        )
        t.setItemDelegateForColumn(
            _COL_DESC,
            _DescriptionDelegate(
                cat_getter=lambda _name: None,
                subs_getter=lambda _name: [],
                parent=t,
            ),
        )
        t.setItemDelegateForColumn(
            _COL_TRUCK,
            _TruckDelegate(lambda: sorted(self._fleet_numbers), t),
        )
        t.setItemDelegateForColumn(
            _COL_DATE, _DateDelegate(lambda: date(self._default_year, 1, 1), t),
        )
        t.setItemDelegateForColumn(_COL_REF, _RefFloatDelegate(t))
        t.setItemDelegateForColumn(_COL_TZS, _TZSDelegate(t))
        t.setItemDelegateForColumn(_COL_USD, _TZSDelegate(t))
        t.setItemDelegateForColumn(_COL_RCPT, _ReceiptDelegate(t))
        people = _ItemDelegate(lambda: list(self._people_names), t)
        t.setItemDelegateForColumn(_COL_OWN, people)
        t.setItemDelegateForColumn(_COL_APP, people)

    def set_lookups(
        self,
        *,
        item_names: Optional[List[str]] = None,
        fleet_numbers: Optional[Set[str] | List[str]] = None,
        fleet_kinds: Optional[Dict[str, str]] = None,
        allowed_truck_labels: Optional[Set[str]] = None,
        people_names: Optional[List[str]] = None,
        default_year: Optional[int] = None,
        can_add_fleet: Optional[bool] = None,
    ) -> None:
        if item_names is not None:
            self._item_names = list(item_names)
        if fleet_numbers is not None:
            self._fleet_numbers = set(fleet_numbers)
        if fleet_kinds is not None:
            self._fleet_kinds = dict(fleet_kinds)
        if allowed_truck_labels is not None:
            self._allowed_truck_labels = set(allowed_truck_labels)
        if people_names is not None:
            self._people_names = list(people_names)
        if default_year is not None:
            self._default_year = int(default_year)
        if can_add_fleet is not None:
            self._can_add_fleet = bool(can_add_fleet)

    def table(self) -> QTableWidget:
        return self._tbl

    def clear_rows(self) -> None:
        self._exit_edit_mode(discard=True, silent=True)
        self._tbl.setRowCount(0)
        self._txs = []

    def tx_at(self, row: int) -> Optional[Transaction]:
        if 0 <= row < len(self._txs):
            return self._txs[row]
        return None

    def dirty_rows(self) -> List[int]:
        return sorted(self._dirty_rows)

    def is_edit_mode(self) -> bool:
        return self._edit_mode

    def has_dirty(self) -> bool:
        return bool(self._dirty_rows)

    def scroll_frozen(self) -> bool:
        """True while edit mode has unsaved dirty rows (pause infinite scroll)."""
        return self._edit_mode and bool(self._dirty_rows)

    @staticmethod
    def _flag(align: str) -> Qt.AlignmentFlag:
        return {
            "left": Qt.AlignLeft,
            "right": Qt.AlignRight,
            "center": Qt.AlignHCenter,
        }[align] | Qt.AlignVCenter

    def _fill_row(
        self, r: int, tx: Transaction, serial: int,
        cashier_names: Dict, row_idx: int,
    ) -> None:
        t = self._tbl
        row_bg = _stripe_bg(row_idx)
        cashier_name = (
            _short_name(cashier_names.get(tx.cashier_id, ""))
            if tx.cashier_id else "—"
        )
        item_str = tx.item or tx.category_name or ""
        tzs_txt, usd_txt = _amount_cells(tx)
        rcpt_key = _normalize_receipt(tx.receipt_status)

        sno = _cell(str(serial), self._flag("center"))
        if tx._id is not None:
            sno.setData(_TX_ID_ROLE, str(tx._id))
        t.setItem(r, 0, sno)

        date_txt = format_register_date(tx.date) if tx.date else ""
        t.setItem(r, 1, _cell(date_txt or "—"))
        t.setItem(r, 2, _cell(item_str or "—"))
        t.setItem(r, 3, _cell(tx.description or "—"))
        t.setItem(r, 4, _cell(tx.truck_number or "—"))
        t.setItem(r, 5, _cell(tx.memo or "—"))

        ref_text = _ref_float_display(tx)
        t.setItem(
            r, _COL_REF,
            _cell(ref_text or "—", color=_T1 if ref_text else _TM),
        )
        t.setItem(
            r, _COL_TZS,
            _cell(
                tzs_txt or "—",
                self._flag("right"),
                color=_T1 if tzs_txt else _TM,
            ),
        )
        t.setItem(
            r, _COL_USD,
            _cell(
                usd_txt or "—",
                self._flag("right"),
                color=_T1 if usd_txt else _TM,
            ),
        )
        t.setItem(r, _COL_RCPT, _cell(rcpt_key, self._flag("center")))
        t.setItem(r, _COL_OWN, _cell(tx.ownership or "—"))
        t.setItem(r, _COL_APP, _cell(tx.approver or "—", color=_T2))
        t.setItem(r, _COL_CASH, _cell(cashier_name))
        _finish_table_row(t, r, row_bg)
        for c in range(len(_COLS)):
            it = t.item(r, c)
            if it is not None:
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

    def populate(self, txs: List[Transaction], skip: int,
                 cashier_names: Dict = None) -> None:
        if cashier_names is None:
            cashier_names = {}
        self.clear_rows()
        self.append_rows(txs, skip, cashier_names)

    def append_rows(
        self, txs: List[Transaction], skip: int,
        cashier_names: Dict = None,
    ) -> None:
        if cashier_names is None:
            cashier_names = {}
        if self.scroll_frozen():
            return
        t = self._tbl
        start = t.rowCount()
        t.blockSignals(True)
        t.setRowCount(start + len(txs))
        if len(self._txs) < start:
            self._txs.extend([None] * (start - len(self._txs)))
        for i, tx in enumerate(txs):
            r = start + i
            if r < len(self._txs):
                self._txs[r] = tx
            else:
                self._txs.append(tx)
            self._fill_row(r, tx, skip + i + 1, cashier_names, r)
            if self._edit_mode:
                self._unlock_row(r, paint_edit=True)
        t.blockSignals(False)

    def row_count(self) -> int:
        return self._tbl.rowCount()

    def clear_column_filters(self) -> None:
        self._col_filters.clear()
        self._col_value_cache.clear()
        hdr = self._tbl.horizontalHeader()
        if isinstance(hdr, ExcelFilterHeaderView):
            hdr.clear_filters()
        self._show_all_rows()

    def reset_default_sort(self) -> None:
        """Restore newest-first date sort (Clear filters)."""
        self._sort_field = "date"
        self._sort_asc = False
        self._update_sort_indicator()

    def column_filters_for_query(self) -> Dict[str, List[str]]:
        """Map active Excel ▾ filters to accountant_service ``column_filters``."""
        out: Dict[str, List[str]] = {}
        for col, accepted in self._col_filters.items():
            field = _COL_FIELD.get(col)
            if not field or not accepted:
                continue
            vals = sorted(v for v in accepted if v and v != "—")
            if vals:
                out[field] = vals
        return out

    def set_column_value_cache(self, cache: Dict[int, Set[str]]) -> None:
        """Replace distinct filter-menu values (full selected range from DB)."""
        self._col_value_cache = {
            int(c): set(vals) for c, vals in (cache or {}).items()
        }

    def _cell_text(self, row: int, col: int) -> str:
        it = self._tbl.item(row, col)
        raw = (it.text() if it else "").strip()
        if col == _COL_RCPT and raw:
            label, _fg = _RECEIPT_MAP.get(_normalize_receipt(raw), (raw, _T1))
            return label
        return raw

    def _filter_source_rows(self) -> List[dict]:
        """Fallback row snapshot when the DB value cache is not ready yet."""
        rows: List[dict] = []
        for r in range(self._tbl.rowCount()):
            row: dict = {}
            for c in _FILTERABLE_COLS:
                txt = self._cell_text(r, c)
                if txt and txt != "—":
                    row[c] = txt
            if row:
                rows.append(row)
        return rows

    def _filter_menu_values(self, col: int) -> set:
        # Prefer full-range distincts from DB (already cascaded by the service).
        if col in self._col_value_cache:
            return set(self._col_value_cache.get(col) or set())
        return cascade_column_values(
            self._filter_source_rows(),
            target_col=col,
            active_filters=self._col_filters,
        )

    def _on_col_filter_changed(self, col: int, accepted) -> None:
        accepted = set(accepted or [])
        if accepted:
            self._col_filters[col] = accepted
        else:
            self._col_filters.pop(col, None)
        hdr = self._tbl.horizontalHeader()
        if isinstance(hdr, ExcelFilterHeaderView):
            hdr.sync_active(self._col_filters)
        # Parent reloads from DB for the whole year/month range.
        self.col_filter_changed.emit()

    def _show_all_rows(self) -> None:
        t = self._tbl
        t.setUpdatesEnabled(False)
        try:
            for r in range(t.rowCount()):
                t.setRowHidden(r, False)
        finally:
            t.setUpdatesEnabled(True)

    def visible_row_count(self) -> int:
        return sum(
            1 for r in range(self._tbl.rowCount())
            if not self._tbl.isRowHidden(r)
        )

    def _on_header_click(self, col: int) -> None:
        sort_field = _COLS[col][2] if 0 <= col < len(_COLS) else None
        if sort_field is None:
            return
        if self._sort_field == sort_field:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_field = sort_field
            self._sort_asc = False
        self._update_sort_indicator()
        self.sort_changed.emit(self._sort_field, self._sort_asc)

    def _on_excel_sort(self, col: int, mode: str) -> None:
        sort_field = _COLS[col][2] if 0 <= col < len(_COLS) else None
        if sort_field is None:
            return
        self._sort_field = sort_field
        self._sort_asc = mode == SORT_ASC
        self._update_sort_indicator()
        self.sort_changed.emit(self._sort_field, self._sort_asc)

    def _update_sort_indicator(self) -> None:
        order = Qt.AscendingOrder if self._sort_asc else Qt.DescendingOrder
        for i, (_, _a, f) in enumerate(_COLS):
            if f == self._sort_field:
                self._tbl.horizontalHeader().setSortIndicator(i, order)
                return

    def enter_edit_mode(self) -> None:
        self._edit_mode = True
        self._dirty_rows.clear()
        self._undo_stack.clear()
        self._cut_cells.clear()
        self._tbl.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self._tbl.blockSignals(True)
        for row in range(self._tbl.rowCount()):
            self._unlock_row(row, paint_edit=True)
        self._tbl.blockSignals(False)
        self.edit_state_changed.emit(True, 0)

    def exit_edit_mode(self, *, discard: bool = True) -> None:
        self._exit_edit_mode(discard=discard, silent=False)

    def _exit_edit_mode(self, *, discard: bool, silent: bool = False) -> None:
        self._commit_open_editor()
        self._edit_mode = False
        self._dirty_rows.clear()
        self._undo_stack.clear()
        self._cut_cells.clear()
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        if not silent:
            self.edit_state_changed.emit(False, 0)
        self._tbl.blockSignals(True)
        for row in range(self._tbl.rowCount()):
            for col in range(self._tbl.columnCount()):
                it = self._tbl.item(row, col)
                if it is None:
                    continue
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if discard:
                    bg = _stripe_bg(row)
                    color = QColor(bg) if isinstance(bg, str) else bg
                    it.setBackground(QBrush(color))
        self._tbl.blockSignals(False)

    def _unlock_row(self, row: int, *, paint_edit: bool) -> None:
        editable = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
        ro = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        for col in range(self._tbl.columnCount()):
            it = self._tbl.item(row, col)
            if it is None:
                continue
            if col in _READONLY_COLS:
                it.setFlags(ro)
            else:
                it.setFlags(editable)
            if paint_edit and row not in self._dirty_rows:
                it.setBackground(QBrush(EDIT_BG))

    def _mark_dirty(self, row: int) -> None:
        if not self._edit_mode or row < 0:
            return
        if row in self._dirty_rows:
            return
        self._dirty_rows.add(row)
        self._tbl.blockSignals(True)
        for col in range(self._tbl.columnCount()):
            it = self._tbl.item(row, col)
            if it is not None:
                it.setBackground(QBrush(DIRTY_BG))
        self._tbl.blockSignals(False)
        self.edit_state_changed.emit(True, len(self._dirty_rows))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._bulk_mutating or item is None or not self._edit_mode:
            return
        row, col = item.row(), item.column()
        if col in _READONLY_COLS:
            return
        if col not in _UPPER_SKIP and col not in (_COL_TZS, _COL_USD):
            text = item.text()
            uppered = _master_upper(col, text)
            if uppered != text:
                self._tbl.blockSignals(True)
                item.setText(uppered)
                self._tbl.blockSignals(False)
        if col in (_COL_TZS, _COL_USD):
            raw = _plain(item.text())
            if raw:
                amt = _parse_amount_text(raw)
                decimals = 2 if col == _COL_USD else 0
                formatted = f"{amt:,.{decimals}f}"
                self._tbl.blockSignals(True)
                item.setText(formatted)
                item.setForeground(QColor(_T1))
                self._tbl.blockSignals(False)
        if col == _COL_TRUCK:
            self._validate_truck_cell(row, item)
        self._mark_dirty(row)

    def _commit_open_editor(self) -> None:
        w = self._tbl.indexWidget(self._tbl.currentIndex())
        if w is not None:
            self._tbl.commitData(w)
            self._tbl.closeEditor(w, QAbstractItemDelegate.NoHint)

    def _table_key_press(self, event: QKeyEvent) -> None:
        mod = event.modifiers()
        key = event.key()

        if mod == Qt.ControlModifier:
            if key == Qt.Key_C:
                self._copy()
                return
            if key == Qt.Key_X:
                self._cut()
                return
            if key == Qt.Key_V:
                self._paste()
                return
            if key == Qt.Key_Z:
                self._undo()
                return
            if key == Qt.Key_A:
                self._tbl.selectAll()
                return
            if key == Qt.Key_D:
                self._fill_down()
                return
            if key == Qt.Key_R:
                self._fill_right()
                return
            if key == Qt.Key_S and self._edit_mode:
                self.save_requested.emit()
                return

        if key == Qt.Key_Escape:
            self._cut_cells.clear()
            self._tbl.viewport().update()
            return

        if key == Qt.Key_F2:
            it = self._tbl.currentItem()
            if it and self._edit_mode and it.column() in _EDITABLE_COLS:
                self._tbl.editItem(it)
            return

        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self._clear_selected()
            return

        if key == Qt.Key_Tab:
            self._tab_forward()
            return

        if key == Qt.Key_Backtab:
            self._step(0, -1, skip=_READONLY_COLS)
            return

        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._step(+1, 0)
            return

        QTableWidget.keyPressEvent(self._tbl, event)

    def _copy(self) -> None:
        items = self._tbl.selectedItems()
        if not items:
            return
        rows = sorted({it.row() for it in items})
        cols = sorted({it.column() for it in items})
        cell_map = {(it.row(), it.column()): it for it in items}
        lines = []
        for row in rows:
            row_cells = []
            for col in cols:
                it = cell_map.get((row, col))
                row_cells.append(it.text() if it is not None else "")
            lines.append("\t".join(row_cells))
        QApplication.clipboard().setText("\n".join(lines))
        # Dashed outline so you can see which cells are on the clipboard.
        self._cut_cells = {(it.row(), it.column()) for it in items}
        self._tbl.viewport().update()

    def _cut(self) -> None:
        """Cut: clipboard + dotted marquee, then clear the cells."""
        if not self._edit_mode:
            self._copy()
            return
        items = self._tbl.selectedItems()
        if not items:
            return
        self._push_undo_cells(self._snapshot_selection())
        self._copy()  # also paints the dotted marquee
        self._clear_selected()
        self._tbl.viewport().update()

    def _clear_selected(self) -> None:
        if not self._edit_mode:
            return
        self._tbl.blockSignals(True)
        for item in self._tbl.selectedItems():
            row, col = item.row(), item.column()
            if col in _READONLY_COLS:
                continue
            item.setText("")
            self._dirty_rows.add(row)
            if col == _COL_TRUCK:
                self._pending_truck_issues.pop(row, None)
        self._tbl.blockSignals(False)
        for row in list(self._dirty_rows):
            self._mark_dirty(row)

    def _paste(self) -> None:
        if not self._edit_mode:
            return
        text = QApplication.clipboard().text()
        if not text:
            return
        lines = text.splitlines()
        sel = self._tbl.selectedIndexes()
        if not sel:
            return
        self._push_undo_cells(self._snapshot_selection())
        start_row = min(i.row() for i in sel)
        start_col = min(i.column() for i in sel)
        sel_rows = sorted({i.row() for i in sel})
        sel_cols = sorted({i.column() for i in sel})
        truck_cells: List[Tuple[int, str]] = []

        self._bulk_mutating = True
        self._tbl.blockSignals(True)
        try:
            if len(lines) == 1 and "\t" not in lines[0] and (
                len(sel_rows) > 1 or len(sel_cols) > 1
            ):
                cell_value = lines[0]
                for row in sel_rows:
                    for col in sel_cols:
                        self._set_pasted_cell(row, col, cell_value, truck_cells)
                return

            for r, line in enumerate(lines):
                for c, cell in enumerate(line.split("\t")):
                    row = start_row + r
                    col = start_col + c
                    if row >= self._tbl.rowCount() or col >= len(_COLS):
                        continue
                    self._set_pasted_cell(row, col, cell, truck_cells)
        finally:
            self._tbl.blockSignals(False)
            self._bulk_mutating = False
            self._cut_cells.clear()
            touched = set(sel_rows)
            for r, _line in enumerate(lines):
                touched.add(start_row + r)
            for row in touched:
                if 0 <= row < self._tbl.rowCount():
                    self._mark_dirty(row)
            self._finalize_truck_cells(truck_cells)

    def _set_pasted_cell(
        self,
        row: int,
        col: int,
        raw: str,
        truck_cells: Optional[List[Tuple[int, str]]] = None,
    ) -> None:
        if col in _READONLY_COLS or col not in _EDITABLE_COLS:
            return
        value = (raw or "").strip()
        if value == "—":
            value = ""
        if col == _COL_RCPT:
            value = _receipt_paste_value(value)
        elif col in (_COL_TZS, _COL_USD):
            if value:
                amt = _parse_amount_text(value)
                decimals = 2 if col == _COL_USD else 0
                value = f"{amt:,.{decimals}f}"
            else:
                value = ""
        elif col == _COL_TRUCK:
            if truck_cells is not None and value:
                truck_cells.append((row, value))
            value = value.upper() if value else ""
        else:
            value = _master_upper(col, value)

        it = self._tbl.item(row, col) or QTableWidgetItem()
        it.setText(value)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        if col in (_COL_TZS, _COL_USD):
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            it.setForeground(QColor(_T1))
        self._tbl.setItem(row, col, it)

    def _fill_down(self) -> None:
        if not self._edit_mode:
            return
        items = self._tbl.selectedItems()
        if not items:
            return
        rows = sorted({it.row() for it in items})
        cols = sorted({it.column() for it in items})
        if len(rows) < 2:
            return
        self._push_undo_cells(self._snapshot_selection())
        source_row = rows[0]
        cell_map = {(it.row(), it.column()): it for it in items}
        truck_cells: List[Tuple[int, str]] = []
        self._bulk_mutating = True
        self._tbl.blockSignals(True)
        try:
            for col in cols:
                if col in _READONLY_COLS:
                    continue
                src = cell_map.get((source_row, col))
                if src is None:
                    continue
                for row in rows[1:]:
                    self._set_pasted_cell(row, col, src.text(), truck_cells)
                    self._dirty_rows.add(row)
        finally:
            self._tbl.blockSignals(False)
            self._bulk_mutating = False
            for row in rows[1:]:
                self._mark_dirty(row)
            self._finalize_truck_cells(truck_cells)

    def _fill_right(self) -> None:
        if not self._edit_mode:
            return
        items = self._tbl.selectedItems()
        if not items:
            return
        rows = sorted({it.row() for it in items})
        cols = sorted({it.column() for it in items})
        if len(cols) < 2:
            return
        self._push_undo_cells(self._snapshot_selection())
        source_col = cols[0]
        cell_map = {(it.row(), it.column()): it for it in items}
        truck_cells: List[Tuple[int, str]] = []
        self._bulk_mutating = True
        self._tbl.blockSignals(True)
        try:
            for row in rows:
                src = cell_map.get((row, source_col))
                if src is None:
                    continue
                for col in cols[1:]:
                    if col in _READONLY_COLS:
                        continue
                    self._set_pasted_cell(row, col, src.text(), truck_cells)
                self._dirty_rows.add(row)
        finally:
            self._tbl.blockSignals(False)
            self._bulk_mutating = False
            for row in rows:
                self._mark_dirty(row)
            self._finalize_truck_cells(truck_cells)

    def _snapshot_selection(self) -> List[Tuple[int, int, str]]:
        snap: List[Tuple[int, int, str]] = []
        for it in self._tbl.selectedItems():
            if it.column() in _READONLY_COLS:
                continue
            snap.append((it.row(), it.column(), it.text()))
        return snap

    def _push_undo_cells(self, snap: List[Tuple[int, int, str]]) -> None:
        if not snap:
            return
        self._undo_stack.append(snap)
        if len(self._undo_stack) > _UNDO_LIMIT:
            self._undo_stack.pop(0)

    def _undo(self) -> None:
        if not self._undo_stack or not self._edit_mode:
            return
        snap = self._undo_stack.pop()
        self._bulk_mutating = True
        self._tbl.blockSignals(True)
        touched: Set[int] = set()
        try:
            for row, col, text in snap:
                it = self._tbl.item(row, col)
                if it is None:
                    continue
                it.setText(text)
                touched.add(row)
        finally:
            self._tbl.blockSignals(False)
            self._bulk_mutating = False
            for row in touched:
                self._mark_dirty(row)

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self._tbl)
        menu.addAction("Copy", self._copy)
        if self._edit_mode:
            menu.addAction("Cut", self._cut)
            menu.addAction("Paste", self._paste)
            menu.addSeparator()
            menu.addAction("Fill Down", self._fill_down)
            menu.addAction("Fill Right", self._fill_right)
            if self._undo_stack:
                menu.addAction("Undo", self._undo)
            menu.addSeparator()
            menu.addAction("Bulk Set Item…", self.bulk_set_item_requested.emit)
            if self._dirty_rows:
                menu.addAction("Save Changes", self.save_requested.emit)
        else:
            menu.addSeparator()
            act = QAction("Edit Mode", menu)
            act.triggered.connect(self.enter_edit_mode)
            menu.addAction(act)
            menu.addAction("Bulk Set Item…", self.bulk_set_item_requested.emit)
        menu.exec(self._tbl.viewport().mapToGlobal(pos))

    def _tab_forward(self) -> None:
        row, col = self._tbl.currentRow(), self._tbl.currentColumn()
        skip = _READONLY_COLS
        next_col = col + 1
        while next_col < self._tbl.columnCount() and next_col in skip:
            next_col += 1
        if next_col >= self._tbl.columnCount():
            next_row = min(row + 1, self._tbl.rowCount() - 1)
            first_col = 0
            while first_col < self._tbl.columnCount() and first_col in skip:
                first_col += 1
            self._tbl.setCurrentCell(next_row, first_col)
        else:
            self._tbl.setCurrentCell(row, next_col)
        self._tbl.setFocus()
        idx = self._tbl.currentIndex()
        if self._edit_mode and idx.column() in _EDITABLE_COLS:
            self._tbl.edit(idx)

    def _step(self, dr: int, dc: int, skip: set = None) -> None:
        row, col = self._tbl.currentRow(), self._tbl.currentColumn()
        new_col, new_row = col + dc, row + dr
        skip = skip or _READONLY_COLS
        if skip and dc != 0:
            while 0 <= new_col < self._tbl.columnCount():
                if new_col not in skip:
                    break
                new_col += dc
        new_row = max(0, min(new_row, self._tbl.rowCount() - 1))
        new_col = max(0, min(new_col, self._tbl.columnCount() - 1))
        self._tbl.setCurrentCell(new_row, new_col)

    # ── Truck registry validation (cashier register parity) ────────────────

    def _set_truck_cell(self, row: int, value: str) -> None:
        it = self._tbl.item(row, _COL_TRUCK)
        prev = self._tbl.blockSignals(True)
        if it is None:
            it = QTableWidgetItem(value)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._tbl.setItem(row, _COL_TRUCK, it)
        else:
            it.setText(value)
        self._tbl.blockSignals(prev)
        self._mark_dirty(row)

    def _resolve_truck_text(self, raw: str) -> tuple[str, Optional[str]]:
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
        if not cells:
            return
        for row, raw in cells:
            status, value = self._resolve_truck_text(raw)
            if status == "empty":
                self._set_truck_cell(row, "")
                self._pending_truck_issues.pop(row, None)
            elif status == "ok":
                self._set_truck_cell(row, value or "")
                self._pending_truck_issues.pop(row, None)
            elif status == "invalid_format":
                self._set_truck_cell(row, value or "")
                self._pending_truck_issues[row] = TruckIssue(
                    row=row, original=raw, kind="invalid_format",
                )
            else:
                self._set_truck_cell(row, value or "")
                self._pending_truck_issues[row] = TruckIssue(
                    row=row, original=raw, kind="not_in_registry",
                )
        self._schedule_truck_correction()

    def _validate_truck_cell(self, row: int, item: QTableWidgetItem) -> None:
        raw = item.text().strip()
        if not raw:
            self._pending_truck_issues.pop(row, None)
            return
        status, value = self._resolve_truck_text(raw)
        if status == "ok":
            if item.text() != value:
                prev = self._tbl.blockSignals(True)
                item.setText(value or "")
                self._tbl.blockSignals(prev)
            self._pending_truck_issues.pop(row, None)
            return
        if status == "empty":
            return
        kind = "invalid_format" if status == "invalid_format" else "not_in_registry"
        if item.text() != value:
            prev = self._tbl.blockSignals(True)
            item.setText(value or "")
            self._tbl.blockSignals(prev)
        self._pending_truck_issues[row] = TruckIssue(
            row=row, original=value or raw, kind=kind,
        )
        self._schedule_truck_correction()

    def _schedule_truck_correction(self) -> None:
        if not self._pending_truck_issues:
            return
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
        live: List[TruckIssue] = []
        for issue in issues:
            it = self._tbl.item(issue.row, _COL_TRUCK)
            current = (it.text().strip() if it else "")
            if not current:
                continue
            status, value = self._resolve_truck_text(current)
            if status == "ok":
                self._set_truck_cell(issue.row, value or "")
                continue
            issue.kind = (
                "invalid_format" if status == "invalid_format" else "not_in_registry"
            )
            issue.original = current
            live.append(issue)
        if not live:
            return

        dlg = self._open_truck_dialog
        if dlg is not None and getattr(dlg, "isVisible", lambda: False)():
            dlg.add_issues(live)
            return

        dlg = TruckCorrectionDialog(
            live,
            self._fleet_numbers,
            can_add=self._can_add_fleet,
            allowed_labels=self._allowed_truck_labels,
            on_resolved=self._on_truck_issue_resolved_live,
            fleet_kinds=self._fleet_kinds or {},
            parent=self,
        )
        self._open_truck_dialog = dlg
        result = dlg.exec()
        self._open_truck_dialog = None

        pending_adds = list(getattr(dlg, "pending_registry_adds", None) or [])
        if pending_adds:
            import asyncio
            asyncio.ensure_future(self._persist_truck_registry_adds(pending_adds))
        if getattr(dlg, "new_labels", None):
            import asyncio
            asyncio.ensure_future(self._remember_truck_labels(dlg.new_labels))
        if result != QDialog.Accepted:
            resolved_rows = {i.row for i in dlg.issues}
            for issue in live:
                if issue.row not in resolved_rows:
                    self._set_truck_cell(issue.row, "")

    def _on_truck_issue_resolved_live(self, issue: TruckIssue) -> None:
        if issue.skip or not issue.corrected:
            self._set_truck_cell(issue.row, "")
            return
        if getattr(issue, "is_place_label", False):
            self._allowed_truck_labels.add(normalize_place_label(issue.corrected))
        else:
            self._fleet_numbers.add(issue.corrected)
        self._set_truck_cell(issue.row, issue.corrected)

    async def _persist_truck_registry_adds(self, adds: list) -> None:
        from tahmeed.services.truck_service import add_fleet_by_collection

        for kind, number in adds:
            try:
                label = await add_fleet_by_collection(kind, number)
                self._fleet_kinds[number] = label
                self._fleet_numbers.add(number)
            except Exception as exc:
                QMessageBox.critical(
                    self, "Error", f"Failed to add {number} to registry:\n{exc}",
                )

    async def _remember_truck_labels(self, labels: list) -> None:
        if not labels:
            return
        try:
            from tahmeed.services.settings_service import set_setting
            merged = merge_allowed_labels(
                self._allowed_truck_labels, labels, DEFAULT_PLACE_LABELS,
            )
            self._allowed_truck_labels = merged
            await set_setting("allowed_truck_labels", sorted(merged))
        except Exception:
            pass

    def _txt(self, row: int, col: int) -> str:
        it = self._tbl.item(row, col)
        return _plain(it.text() if it else "")

    def updates_from_row(self, row: int) -> Optional[dict]:
        """Build Mongo $set fields from current cell values for one dirty row."""
        tx = self.tx_at(row)
        if tx is None or tx._id is None:
            return None

        date_str = self._txt(row, _COL_DATE)
        default_year = tx.date.year if tx.date else self._default_year
        tx_date = _parse_optional_date(date_str, default_year=default_year)
        if tx_date is None:
            tx_date = tx.date

        tzs = self._txt(row, _COL_TZS)
        usd = self._txt(row, _COL_USD)
        amount, amount_usd, currency = pack_money(
            _parse_optional_amount_text(tzs) if tzs else None,
            _parse_optional_amount_text(usd) if usd else None,
        )
        if not tzs and not usd:
            amount = float(tx.amount or 0)
            currency = _currency_key(tx)
            if _is_tzs(currency):
                currency = "TZS"
            amount_usd = getattr(tx, "amount_usd", None)

        item_name = self._txt(row, _COL_ITEM)
        rcpt = _norm_receipt_text(self._txt(row, _COL_RCPT))
        if rcpt not in _VALID_RCPT:
            rcpt = _normalize_receipt(tx.receipt_status)

        ref = self._txt(row, _COL_REF).upper()
        updates = {
            "description": self._txt(row, _COL_DESC),
            "item": item_name,
            "category_name": item_name,
            "truck_number": self._txt(row, _COL_TRUCK),
            "amount": amount,
            "currency": currency,
            "amount_usd": amount_usd,
            "memo": self._txt(row, _COL_MEMO),
            "receipt_status": rcpt,
            "ref_float": ref,
            "ownership": self._txt(row, _COL_OWN),
            "approver": self._txt(row, _COL_APP),
        }
        if tx_date is not None:
            updates["date"] = tx_date
        return updates

    def selected_tx_ids_for_item_bulk(self) -> List:
        from bson import ObjectId

        rows = sorted({idx.row() for idx in self._tbl.selectedIndexes()})
        ids = []
        for r in rows:
            tx = self.tx_at(r)
            if tx is not None and tx._id is not None:
                ids.append(ObjectId(str(tx._id)))
        return ids
