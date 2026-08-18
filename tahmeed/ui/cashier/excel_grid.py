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
    QStyle, QComboBox, QDialog, QFrame, QListWidget, QListWidgetItem, QPushButton,
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
    get_transactions_by_date, save_transaction, request_or_delete_transaction,
    update_transaction, insert_pending_edit,
    check_for_duplicates, submit_day_for_verify, recount_day_order,
    next_day_order,
)
from tahmeed.services.category_service import get_all_categories, item_key
from tahmeed.services.subtable_service import get_subtables
from tahmeed.services.settings_service import get_setting, set_setting
from tahmeed.services.register_draft_service import (
    build_draft_payload,
    cells_for_json,
    cells_from_json,
    clear_register_draft,
    draft_is_empty,
    hydrate_pending_meta,
    load_register_draft,
    save_register_draft,
    serialize_pending_meta,
)
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit
from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit, accept_completion
from tahmeed.ui.dialogs.truck_correction_dialog import TruckCorrectionDialog, TruckIssue

# ---------------------------------------------------------------------------
# Column indices / colors / delegates (shared with RejectedView)
# ---------------------------------------------------------------------------
from tahmeed.ui.cashier.register_delegates import (  # noqa: E402
    COL_SNO, COL_DATE, COL_ITEM, COL_DESC, COL_TRUCK, COL_MEMO,
    COL_REF, COL_TZS, COL_RECEIPT, COL_OWN, COL_APR, COL_PAYEE, COL_CHEQUE,
    COL_CASHIER,
    HEADERS, CHECK_COLS, READONLY_COLS, _DATA_SKIP_COLS, _UPPER_SKIP_COLS,
    DEFAULT_EDITABLE_ROWS, _REF_FLOAT_OPTS, _COL_PREFERRED, _COL_FLEX, _COL_MIN,
    _is_refund_float, _ref_float_text, _parse_optional_date, format_register_date,
    SAVED_BG, NEW_BG, EMPTY_BG, NEG_COLOR, EDIT_BG, DIRTY_BG, DUP_BG, MISMATCH_BG,
    _FOOTER_BTN_STYLE,
    _accept_editor_completion, _upper_text,
    _ExcelCellDelegate, _DescriptionDelegate, _TruckDelegate, _DateDelegate,
    _RefFloatDelegate, _norm_receipt_text, _receipt_paste_value, _parse_amount_text, _ReceiptDelegate,
    _ItemDelegate, _CurrencyLineEdit, _TZSDelegate,
    _RCPT_COLORS, _RCPT_LABEL, _RECEIPT_OPTS, _RCPT_OPT_KEY, _RCPT_NORM, _VALID_RCPT,
)

_ROWS_CLIP_PREFIX = "TAHMEED_ROWS_V1\n"



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


def cascade_column_values(
    rows: List[dict],
    *,
    target_col: int,
    active_filters: dict,
) -> set:
    """Distinct values for *target_col* from rows that pass every *other* filter.

    Re-exported from the shared Excel filter widget for Daily Register.
    """
    from tahmeed.ui.widgets.excel_column_filter import (
        cascade_column_values as _cascade,
    )

    return _cascade(rows, target_col=target_col, active_filters=active_filters)


class _ColumnFilterPopup(QFrame):
    """Excel-style checklist popup: values from the table only, with search + Apply."""

    applied = Signal(object)  # set[str] | empty set = Show All

    def __init__(self, values: set, current: set, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("colFilterPopup")
        self.setStyleSheet(
            "QFrame#colFilterPopup{"
            " background:#ffffff;border:1px solid #D1D5DB;border-radius:6px;}"
        )
        self._all_values = sorted(values, key=lambda v: v.lower())
        self._current = set(current or [])

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        hint = QLabel(f"{len(self._all_values)} value(s) in this table")
        hint.setStyleSheet(
            "font-size:10px;color:#6B7280;background:transparent;border:none;"
        )
        root.addWidget(hint)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            "QLineEdit{border:1px solid #D1D5DB;border-radius:4px;"
            "padding:4px 8px;font-size:12px;}"
        )
        self._search.textChanged.connect(self._refilter)
        root.addWidget(self._search)

        self._list = QListWidget()
        self._list.setMinimumWidth(220)
        self._list.setMaximumHeight(260)
        self._list.setStyleSheet(
            "QListWidget{border:1px solid #E5E7EB;border-radius:4px;font-size:12px;}"
            "QListWidget::item{padding:3px 6px;}"
        )
        root.addWidget(self._list, 1)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        show_all = QPushButton("Show All")
        show_all.setCursor(Qt.PointingHandCursor)
        show_all.setEnabled(bool(self._current))
        show_all.clicked.connect(self._on_show_all)
        apply_btn = QPushButton("Apply")
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setStyleSheet(
            "QPushButton{background:#0077C5;color:#fff;border:none;"
            "border-radius:4px;padding:5px 12px;font-weight:600;}"
        )
        apply_btn.clicked.connect(self._on_apply)
        btns.addWidget(show_all)
        btns.addStretch()
        btns.addWidget(apply_btn)
        root.addLayout(btns)

        self._refilter("")
        self._search.setFocus()

    def _refilter(self, text: str = "") -> None:
        needle = (text or self._search.text() or "").strip().lower()
        self._list.clear()
        for val in self._all_values:
            if needle and needle not in val.lower():
                continue
            it = QListWidgetItem(val)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(
                Qt.Checked if val in self._current else Qt.Unchecked
            )
            self._list.addItem(it)

    def _checked(self) -> set:
        # Start from prior selection, then sync visible rows' check states
        # (so search doesn't wipe hidden checked values).
        result = set(self._current)
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.checkState() == Qt.Checked:
                result.add(it.text())
            else:
                result.discard(it.text())
        return result

    def _on_show_all(self) -> None:
        self.applied.emit(set())
        self.close()

    def _on_apply(self) -> None:
        self.applied.emit(self._checked())
        self.close()


class _FilterHeaderView(QHeaderView):
    """Horizontal header ▾ filters — options only from table rows, with chaining."""

    filter_changed = Signal(int, set)   # (col_index, accepted_values); empty = cleared

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._active: dict = {}   # col -> set of accepted values
        self._value_provider = None  # optional callable(col) -> set[str]
        self._popup = None

    def set_value_provider(self, provider) -> None:
        self._value_provider = provider

    def clear_filters(self) -> None:
        self._active.clear()
        self.viewport().update()

    def sync_active(self, filters: dict) -> None:
        """Mirror DailyRegister._col_filters onto the chevron paint state."""
        self._active = {c: set(v) for c, v in (filters or {}).items() if v}
        self.viewport().update()

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
        if not callable(self._value_provider):
            return
        values = set(self._value_provider(col) or [])
        current = set(self._active.get(col, set()) or [])
        # Keep currently selected values visible so they can be unchecked.
        values |= current
        if not values and not current:
            return

        if self._popup is not None:
            self._popup.close()
            self._popup = None

        popup = _ColumnFilterPopup(values, current, parent=self)
        self._popup = popup

        def _on_applied(new_filter):
            new_filter = set(new_filter or [])
            if new_filter:
                self._active[col] = new_filter
            else:
                self._active.pop(col, None)
            self.filter_changed.emit(col, new_filter)
            self.viewport().update()

        popup.applied.connect(_on_applied)
        # Position under the chevron
        popup.adjustSize()
        popup.move(global_pos)
        popup.show()


# ---------------------------------------------------------------------------
# DailyRegister
# ---------------------------------------------------------------------------

class DailyRegister(QWidget):
    """Unified daily expense register (replaces ExcelGrid + TransactionsTable)."""

    rows_saved        = Signal(int)
    stats_updated     = Signal(int, float, float, object)  # n, total_tzs, refund, register_date
    edit_state_changed = Signal(bool, int)         # (edit_mode_active, dirty_row_count)
    mode_changed      = Signal(bool)               # merged mode on/off
    attachment_count_changed = Signal(int)         # selected row attachment count
    save_busy_changed = Signal(bool)               # True while Save/Submit is in flight

    def __init__(self, user: User, categories: List[Category], parent=None):
        super().__init__(parent)
        self._user        = user
        self._categories  = categories
        self._cat_by_name: dict = {c.name.lower(): c for c in categories}
        self._locked_subitems: dict = {}   # item name (lower) -> [sub-item names]
        self._restrict_items: bool = False
        self._defer_item_to_verify: bool = False
        self._restrict_trucks: bool = True  # always on — only registered fleet numbers
        self._fleet_numbers: set = set()   # uppercased valid fleet numbers
        self._fleet_kinds: dict = {}       # number → "truck" | "trailer" | "motor_vehicle"
        self._allowed_truck_labels: set = set(DEFAULT_PLACE_LABELS)
        self._people_names: list = []      # Ownership / APR BY suggestions (unrestricted)
        self._cashier_names: dict = {}     # ObjectId -> display name
        self._merged_mode: bool = False    # Shared/Merged day (all cashiers)
        self._current_date: date = date.today()
        self._saved_count: int   = 0
        self._saved_ids: dict    = {}   # row_index -> ObjectId
        self._saved_txs: dict    = {}   # row_index -> original Transaction (saved rows)
        self._edit_mode: bool    = False
        self._dirty_rows: set    = set()  # saved row indices modified while editing
        self._col_filters: dict   = {}   # col -> set of accepted values
        self._search_text: str    = ""
        self._pending_highlight: str = ""  # set by navigate_to_date; consumed in _populate
        self._load_upload_id: str = ""     # one-shot: load this Excel batch instead of a day
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
        # Excel cut marquee (cells stay until paste / Insert Cut Cells / Esc)
        self._cut_cells: set = set()          # {(row, col), ...}
        self._cut_payload: dict = {}          # serialized cut buffer
        self._cut_is_rows: bool = False
        # Undo stack of cell snapshots
        self._undo_stack: list = []           # [{(r,c): text}, ...]
        self._undo_limit: int = 40
        # Re-entrancy guards — prevent double-click duplicate inserts.
        self._save_in_flight = False
        self._submit_in_flight = False
        # Local draft autosave (crash / power-loss recovery).
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(1_500)
        self._draft_timer.timeout.connect(self._flush_local_draft)
        self._restoring_draft = False
        self._load_gen = 0
        self._build_ui()
        self._show_register_loading("Loading…")
        asyncio.ensure_future(self._load_date(self._current_date))
        asyncio.ensure_future(self._load_categories())
        asyncio.ensure_future(self._load_cashier_settings())
        asyncio.ensure_future(self._load_locked_subitems())
        asyncio.ensure_future(self._load_fleet_numbers())
        asyncio.ensure_future(self._load_people_names())
        asyncio.ensure_future(self._load_description_cache())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Table ──────────────────────────────────────────────────────
        self._table = _ExcelTableWidget(DEFAULT_EDITABLE_ROWS, len(HEADERS))
        self._table._grid_owner = self
        _fhv = _FilterHeaderView(self._table)
        _fhv.set_value_provider(self._filter_menu_values)
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
        self._table.setColumnHidden(COL_CASHIER, True)

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
        self._table.itemSelectionChanged.connect(self._emit_attachment_badge)

        self._table_host = QWidget(self)
        host_lay = QVBoxLayout(self._table_host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.setSpacing(0)
        host_lay.addWidget(self._table)
        root.addWidget(self._table_host, 1)
        self._loading = LoadingOverlay(self._table_host, "Loading…")

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

    def navigate_to_date(
        self, d: date, highlight_term: str = "", *, merged: bool | None = None
    ) -> None:
        """Called by dashboard when TransactionBrowser 'Go To' is used.

        highlight_term — if provided, the register scrolls to the first row
        containing this text after the date loads and briefly flashes it.
        merged=True shows every cashier's rows (Browse is a merged view).
        """
        if isinstance(d, datetime):
            d = d.date()
        if not isinstance(d, date):
            return
        self._pending_highlight = highlight_term
        self._commit_open_editor()
        if merged is True and not self._merged_mode:
            self._merged_mode = True
            self.mode_changed.emit(True)
        if self.has_unsaved_work():
            self._flush_local_draft()
            resp = QMessageBox.question(
                self, "Unsaved changes",
                "You have unsaved changes on this date.\nSave them before leaving?",
                QMessageBox.Yes | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if resp == QMessageBox.Cancel:
                self._pending_highlight = ""
                self._load_upload_id = ""
                return
            if resp == QMessageBox.Yes:
                asyncio.ensure_future(self._save_then_navigate(d))
                return
            # Discard → clear local recovery draft for this date, then leave.
            self._clear_local_draft()
        self._reset_edit_state()
        self._current_date = d
        if self._load_upload_id:
            self._show_register_loading("Loading upload…")
        else:
            self._show_register_loading(f"Loading {d.strftime('%d %b %Y')}…")
        asyncio.ensure_future(self._load_date(d))

    def navigate_to_upload(self, upload_id: str, primary_date=None) -> None:
        """Open every row of one Excel upload on the register table."""
        uid = str(upload_id or "").strip()
        if not uid:
            return
        self._load_upload_id = uid
        d = primary_date
        if isinstance(d, datetime):
            d = d.date()
        if not isinstance(d, date):
            d = self._current_date
        self.navigate_to_date(d, merged=True)

    async def _save_then_navigate(self, d: date) -> None:
        await self._do_save()
        self._current_date = d
        if self._load_upload_id:
            self._show_register_loading("Loading upload…")
        else:
            self._show_register_loading(f"Loading {d.strftime('%d %b %Y')}…")
        await self._load_date(d)

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------

    def _show_register_loading(self, message: str = "Loading…") -> None:
        overlay = getattr(self, "_loading", None)
        if overlay is not None:
            overlay.show_loading(message)

    def _hide_register_loading(self) -> None:
        overlay = getattr(self, "_loading", None)
        if overlay is not None:
            overlay.hide_loading()

    async def _load_date(self, d: date) -> None:
        from tahmeed.ui.async_utils import pause_background_polls

        with pause_background_polls(self):
            await self._load_date_body(d)

    async def _load_date_body(self, d: date) -> None:
        self._load_gen += 1
        seq = self._load_gen
        upload_id = self._load_upload_id
        if upload_id:
            self._show_register_loading("Loading upload…")
        else:
            label = d.strftime("%d %b %Y") if isinstance(d, date) else "register"
            self._show_register_loading(f"Loading {label}…")
        try:
            self._load_upload_id = ""
            if upload_id:
                from tahmeed.services.daily_import_service import get_daily_upload_records
                txs = await get_daily_upload_records(upload_id)
            elif self._merged_mode:
                txs = await get_transactions_by_date(d, merged=True)
            else:
                txs = await get_transactions_by_date(d, cashier_id=self._user._id)
            if seq != self._load_gen:
                return
            ids = [tx.cashier_id for tx in txs if tx.cashier_id]
            cashier_names = {}
            if ids:
                from tahmeed.services.accountant_service import get_cashier_names
                cashier_names = await get_cashier_names(ids)
            if seq != self._load_gen:
                return
            self._cashier_names = cashier_names
            self._pending_row_meta.clear()
            self._populate(txs)
            show_cashier = self._merged_mode or bool(upload_id)
            self._table.setColumnHidden(COL_CASHIER, not show_cashier)
            restored = self._restore_local_draft()
            if restored:
                self._show_draft_restored_notice(*restored)
        except Exception as exc:
            if seq == self._load_gen:
                QMessageBox.critical(self, "Error", f"Failed to load:\n{exc}")
        finally:
            if seq == self._load_gen:
                self._hide_register_loading()

    def set_merged_mode(self, merged: bool) -> None:
        """Switch My entries ↔ Merged (all cashiers for the day)."""
        if bool(merged) == self._merged_mode:
            return
        self._commit_open_editor()
        if self.has_unsaved_work():
            self._flush_local_draft()
            resp = QMessageBox.question(
                self, "Unsaved changes",
                "You have unsaved changes.\nSave them before switching mode?",
                QMessageBox.Yes | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if resp == QMessageBox.Cancel:
                self.mode_changed.emit(self._merged_mode)
                return
            if resp == QMessageBox.Yes:
                asyncio.ensure_future(self._save_then_switch_mode(bool(merged)))
                return
            self._clear_local_draft()
        self._merged_mode = bool(merged)
        self._reset_edit_state()
        self.mode_changed.emit(self._merged_mode)
        self._show_register_loading("Loading…")
        asyncio.ensure_future(self._load_date(self._current_date))

    async def _save_then_switch_mode(self, merged: bool) -> None:
        ok = await self._do_save()
        if not ok:
            self.mode_changed.emit(self._merged_mode)
            return
        self._merged_mode = merged
        self.mode_changed.emit(self._merged_mode)
        self._show_register_loading("Loading…")
        await self._load_date(self._current_date)

    def submit_for_verify(self) -> None:
        """Submit every draft row for the current calendar day to Verify."""
        if self._submit_in_flight or self._save_in_flight:
            return
        asyncio.ensure_future(self._do_submit_for_verify())

    async def _do_submit_for_verify(self) -> None:
        if self._submit_in_flight or self._save_in_flight:
            return
        self._submit_in_flight = True
        self.save_busy_changed.emit(True)
        try:
            self._commit_open_editor()
            if self.has_unsaved_work():
                resp = QMessageBox.question(
                    self, "Unsaved changes",
                    "Save changes before submitting this day for verify?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Yes,
                )
                if resp != QMessageBox.Yes:
                    return
                if not await self._do_save():
                    return
            d = self._current_date
            label = d.strftime("%d %b %Y")
            resp = QMessageBox.question(
                self, "Submit for Verify",
                f"Submit all draft entries for {label} to the Verify inbox?\n\n"
                "This sends the whole day's transactions (all cashiers).",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if resp != QMessageBox.Yes:
                return
            try:
                n = await submit_day_for_verify(d)
                QMessageBox.information(
                    self, "Submitted",
                    f"{n:,} entr{'y' if n == 1 else 'ies'} sent to Verify for {label}.",
                )
                await self._load_date(d)
            except Exception as exc:
                QMessageBox.critical(self, "Submit Failed", str(exc))
        finally:
            self._submit_in_flight = False
            if not self._save_in_flight:
                self.save_busy_changed.emit(False)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load_date(self._current_date))
        asyncio.ensure_future(self._load_cashier_settings())

    def reload_settings(self) -> None:
        """Re-read the restrict toggles, locked sub-items and fleet list without
        touching the grid rows (so unsaved entries survive). Called on entering
        the table tab."""
        asyncio.ensure_future(self._load_categories())
        asyncio.ensure_future(self._load_cashier_settings())
        asyncio.ensure_future(self._load_locked_subitems())
        asyncio.ensure_future(self._load_fleet_numbers())
        asyncio.ensure_future(self._load_people_names())
        asyncio.ensure_future(self._load_description_cache())

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
        self._clear_column_filters()
        self._clear_cut_marquee()
        self._undo_stack.clear()
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

        date_str = format_register_date(tx.date) if tx.date else ""
        date_item = saved_item(date_str)
        if tx.date and tx.created_at and tx.date.date() != tx.created_at.date():
            date_item.setBackground(QBrush(MISMATCH_BG))
            date_item.setToolTip(
                f"Transaction dated {tx.date.strftime('%d %b %y')} but submitted on "
                f"{tx.created_at.strftime('%d %b %y')}"
            )
        self._table.setItem(row, COL_DATE, date_item)

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
        cashier = self._cashier_names.get(tx.cashier_id, "—") if tx.cashier_id else "—"
        self._table.setItem(row, COL_CASHIER, saved_item(cashier))

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
        self.stats_updated.emit(n, tzs, refund, self._current_date)

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
        tx_date = _parse_optional_date(date_str, default_year=self._current_date.year)
        if tx_date is None:
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
            item_name = cat.name.upper()
        elif item_name and self._restrict_items:
            raise ValueError(f'"{item_name}" is not a known item.')
        elif item_name:
            item_name = item_name.upper()

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
                description = match.upper()
            else:
                description = description.upper()
        else:
            description = description.upper()

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
                            f'"{norm.value}" is not a registered fleet vehicle.'
                        )
                else:
                    truck_number = matched

        ref_text = txt(COL_REF).upper()
        orig = self._saved_txs.get(row)
        return Transaction(
            date=tx_date,
            description=description,
            item=item_name,
            category_name=item_name or None,
            category_id=meta.get("category_id"),
            truck_number=truck_number,
            amount=amount,
            currency=meta.get("currency") or "TZS",
            memo=txt(COL_MEMO).upper(),
            receipt_status=rcpt_status,
            ref_float=ref_text,
            notes_flag=_is_refund_float(ref_text),
            ownership=txt(COL_OWN).upper(),
            approver=txt(COL_APR).upper(),
            payee=txt(COL_PAYEE).upper(),
            cheque=txt(COL_CHEQUE).upper(),
            cashier_id=self._user._id,
            day_order=row,
            register_status="draft",
            daily_import_id=meta.get("daily_import_id"),
            daily_import_source=meta.get("daily_import_source"),
            date_discrepancy=bool(meta.get("date_discrepancy")),
            import_primary_date=meta.get("import_primary_date"),
            lpo_do=(meta.get("lpo_do") or "").upper(),
            do_number=(meta.get("do_number") or "").upper(),
            reported_date=getattr(orig, "reported_date", None) if orig is not None else None,
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
        if discard:
            # Keep typed new rows in the local draft; drop dirty saved-row edits.
            self._flush_local_draft(include_dirty=False)
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
        self._schedule_draft_autosave()

    def _updates_from_row(self, row: int) -> Optional[dict]:
        """Build the $set payload for an edited saved row from its cell values.
        Returns None when the row has no description. Raises ValueError on
        validation errors (bad item / locked description / unregistered truck)."""
        tx = self._build_transaction_from_row(row)
        if tx is None:
            return None
        return {
            "date": tx.date,
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
        COL_DATE, COL_ITEM, COL_DESC, COL_TRUCK, COL_MEMO,
        COL_REF, COL_TZS, COL_RECEIPT, COL_OWN, COL_APR, COL_PAYEE, COL_CHEQUE,
    ]

    def export_as(self, fmt: str = "xlsx") -> None:
        """Export visible rows as Excel, CSV, or PDF.

        ``fmt`` is one of ``xlsx``, ``csv``, or ``pdf``. Format is chosen from
        the Export menu so the save dialog only asks for a filename/location.
        """
        fmt = (fmt or "xlsx").lower().strip()
        if fmt not in ("xlsx", "csv", "pdf"):
            fmt = "xlsx"

        filters = {
            "xlsx": ("Excel Workbook (*.xlsx)", ".xlsx"),
            "csv": ("CSV File (*.csv)", ".csv"),
            "pdf": ("PDF Report (*.pdf)", ".pdf"),
        }
        file_filter, ext = filters[fmt]
        default_name = f"register_{self._current_date.isoformat()}{ext}"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export register as {ext.lstrip('.').upper()}",
            default_name, file_filter,
        )
        if not path:
            return
        if not path.lower().endswith(ext):
            path = f"{path}{ext}"

        rows = self._visible_export_rows()
        if not rows:
            QMessageBox.information(self, "Nothing to export",
                                    "There are no visible rows to export.")
            return
        try:
            if fmt == "pdf":
                self._write_pdf(path, rows)
            elif fmt == "csv":
                self._write_csv(path, rows)
            else:
                self._write_xlsx(path, rows)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Export complete",
                               f"{len(rows)} row(s) exported to:\n{path}")

    def export_xlsx(self) -> None:
        """Backward-compatible alias — opens Export as Excel."""
        self.export_as("xlsx")

    def export_csv(self) -> None:
        """Backward-compatible alias — opens Export as CSV."""
        self.export_as("csv")

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

    def _write_pdf(self, path: str, rows: List[list]) -> None:
        from tahmeed.services.daily_register_pdf import export_daily_register_pdf

        export_daily_register_pdf(
            path,
            rows=rows,
            register_date=self._current_date,
        )

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
            self._col_filters[col] = set(accepted)
        else:
            self._col_filters.pop(col, None)
        self._prune_stale_column_filters(changed_col=col)
        self._sync_filter_header()
        self._apply_filters()

    def _clear_column_filters(self) -> None:
        self._col_filters.clear()
        hdr = self._table.horizontalHeader()
        if isinstance(hdr, _FilterHeaderView):
            hdr.clear_filters()

    def _sync_filter_header(self) -> None:
        hdr = self._table.horizontalHeader()
        if isinstance(hdr, _FilterHeaderView):
            hdr.sync_active(self._col_filters)

    def _prune_stale_column_filters(self, *, changed_col: int) -> None:
        """Drop selections on other columns that no longer appear after chaining."""
        if not self._col_filters:
            return
        # Iterate until stable — narrowing one column can invalidate another.
        for _ in range(len(self._col_filters) + 1):
            changed = False
            for col in list(self._col_filters.keys()):
                if col == changed_col:
                    continue
                available = self._filter_menu_values(col)
                kept = {v for v in self._col_filters.get(col, set()) if v in available}
                if not kept:
                    self._col_filters.pop(col, None)
                    changed = True
                elif kept != self._col_filters[col]:
                    self._col_filters[col] = kept
                    changed = True
            if not changed:
                break

    def _cell_filter_value(self, row: int, col: int) -> str:
        it = self._table.item(row, col)
        if col == COL_RECEIPT:
            raw = it.text().strip().lower() if it else ""
            return _RCPT_LABEL.get(raw, raw)
        return it.text().strip() if it else ""

    def _iter_filter_source_indices(self):
        for row in range(self._table.rowCount()):
            if row < self._saved_count or self._row_has_data(row):
                yield row

    def _row_matches_other_filters(self, row: int, *, exclude_col: int) -> bool:
        """True if *row* matches search + every active column filter except *exclude_col*."""
        search = self._search_text
        if search:
            matched = False
            for c in range(self._table.columnCount()):
                it = self._table.item(row, c)
                if not it:
                    continue
                if c == COL_RECEIPT:
                    label = _RCPT_LABEL.get(it.text().strip().lower(), it.text())
                    if search in label.lower() or search in it.text().lower():
                        matched = True
                        break
                elif search in it.text().lower():
                    matched = True
                    break
            if not matched:
                return False

        for c, accepted in self._col_filters.items():
            if c == exclude_col or not accepted:
                continue
            if self._cell_filter_value(row, c) not in accepted:
                return False
        return True

    def _filter_menu_values(self, col: int) -> set:
        """Distinct values present in the table for *col*, chained through other filters."""
        rows: List[dict] = []
        for row in self._iter_filter_source_indices():
            if not self._row_matches_other_filters(row, exclude_col=col):
                continue
            m: dict = {}
            for c in range(self._table.columnCount()):
                if c == COL_SNO:
                    continue
                v = self._cell_filter_value(row, c)
                if v:
                    m[c] = v
            if m:
                rows.append(m)
        # active_filters already applied via _row_matches_other_filters; pass empty
        # here so cascade_column_values just collects target_col values.
        return cascade_column_values(rows, target_col=col, active_filters={})

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
                if self._cell_filter_value(row, col) not in accepted:
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
            if col == COL_DESC and item.text().strip():
                from tahmeed.services.cashier_service import remember_description
                remember_description(item.text())
                if not self._bulk_mutating:
                    desc = item.text().strip()
                    QTimer.singleShot(
                        0, lambda r=row, d=desc: self._kick_auto_fill_item(r, d)
                    )
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
        if col not in READONLY_COLS and col not in CHECK_COLS and col not in (COL_DATE,):
            self._table.blockSignals(True)
            self._sync_row_date(row)
            self._table.blockSignals(False)

        # Item / Description / Truck validation (canonicalise, restrict, locked lists)
        if col == COL_ITEM and item.text().strip():
            self._validate_item_cell(row, item)
        elif col == COL_DESC and item.text().strip():
            from tahmeed.services.cashier_service import remember_description
            remember_description(item.text())
            self._validate_locked_description(row, item)
            if not self._bulk_mutating:
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

        self._schedule_draft_autosave()

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
        return format_register_date(self._current_date)

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
            if key == Qt.Key_Z:    self._undo();                               return
            if key == Qt.Key_A:    self._table.selectAll();                    return
            if key == Qt.Key_D:    self._fill_down();                          return
            if key == Qt.Key_R:    self._fill_right();                         return
            if key == Qt.Key_Home: self._table.setCurrentCell(0, 0);          return
            if key == Qt.Key_End:  self._go_to_last_cell();                   return

        if key == Qt.Key_Escape:
            if self._cut_cells:
                self._clear_cut_marquee()
                return

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
        """If the focused cell is an empty Date cell, write the register date."""
        row, col = self._table.currentRow(), self._table.currentColumn()
        if col != COL_DATE or row < self._saved_count:
            return
        it = self._table.item(row, col)
        if it is not None and it.text().strip():
            return
        cur = self._current_date
        today_str = format_register_date(cur)
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

    def _push_undo_cells(self, cells: dict) -> None:
        """Snapshot {(row, col): text} before a mutating edit."""
        if not cells:
            return
        self._undo_stack.append(dict(cells))
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack = self._undo_stack[-self._undo_limit:]

    def _snapshot_selection(self) -> dict:
        snap = {}
        for it in self._table.selectedItems():
            if it.column() in READONLY_COLS:
                continue
            snap[(it.row(), it.column())] = it.text()
        return snap

    def _snapshot_rows(self, rows: list) -> dict:
        snap = {}
        for row in rows:
            for col in range(self._table.columnCount()):
                if col == COL_SNO:
                    continue
                it = self._table.item(row, col)
                snap[(row, col)] = it.text() if it else ""
        return snap

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        snap = self._undo_stack.pop()
        self._clear_cut_marquee()
        self._table.blockSignals(True)
        try:
            for (row, col), text in snap.items():
                if row < 0 or row >= self._table.rowCount():
                    continue
                if col == COL_SNO:
                    continue
                if row < self._saved_count and not self._edit_mode:
                    continue
                it = self._table.item(row, col)
                if it is None:
                    it = QTableWidgetItem("")
                    if col == COL_CASHIER:
                        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    else:
                        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    self._table.setItem(row, col, it)
                elif col == COL_CASHIER:
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                it.setText(text)
                if row < self._saved_count and self._edit_mode:
                    self._dirty_rows.add(row)
        finally:
            self._table.blockSignals(False)
        self._renumber()
        self._update_footer()
        self._table.viewport().update()

    def _clear_cut_marquee(self) -> None:
        self._cut_cells = set()
        self._cut_payload = {}
        self._cut_is_rows = False
        self._table.viewport().update()

    def _has_cut_buffer(self) -> bool:
        return bool(self._cut_cells and self._cut_payload)

    def _copy(self) -> None:
        # Use selectedIndexes so blank cells stay in the rectangle (Excel-aligned TSV).
        indexes = self._table.selectedIndexes()
        if not indexes:
            return
        rows = sorted({i.row() for i in indexes})
        cols = sorted({i.column() for i in indexes})
        lines = []
        for row in rows:
            row_cells = []
            for col in cols:
                it = self._table.item(row, col)
                if it is None:
                    row_cells.append("")
                elif col in CHECK_COLS:
                    row_cells.append("1" if it.data(Qt.UserRole) else "0")
                else:
                    row_cells.append(it.text())
            lines.append("\t".join(row_cells))
        QApplication.clipboard().setText("\n".join(lines))

    def _cut(self) -> None:
        """Excel-style cut: copy + dashed marquee; content stays until paste/insert."""
        sel_rows = sorted({i.row() for i in self._table.selectedIndexes()})
        if sel_rows and self._selection_is_full_rows(sel_rows):
            self._cut_rows(sel_rows)
            return

        items = self._table.selectedItems()
        if not items:
            return
        editable = []
        for it in items:
            row, col = it.row(), it.column()
            if col in READONLY_COLS:
                continue
            if row < self._saved_count and not self._edit_mode:
                continue
            editable.append(it)
        if not editable:
            return

        self._push_undo_cells(self._snapshot_selection())
        self._copy()

        rows = sorted({it.row() for it in editable})
        cols = sorted({it.column() for it in editable})
        cell_map = {(it.row(), it.column()): it for it in editable}
        grid = []
        cut_cells = set()
        for row in rows:
            line = []
            for col in cols:
                it = cell_map.get((row, col))
                text = it.text() if it else ""
                line.append(text)
                if it is not None:
                    cut_cells.add((row, col))
            grid.append(line)

        self._cut_cells = cut_cells
        self._cut_is_rows = False
        self._cut_payload = {
            "kind": "cells",
            "rows": rows,
            "cols": cols,
            "grid": grid,
        }
        self._table.viewport().update()

    def _selection_is_full_rows(self, rows: list) -> bool:
        """True when the selection covers every data column for each row."""
        ncols = self._table.columnCount()
        data_cols = {c for c in range(ncols) if c not in READONLY_COLS}
        if not data_cols:
            return False
        selected = {(i.row(), i.column()) for i in self._table.selectedIndexes()}
        for row in rows:
            for col in data_cols:
                if (row, col) not in selected:
                    return False
        return True

    def _serialize_row(self, row: int) -> list:
        cells = []
        for col in range(self._table.columnCount()):
            if col == COL_SNO:
                cells.append("")
                continue
            it = self._table.item(row, col)
            if it is None:
                cells.append("")
            elif col in CHECK_COLS:
                cells.append("1" if it.data(Qt.UserRole) else "0")
            else:
                cells.append(it.text())
        return cells

    def _row_value_map(self, row: int) -> dict:
        """Column index → exact cell text for cut/insert (preserves Receipt/Cashier)."""
        values = {}
        for col in range(self._table.columnCount()):
            if col == COL_SNO:
                continue
            it = self._table.item(row, col)
            if it is None:
                values[col] = ""
            elif col in CHECK_COLS:
                values[col] = "1" if it.data(Qt.UserRole) else "0"
            else:
                values[col] = it.text()
        return values

    def _capture_row_meta(self, row: int) -> dict:
        """Identity/metadata needed to move a row without losing cashier or tx id."""
        pending = self._pending_row_meta.get(row)
        return {
            "was_saved": row < self._saved_count,
            "saved_id": self._saved_ids.get(row),
            "saved_tx": self._saved_txs.get(row),
            "pending": dict(pending) if pending else None,
            "dirty": row in self._dirty_rows,
        }

    def _cut_rows(self, rows: list) -> None:
        """Mark whole rows as cut (marquee) — do not delete until paste/insert."""
        movable = []
        for row in rows:
            if row >= self._saved_count:
                movable.append(row)
            elif self._merged_mode and self._edit_mode:
                tx = self._saved_txs.get(row)
                if tx is not None and (getattr(tx, "register_status", "") or "") == "draft":
                    movable.append(row)
        if not movable:
            self._copy()
            return

        self._push_undo_cells(self._snapshot_rows(movable))
        maps = [self._row_value_map(r) for r in movable]
        metas = [self._capture_row_meta(r) for r in movable]
        lines = ["\t".join(self._serialize_row(r)) for r in movable]
        QApplication.clipboard().setText(_ROWS_CLIP_PREFIX + "\n".join(lines))

        cut_cells = set()
        for row in movable:
            for col in range(self._table.columnCount()):
                if col == COL_SNO:
                    continue
                cut_cells.add((row, col))

        self._cut_cells = cut_cells
        self._cut_is_rows = True
        self._cut_payload = {
            "kind": "rows",
            "rows": list(movable),
            "lines": lines,
            "maps": maps,
            "row_metas": metas,
        }
        self._table.viewport().update()

    def _write_row_values(self, row: int, values: dict) -> list:
        """Write a column→text map onto *row*. Returns truck cells to finalize."""
        truck_cells: list = []
        for col, cell in values.items():
            if col >= self._table.columnCount() or col == COL_SNO:
                continue
            if col == COL_CASHIER:
                it = QTableWidgetItem(str(cell) if cell is not None else "")
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._table.setItem(row, col, it)
                continue
            if col in CHECK_COLS:
                it = QTableWidgetItem()
                it.setData(Qt.UserRole, str(cell).strip() in ("1", "true", "True", "YES"))
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                self._table.setItem(row, col, it)
            elif col == COL_RECEIPT:
                it = QTableWidgetItem(_receipt_paste_value(str(cell)))
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                self._table.setItem(row, col, it)
            elif col == COL_TZS:
                amt = _parse_amount_text(str(cell))
                text = f"{amt:,.2f}" if str(cell).strip() else ""
                it = QTableWidgetItem(text)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if amt < 0:
                    it.setForeground(NEG_COLOR)
                self._table.setItem(row, col, it)
            elif col == COL_TRUCK:
                raw = str(cell).strip()
                self._table.setItem(
                    row, col, QTableWidgetItem(raw.upper() if raw else "")
                )
                if raw:
                    truck_cells.append((row, raw))
            else:
                self._table.setItem(
                    row, col, QTableWidgetItem(_upper_text(col, str(cell).strip()))
                )
        return truck_cells

    def _style_moved_row(self, row: int, was_saved: bool) -> None:
        """Restore saved/edit styling after a cut→insert move."""
        if not was_saved:
            return
        if self._edit_mode:
            bg = QBrush(DIRTY_BG if row in self._dirty_rows else EDIT_BG)
            editable = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
            ro = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        else:
            bg = QBrush(SAVED_BG)
            editable = ro = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        for col in range(self._table.columnCount()):
            it = self._table.item(row, col)
            if it is None:
                continue
            it.setBackground(bg)
            it.setFlags(ro if col in READONLY_COLS else editable)

    def _restore_moved_row_meta(self, row: int, meta: dict) -> None:
        """Re-attach tx id / import meta / dirty flag onto an inserted cut row."""
        if not meta:
            return
        pending = meta.get("pending")
        if pending:
            self._pending_row_meta[row] = dict(pending)
        saved_id = meta.get("saved_id")
        saved_tx = meta.get("saved_tx")
        was_saved = bool(meta.get("was_saved") or saved_id is not None)
        if saved_id is not None:
            self._saved_ids[row] = saved_id
        if saved_tx is not None:
            self._saved_txs[row] = saved_tx
        if was_saved and self._edit_mode:
            self._dirty_rows.add(row)
        elif meta.get("dirty"):
            self._dirty_rows.add(row)
        self._style_moved_row(row, was_saved)

    def _insert_cut_cells(self) -> None:
        """Insert Cut Cells — move the cut buffer to the current position."""
        if not self._has_cut_buffer():
            return
        if self._cut_payload.get("kind") == "rows":
            maps = self._cut_payload.get("maps")
            if maps:
                self._paste_row_maps(maps, clear_cut_after=True)
            else:
                lines = list(self._cut_payload.get("lines") or [])
                self._paste_rows("\n".join(lines), clear_cut_after=True)
            return
        self._paste()

    def _paste_row_maps(self, maps: list, clear_cut_after: bool = True) -> None:
        """Insert rows from exact column→value maps (preserves Receipt etc.)."""
        if not maps:
            return
        metas: list = []
        source_rows: list = []
        if (
            clear_cut_after
            and self._has_cut_buffer()
            and self._cut_is_rows
            and self._cut_payload.get("kind") == "rows"
        ):
            metas = list(self._cut_payload.get("row_metas") or [])
            source_rows = list(self._cut_payload.get("rows") or [])

        cur = self._table.currentRow()
        if self._merged_mode and self._edit_mode:
            insert_at = max(cur, 0)
        else:
            insert_at = max(cur, self._saved_count)

        # Remove cut sources first so row maps stay consistent, then insert.
        if source_rows:
            removed_before = sum(1 for r in source_rows if r < insert_at)
            self._table.blockSignals(True)
            try:
                for row in sorted(source_rows, reverse=True):
                    if row < 0 or row >= self._table.rowCount():
                        continue
                    self._shift_row_maps_on_remove(row)
                    self._table.removeRow(row)
                    if row < self._saved_count:
                        self._saved_count -= 1
            finally:
                self._table.blockSignals(False)
            insert_at = max(0, insert_at - removed_before)
            self._clear_cut_marquee()
            clear_cut_after = False
            min_rows = self._saved_count + DEFAULT_EDITABLE_ROWS
            if self._table.rowCount() < min_rows:
                start = self._table.rowCount()
                self._table.setRowCount(min_rows)
                self._init_editable_rows(start, min_rows)

        truck_cells: list = []
        self._bulk_mutating = True
        prev = self._table.blockSignals(True)
        try:
            for i, values in enumerate(maps):
                meta = metas[i] if i < len(metas) else {}
                was_saved = bool(
                    meta.get("was_saved") or meta.get("saved_id") is not None
                )
                # Keep saved drafts in the saved prefix; new rows stay below it.
                if was_saved:
                    insert_at = min(insert_at, self._saved_count)
                else:
                    insert_at = max(insert_at, self._saved_count)

                self._shift_row_maps_on_insert(insert_at)
                self._table.insertRow(insert_at)
                self._init_editable_rows(insert_at, insert_at + 1)
                truck_cells.extend(self._write_row_values(insert_at, values))
                self._restore_moved_row_meta(insert_at, meta)
                self._sync_row_date(insert_at)
                if was_saved:
                    self._saved_count += 1
                insert_at += 1
        finally:
            self._table.blockSignals(prev)
            self._bulk_mutating = False
        self._renumber()
        self._update_footer()
        self._finalize_truck_cells(truck_cells)
        if clear_cut_after and self._has_cut_buffer() and self._cut_is_rows:
            self._clear_cut_source_cells()
        self.edit_state_changed.emit(self._edit_mode, len(self._dirty_rows))
        self._schedule_draft_autosave()

    def _clear_cut_source_cells(self) -> None:
        """After a successful paste/insert, clear/remove the original cut source."""
        if not self._cut_cells:
            return
        rows_touched = set()
        self._table.blockSignals(True)
        try:
            if self._cut_is_rows and self._cut_payload.get("kind") == "rows":
                for row in sorted(self._cut_payload.get("rows") or [], reverse=True):
                    if row < 0 or row >= self._table.rowCount():
                        continue
                    self._shift_row_maps_on_remove(row)
                    self._table.removeRow(row)
                    if row < self._saved_count:
                        self._saved_count -= 1
                min_rows = self._saved_count + DEFAULT_EDITABLE_ROWS
                if self._table.rowCount() < min_rows:
                    start = self._table.rowCount()
                    self._table.setRowCount(min_rows)
                    self._init_editable_rows(start, min_rows)
            else:
                for row, col in list(self._cut_cells):
                    if row < 0 or row >= self._table.rowCount():
                        continue
                    if row < self._saved_count and not self._edit_mode:
                        continue
                    it = self._table.item(row, col)
                    if it is not None:
                        it.setText("")
                        rows_touched.add(row)
                        if row < self._saved_count:
                            self._dirty_rows.add(row)
                for row in rows_touched:
                    self._sync_row_date(row)
        finally:
            self._table.blockSignals(False)
        self._clear_cut_marquee()
        self._renumber()
        self._update_footer()

    def _shift_row_maps_on_insert(self, at_row: int) -> None:
        def _shift(mapping: dict) -> dict:
            return {
                (k + 1 if k >= at_row else k): v
                for k, v in mapping.items()
            }
        self._pending_row_meta = _shift(self._pending_row_meta)
        self._saved_ids = _shift(self._saved_ids)
        self._saved_txs = _shift(self._saved_txs)
        self._dirty_rows = {(r + 1 if r >= at_row else r) for r in self._dirty_rows}
        if self._cut_cells:
            self._cut_cells = {
                (r + 1 if r >= at_row else r, c) for r, c in self._cut_cells
            }
        if self._cut_is_rows and self._cut_payload.get("rows"):
            self._cut_payload["rows"] = [
                r + 1 if r >= at_row else r for r in self._cut_payload["rows"]
            ]

    def _shift_row_maps_on_remove(self, at_row: int) -> None:
        def _shift(mapping: dict) -> dict:
            out = {}
            for k, v in mapping.items():
                if k == at_row:
                    continue
                out[k - 1 if k > at_row else k] = v
            return out
        self._pending_row_meta = _shift(self._pending_row_meta)
        self._saved_ids = _shift(self._saved_ids)
        self._saved_txs = _shift(self._saved_txs)
        self._dirty_rows = {
            (r - 1 if r > at_row else r)
            for r in self._dirty_rows
            if r != at_row
        }
        if self._cut_cells:
            self._cut_cells = {
                (r - 1 if r > at_row else r, c)
                for r, c in self._cut_cells
                if r != at_row
            }
        if self._cut_is_rows and self._cut_payload.get("rows"):
            self._cut_payload["rows"] = [
                r - 1 if r > at_row else r
                for r in self._cut_payload["rows"]
                if r != at_row
            ]

    def _paste_rows(self, body: str, clear_cut_after: bool = True) -> None:
        """Insert cut/copied rows at the current position."""
        lines = [ln for ln in body.splitlines() if ln.strip() != "" or "\t" in ln]
        if not lines:
            return
        # Prefer exact maps + identity when this paste is finishing a row cut.
        if (
            clear_cut_after
            and self._has_cut_buffer()
            and self._cut_is_rows
            and self._cut_payload.get("maps")
        ):
            self._paste_row_maps(
                self._cut_payload["maps"], clear_cut_after=True
            )
            return

        cur = self._table.currentRow()
        if self._merged_mode and self._edit_mode:
            insert_at = max(cur, 0)
        else:
            insert_at = max(cur, self._saved_count)

        truck_cells: list = []
        self._bulk_mutating = True
        prev = self._table.blockSignals(True)
        try:
            for line in lines:
                self._shift_row_maps_on_insert(insert_at)
                self._table.insertRow(insert_at)
                self._init_editable_rows(insert_at, insert_at + 1)
                cells = line.split("\t")
                values = {
                    col: cell
                    for col, cell in enumerate(cells)
                    if col < self._table.columnCount() and col != COL_SNO
                }
                truck_cells.extend(self._write_row_values(insert_at, values))
                self._sync_row_date(insert_at)
                if insert_at < self._saved_count:
                    self._saved_count += 1
                insert_at += 1
        finally:
            self._table.blockSignals(prev)
            self._bulk_mutating = False
        self._renumber()
        self._update_footer()
        self._finalize_truck_cells(truck_cells)
        if clear_cut_after and self._has_cut_buffer() and self._cut_is_rows:
            self._clear_cut_source_cells()

    def _paste(self) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return

        if text.startswith(_ROWS_CLIP_PREFIX):
            if self._has_cut_buffer() and self._cut_payload.get("maps"):
                self._paste_row_maps(
                    self._cut_payload["maps"], clear_cut_after=True
                )
            else:
                self._paste_rows(text[len(_ROWS_CLIP_PREFIX):])
            return

        self._push_undo_cells(self._snapshot_selection())

        lines = text.splitlines()

        # selectedIndexes() covers blank rows (which have no QTableWidgetItem and
        # therefore never appear in selectedItems()).
        # In edit mode, paste may land on saved rows; otherwise stay below them.
        min_row = 0 if self._edit_mode else self._saved_count
        sel_indexes = self._table.selectedIndexes()
        if sel_indexes:
            start_row = max(min(i.row() for i in sel_indexes), min_row)
            start_col = min(i.column() for i in sel_indexes)
            sel_rows = sorted({i.row() for i in sel_indexes if i.row() >= min_row})
            sel_cols = sorted({i.column() for i in sel_indexes})
        else:
            start_row = max(self._table.currentRow(), min_row)
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
                            norm = _receipt_paste_value(cell_value)
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
                    if row < self._saved_count and self._edit_mode:
                        self._mark_dirty(row)
                    self._sync_row_date(row)
                self._table.blockSignals(prev)
                self._renumber()
                self._finalize_truck_cells(truck_cells)
            else:
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
                        if row < self._saved_count and not self._edit_mode:
                            continue
                        touched_rows.add(row)
                        if col in CHECK_COLS:
                            it = self._table.item(row, col) or QTableWidgetItem()
                            it.setData(Qt.UserRole, cell.strip() in ("1", "true", "True", "YES"))
                            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                            self._table.setItem(row, col, it)
                        elif col == COL_RECEIPT:
                            norm = _receipt_paste_value(cell)
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
                    if row < self._saved_count and self._edit_mode:
                        self._mark_dirty(row)
                    self._sync_row_date(row)
                self._table.blockSignals(prev)
                self._renumber()
                self._finalize_truck_cells(truck_cells)
        finally:
            self._bulk_mutating = False

        if self._has_cut_buffer() and not self._cut_is_rows:
            self._clear_cut_source_cells()
        self._update_footer()
        self._schedule_draft_autosave()

    def _clear_selected(self) -> None:
        snap = self._snapshot_selection()
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
        if snap:
            self._push_undo_cells(snap)
        self._renumber()
        self._update_footer()

    def _fill_down(self) -> None:
        """Ctrl+D: copy the top row of the selection into all rows below it."""
        items = self._table.selectedItems()
        if not items:
            return
        self._push_undo_cells(self._snapshot_selection())
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
        self._push_undo_cells(self._snapshot_selection())
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
        if 0 <= row < self._saved_count and not self._edit_mode:
            act = menu.addAction("Delete Saved Entry")
            act.triggered.connect(lambda: self._delete_saved_row(row))
        else:
            menu.addAction("Copy",  self._copy)
            menu.addAction("Cut",   self._cut)
            menu.addAction("Paste", self._paste)
            if self._has_cut_buffer():
                menu.addAction("Insert Cut Cells", self._insert_cut_cells)
            menu.addSeparator()
            menu.addAction("Insert Row Above",       self._insert_above)
            menu.addAction("Insert Row Below",       self._insert_below)
            menu.addAction("Delete Selected Row(s)", self._delete_rows)
            if self._undo_stack:
                menu.addSeparator()
                menu.addAction("Undo", self._undo)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _insert_above(self) -> None:
        row = max(self._table.currentRow(), self._saved_count)
        self._shift_row_maps_on_insert(row)
        self._table.insertRow(row)
        self._init_editable_rows(row, row + 1)
        self._renumber()

    def _insert_below(self) -> None:
        row = max(self._table.currentRow() + 1, self._saved_count)
        self._shift_row_maps_on_insert(row)
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
            self._shift_row_maps_on_remove(row)
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
        tx = self._saved_txs.get(row)
        it = self._table.item(row, COL_DESC)
        desc = it.text() if it else "?"
        is_pending_edit = bool(
            tx is not None
            and getattr(tx, "original_transaction_id", None)
            and not getattr(tx, "verified", False)
        )
        is_verified = bool(tx is not None and getattr(tx, "verified", False))
        is_submitted = (
            tx is not None
            and not is_verified
            and (getattr(tx, "register_status", "") or "submitted") != "draft"
        )
        if is_verified:
            msg = (
                f'Request deletion of approved expense:\n"{desc}"?\n\n'
                "It will leave Master Expenses immediately and appear in the "
                "accountant's Verify → Deleted tab for confirm or restore."
            )
        elif is_pending_edit:
            msg = (
                f'Delete pending edit:\n"{desc}"?\n\n'
                "This undoes the edit. The original approved expense stays in Master."
            )
        elif is_submitted:
            msg = (
                f'Delete submitted transaction:\n"{desc}"?\n\n'
                "It will be removed from the Verify inbox."
            )
        else:
            msg = f'Delete saved transaction:\n"{desc}"?'
        if (
            QMessageBox.question(
                self, "Delete Entry",
                msg,
                QMessageBox.Yes | QMessageBox.No,
            )
            == QMessageBox.Yes
        ):
            asyncio.ensure_future(self._do_delete_saved(tx_id))

    async def _do_delete_saved(self, tx_id) -> None:
        try:
            cashier_id = getattr(self._user, "_id", None)
            result = await request_or_delete_transaction(tx_id, cashier_id)
            if result == "not_found":
                QMessageBox.warning(
                    self, "Not Found",
                    "That entry was already removed.",
                )
            elif result == "deletion_requested":
                QMessageBox.information(
                    self, "Deletion Requested",
                    "The approved expense was sent to Verify → Deleted.\n"
                    "An accountant must confirm permanent removal or restore it.",
                )
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

        try:
            preview = await run_daily_import_flow(self)
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", f"Could not import this file:\n\n{exc}")
            return
        if preview is None:
            return
        await self.apply_daily_import_preview(preview)

    async def apply_daily_import_preview(self, preview) -> None:
        """Navigate to the Excel main date and stage rows for the user to Save."""
        from tahmeed.services.daily_import_service import staged_row_payload
        from tahmeed.ui.widgets.upload_busy import UploadBusy

        primary = preview.primary_date or self._current_date
        if primary != self._current_date:
            if self.has_unsaved_work():
                ok = await self.confirm_leave()
                if not ok:
                    return
            self._reset_edit_state()
            self._current_date = primary
            with UploadBusy(self, "Opening import date…", title="Import"):
                await self._load_date(primary)

        payloads = [staged_row_payload(row, preview) for row in preview.rows]
        # Queue truck issues now, but open the correction dialog only after this
        # async import task finishes — nested dialog.exec() + ensure_future crashes
        # under Python 3.14 / qasync.
        self._suppress_truck_dialog = True
        try:
            with UploadBusy(
                self,
                f"Loading {len(payloads):,} row(s) into table…",
                title="Import",
            ) as busy:
                busy.update(f"Loading {len(payloads):,} row(s) into table…")
                self._load_staged_import_rows(payloads)
            QMessageBox.information(
                self,
                "Import ready",
                f"Loaded {len(payloads):,} row(s) from \"{preview.source_filename}\" "
                f"under register date {primary.strftime('%d/%m/%Y')}.\n\n"
                "Excel row dates are kept as written. Open this upload anytime to "
                "see every row in the batch.\n\n"
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
                    "lpo_do": (data.get("lpo_do") or "").upper(),
                    "do_number": (data.get("do_number") or "").upper(),
                }

                dt = data.get("date")
                date_str = format_register_date(dt) if dt else ""
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
                    self._table.setItem(
                        target, COL_REF, QTableWidgetItem(_upper_text(COL_REF, ref))
                    )

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
        self._schedule_draft_autosave()

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
                            formatted = format_register_date(row_data[file_col])
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
        if self._save_in_flight or self._submit_in_flight:
            return
        asyncio.ensure_future(self._do_save())

    # ------------------------------------------------------------------
    # QuickBooks-style toolbar actions
    # ------------------------------------------------------------------

    def _data_rows(self) -> List[int]:
        """Saved + non-empty editable rows (for Find navigation)."""
        rows: List[int] = []
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            if row < self._saved_count or self._row_has_data(row):
                rows.append(row)
        return rows

    def toolbar_find(self, direction: int) -> None:
        """Move selection to previous (-1) or next (+1) data row."""
        rows = self._data_rows()
        if not rows:
            return
        cur = self._table.currentRow()
        if cur not in rows:
            target = rows[0] if direction >= 0 else rows[-1]
        else:
            idx = rows.index(cur)
            target = rows[(idx + (1 if direction >= 0 else -1)) % len(rows)]
        self._table.selectRow(target)
        self._table.setCurrentCell(target, COL_DESC)
        self._table.scrollToItem(
            self._table.item(target, COL_DESC) or self._table.item(target, COL_SNO),
            QAbstractItemView.PositionAtCenter,
        )

    def toolbar_new_row(self) -> None:
        """Insert a blank editable row (same as Insert Row Below)."""
        self._insert_below()
        row = self._table.currentRow()
        if row < 0:
            row = self._saved_count
        self._table.selectRow(row)
        self._table.setCurrentCell(row, COL_DESC)
        item = self._table.item(row, COL_DESC)
        if item is not None:
            self._table.editItem(item)

    def toolbar_delete(self) -> None:
        """Delete the current saved entry or clear selected unsaved rows."""
        row = self._table.currentRow()
        if row < 0:
            return
        if row < self._saved_count:
            self._delete_saved_row(row)
            return
        # Unsaved editable selection
        self._delete_rows()

    def toolbar_copy_row(self) -> None:
        """Duplicate the current row into a new unsaved editable row."""
        row = self._table.currentRow()
        if row < 0 or not (row < self._saved_count or self._row_has_data(row)):
            QMessageBox.information(
                self, "Create a Copy",
                "Select an entry to copy first.",
            )
            return
        values: dict = {}
        for col in range(self._table.columnCount()):
            if col in (COL_SNO, COL_CASHIER):
                continue
            it = self._table.item(row, col)
            if col == COL_RECEIPT and it is not None:
                text = it.text().strip()
                values[col] = text or (it.data(Qt.UserRole) or "pending")
            else:
                values[col] = it.text() if it else ""
        insert_at = max(self._saved_count, row + 1)
        self._shift_row_maps_on_insert(insert_at)
        self._table.insertRow(insert_at)
        self._init_editable_rows(insert_at, insert_at + 1)
        self._write_row_values(insert_at, values)
        self._renumber()
        self._table.selectRow(insert_at)
        self._table.setCurrentCell(insert_at, COL_DESC)

    def toolbar_print(self) -> None:
        self.export_as("pdf")

    def toolbar_attach(self) -> None:
        """Open attachment manager for the selected saved transaction."""
        row = self._table.currentRow()
        if row < 0 or row >= self._saved_count:
            QMessageBox.information(
                self, "Attach File",
                "Save the entry first, then select it to attach a file.",
            )
            return
        tx_id = self._saved_ids.get(row)
        if not tx_id:
            QMessageBox.information(
                self, "Attach File",
                "Save the entry first, then select it to attach a file.",
            )
            return
        tx = self._saved_txs.get(row)
        desc = ""
        if tx is not None:
            desc = getattr(tx, "description", "") or ""
        else:
            it = self._table.item(row, COL_DESC)
            desc = it.text() if it else ""
        from tahmeed.ui.dialogs.attachment_dialog import AttachmentDialog
        dlg = AttachmentDialog(
            tx_id,
            description=desc,
            actor_id=getattr(self._user, "_id", None),
            parent=self,
        )
        dlg.exec()
        asyncio.ensure_future(self._refresh_attachment_meta(row, tx_id))

    async def _refresh_attachment_meta(self, row: int, tx_id) -> None:
        try:
            from tahmeed.services.attachment_service import get_attachments
            atts = await get_attachments(tx_id)
            tx = self._saved_txs.get(row)
            if tx is not None:
                tx.attachments = atts
            self.attachment_count_changed.emit(len(atts))
        except Exception:
            self.attachment_count_changed.emit(0)

    def selected_attachment_count(self) -> int:
        row = self._table.currentRow()
        if row < 0 or row >= self._saved_count:
            return 0
        tx = self._saved_txs.get(row)
        if tx is None:
            return 0
        return len(getattr(tx, "attachments", None) or [])

    def _emit_attachment_badge(self) -> None:
        self.attachment_count_changed.emit(self.selected_attachment_count())

    def has_unsaved_work(self) -> bool:
        """True when edit-mode dirty rows or typed-but-unsaved new rows exist."""
        if self._dirty_rows:
            return True
        for row in range(self._saved_count, self._table.rowCount()):
            if self._row_has_data(row):
                return True
        return False

    # ------------------------------------------------------------------
    # Local draft autosave (crash / power-loss recovery)
    # ------------------------------------------------------------------

    def _schedule_draft_autosave(self) -> None:
        if self._restoring_draft or self._save_in_flight or self._bulk_mutating:
            return
        self._draft_timer.start()

    def _flush_local_draft(self, *, include_dirty: bool = True) -> None:
        """Persist current unsaved grid state to disk (or clear if empty)."""
        if self._restoring_draft:
            return
        try:
            self._commit_open_editor()
            payload = self._capture_local_draft(include_dirty=include_dirty)
            save_register_draft(payload)
        except Exception:
            # Local draft must never break typing / save.
            pass

    def _clear_local_draft(self) -> None:
        self._draft_timer.stop()
        try:
            clear_register_draft(
                self._user._id, self._current_date, merged=self._merged_mode
            )
        except Exception:
            pass

    def _capture_local_draft(self, *, include_dirty: bool = True) -> dict:
        dirty_saved: list = []
        if include_dirty:
            for row in sorted(self._dirty_rows):
                tx_id = self._saved_ids.get(row)
                if tx_id is None:
                    continue
                tx = self._saved_txs.get(row)
                dirty_saved.append({
                    "tx_id": str(tx_id),
                    "cashier_id": str(tx.cashier_id) if tx and tx.cashier_id else None,
                    "cells": cells_for_json(self._row_value_map(row)),
                })

        new_rows: list = []
        for row in range(self._saved_count, self._table.rowCount()):
            if not self._row_has_data(row):
                continue
            pending = self._pending_row_meta.get(row)
            new_rows.append({
                "cells": cells_for_json(self._row_value_map(row)),
                "pending_meta": serialize_pending_meta(pending),
            })

        return build_draft_payload(
            user_id=self._user._id,
            username=getattr(self._user, "username", "") or "",
            register_date=self._current_date,
            merged=self._merged_mode,
            edit_mode=self._edit_mode and include_dirty and bool(dirty_saved),
            dirty_saved=dirty_saved,
            new_rows=new_rows,
        )

    def _restore_local_draft(self) -> Optional[tuple]:
        """Apply a saved draft onto the freshly populated grid.

        Returns ``(dirty_count, new_count)`` when anything was restored, else None.
        """
        draft = load_register_draft(
            self._user._id, self._current_date, merged=self._merged_mode
        )
        if draft is None or draft_is_empty(draft):
            return None

        dirty_entries = list(draft.get("dirty_saved") or [])
        new_entries = list(draft.get("new_rows") or [])
        if not dirty_entries and not new_entries:
            return None

        self._restoring_draft = True
        self._bulk_mutating = True
        dirty_applied = 0
        new_applied = 0
        truck_cells: list = []
        prev = self._table.blockSignals(True)
        try:
            id_to_row = {str(tx_id): row for row, tx_id in self._saved_ids.items()}
            need_edit = bool(dirty_entries) and bool(draft.get("edit_mode", True))
            if need_edit and not self._edit_mode:
                # Unlock saved rows without clearing dirty set prematurely.
                self._edit_mode = True
                editable = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
                for row in range(self._saved_count):
                    for col in range(self._table.columnCount()):
                        it = self._table.item(row, col)
                        if it is None:
                            continue
                        if col not in READONLY_COLS:
                            it.setFlags(editable)
                        it.setBackground(QBrush(EDIT_BG))

            for entry in dirty_entries:
                tx_id = str(entry.get("tx_id") or "")
                row = id_to_row.get(tx_id)
                if row is None:
                    continue
                values = cells_from_json(entry.get("cells"))
                truck_cells.extend(self._write_row_values(row, values))
                self._dirty_rows.add(row)
                for col in range(self._table.columnCount()):
                    it = self._table.item(row, col)
                    if it is not None:
                        it.setBackground(QBrush(DIRTY_BG))
                dirty_applied += 1

            if new_entries:
                start = self._first_empty_editable_row()
                needed = start + len(new_entries) - self._table.rowCount()
                if needed > 0:
                    self._append_editable_rows(needed + 5)
                for offset, entry in enumerate(new_entries):
                    row = start + offset
                    if row < self._saved_count:
                        continue
                    values = cells_from_json(entry.get("cells"))
                    truck_cells.extend(self._write_row_values(row, values))
                    meta = hydrate_pending_meta(entry.get("pending_meta"))
                    if meta:
                        self._pending_row_meta[row] = meta
                    self._activate_row(row)
                    self._sync_row_date(row)
                    new_applied += 1
        finally:
            self._table.blockSignals(prev)
            self._bulk_mutating = False
            self._restoring_draft = False

        self._renumber()
        self._finalize_truck_cells(truck_cells)
        self._update_footer()
        if dirty_applied:
            self.edit_state_changed.emit(True, len(self._dirty_rows))
        elif self._edit_mode:
            self.edit_state_changed.emit(True, 0)

        if dirty_applied or new_applied:
            return dirty_applied, new_applied
        return None

    def _show_draft_restored_notice(self, dirty_count: int, new_count: int) -> None:
        parts = []
        if dirty_count:
            parts.append(
                f"{dirty_count} edited row{'s' if dirty_count != 1 else ''}"
            )
        if new_count:
            parts.append(
                f"{new_count} new entr{'ies' if new_count != 1 else 'y'}"
            )
        detail = " and ".join(parts) if parts else "unsaved work"
        QMessageBox.information(
            self,
            "Draft restored",
            f"Recovered {detail} from before the app closed.\n\n"
            "Click Save to store them on the server.",
        )

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
            self._clear_local_draft()
            return True
        self._flush_local_draft()
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
            self._clear_local_draft()
            return True
        # Yes — save; if the user cancels mid-save (duplicates / off-date), stay.
        return await self._do_save()

    async def _do_save(self) -> bool:
        """Persist dirty + new rows. Returns False if the user cancelled mid-save."""
        if self._save_in_flight:
            return False
        self._save_in_flight = True
        # Submit already holds the busy UI lock; avoid flickering it off early.
        nested_under_submit = self._submit_in_flight
        if not nested_under_submit:
            self.save_busy_changed.emit(True)
        try:
            return await self._do_save_body()
        finally:
            self._save_in_flight = False
            if not nested_under_submit and not self._submit_in_flight:
                self.save_busy_changed.emit(False)

    async def _do_save_body(self) -> bool:
        """Inner save implementation (caller holds `_save_in_flight`)."""
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
                if orig is not None and getattr(orig, "original_transaction_id", None):
                    # Already a pending-edit clone — refresh it in place.
                    updates["edited_after_verification"] = True
                    await update_transaction(tx_id, updates)
                elif orig is not None and orig.verified:
                    # Leave the original in Master Expenses intact; insert a
                    # pending-edit document that the accountant reviews in the
                    # Edited tab. On re-approval the new values cascade to the
                    # original in-place.
                    await insert_pending_edit(tx_id, updates, self._user._id)
                elif orig is not None and (getattr(orig, "register_status", "") or "") == "draft":
                    # Still in Merged draft — update in place; do not send to Edited yet.
                    await update_transaction(tx_id, updates)
                elif orig is not None:
                    # Option B: any edit of a saved (unverified / rejected) row
                    # moves it to Verify → Edited for accountant re-approval.
                    updates["edited_after_verification"] = True
                    if getattr(orig, "rejected", False):
                        updates["rejected"] = False
                        updates["rejection_reason"] = None
                        updates["discarded"] = False
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
            _parsed = _parse_optional_date(_ds, default_year=self._current_date.year)
            if _parsed is not None:
                _td = _parsed.date()
            else:
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
        append_order = None
        if not self._merged_mode:
            try:
                append_order = await next_day_order(self._current_date)
            except Exception:
                append_order = None
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
                tx_date = _parse_optional_date(
                    date_str, default_year=self._current_date.year
                )
                if tx_date is None:
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
                    item_name = cat.name.upper()
                elif item_name and self._restrict_items:
                    errors.append(f'Row {row + 1}: "{item_name}" is not a known item.')
                    continue
                elif item_name:
                    item_name = item_name.upper()

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
                        description = match.upper()
                    else:
                        description = description.upper()
                else:
                    description = description.upper()

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
                                    f'Row {row + 1}: "{norm.value}" is not a registered fleet vehicle.'
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

                ref_text = txt(COL_REF).upper()
                meta = self._pending_row_meta.get(row) or {}
                if self._merged_mode:
                    order = row
                elif append_order is not None:
                    order = append_order
                    append_order += 1
                else:
                    order = row
                tx = Transaction(
                    date=tx_date,
                    description=description,
                    item=item_name,
                    # The chosen item *is* the category — keep them in sync so the
                    # item's sidebar tab (which filters on category_name) shows it.
                    category_name=item_name or None,
                    category_id=meta.get("category_id"),
                    truck_number=truck_number,
                    amount=amount,
                    currency=meta.get("currency") or "TZS",
                    memo=txt(COL_MEMO).upper(),
                    receipt_status=rcpt_status,
                    ref_float=ref_text,
                    notes_flag=_is_refund_float(ref_text),
                    ownership=txt(COL_OWN).upper(),
                    approver=txt(COL_APR).upper(),
                    payee=txt(COL_PAYEE).upper(),
                    cheque=txt(COL_CHEQUE).upper(),
                    cashier_id=self._user._id,
                    day_order=order,
                    register_status="draft",
                    possible_duplicate=is_dup,
                    daily_import_id=meta.get("daily_import_id"),
                    daily_import_source=meta.get("daily_import_source"),
                    date_discrepancy=bool(meta.get("date_discrepancy")),
                    import_primary_date=meta.get("import_primary_date"),
                    lpo_do=(meta.get("lpo_do") or "").upper(),
                    do_number=(meta.get("do_number") or "").upper(),
                )
                await save_transaction(tx)
                saved += 1
                self._pending_row_meta.pop(row, None)
            except Exception as exc:
                errors.append(f"Row {row + 1}: {exc}")

        if cancel_all:
            self._flush_local_draft()
            return False

        if self._merged_mode and (saved or updated):
            try:
                await self._persist_visual_day_order()
            except Exception as exc:
                errors.append(f"Could not save row order: {exc}")

        if errors:
            QMessageBox.warning(
                self, "Save — partial errors",
                f"{saved} added, {updated} updated.\n\nErrors:\n" + "\n".join(errors),
            )
            # Keep the grid as-is and refresh the local draft so a crash still
            # recovers remaining unsaved / failed rows.
            self._flush_local_draft()
            return True
        elif saved == 0 and updated == 0:
            QMessageBox.information(self, "Nothing to save", "No changes to save.")
            self._clear_local_draft()
            return True
        # else: clean save — reload silently, no popup

        self._clear_local_draft()
        self._reset_edit_state()
        self.rows_saved.emit(saved)
        await self._load_date(self._current_date)
        return True

    def _ordered_saved_ids(self) -> list:
        """Transaction ids in current on-screen order (saved prefix)."""
        return [
            self._saved_ids[r]
            for r in range(self._saved_count)
            if self._saved_ids.get(r)
        ]

    async def _persist_visual_day_order(self) -> None:
        """Write Merged-table sequence to ``day_order`` before a reload can scramble it."""
        if not self._merged_mode:
            return
        ordered_ids = self._ordered_saved_ids()
        if ordered_ids:
            await recount_day_order(self._current_date, ordered_ids)

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

    async def _load_categories(self) -> None:
        """Ensure the Item column has the live Manage Items catalog.

        Dashboard also pushes categories, but the register reloads on its own so
        autocomplete / restrict still work if that push failed or is stale.
        """
        try:
            cats = await get_all_categories()
        except Exception:
            return
        if cats:
            self.update_categories(cats)
            self._revalidate_visible_item_cells()

    def _revalidate_visible_item_cells(self) -> None:
        """Re-run Item validation after the catalog loads (clears false flags)."""
        for row in range(self._table.rowCount()):
            it = self._table.item(row, COL_ITEM)
            if it is None or not it.text().strip():
                continue
            if row < self._saved_count and not self._edit_mode:
                continue
            self._validate_item_cell(row, it)

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
        """Pre-fill Item from a saved description map or prior entries."""
        if not description.strip():
            return
        item_it = self._table.item(row, COL_ITEM)
        if item_it and item_it.text().strip():
            return
        from tahmeed.services.cashier_service import resolve_item_name_for_description

        try:
            cat_name = await resolve_item_name_for_description(description)
        except Exception:
            return
        if not cat_name:
            return
        prev = self._table.blockSignals(True)
        if item_it is None:
            item_it = QTableWidgetItem(cat_name)
            item_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._table.setItem(row, COL_ITEM, item_it)
        else:
            item_it.setText(cat_name)
        self._table.blockSignals(prev)
        self._validate_item_cell(row, item_it)

    async def _load_fleet_numbers(self) -> None:
        from tahmeed.services.truck_service import get_fleet_kinds, get_fleet_numbers
        try:
            self._fleet_numbers = await get_fleet_numbers()
        except Exception:
            self._fleet_numbers = set()
        try:
            self._fleet_kinds = await get_fleet_kinds()
        except Exception:
            self._fleet_kinds = {}
        try:
            raw = await get_setting("allowed_truck_labels")
            if isinstance(raw, list) and raw:
                self._allowed_truck_labels = merge_allowed_labels(raw, DEFAULT_PLACE_LABELS)
            else:
                self._allowed_truck_labels = set(DEFAULT_PLACE_LABELS)
        except Exception:
            self._allowed_truck_labels = set(DEFAULT_PLACE_LABELS)

    async def _load_description_cache(self) -> None:
        """Warm system-wide description history for Excel-style autocomplete."""
        from tahmeed.services.cashier_service import ensure_description_cache
        try:
            await ensure_description_cache()
        except Exception:
            pass

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
    # Item-column validation (canonicalise / restrict / flag unknown)
    # ------------------------------------------------------------------

    def _item_row_background(self, row: int) -> QBrush:
        if row < self._saved_count:
            if row in self._dirty_rows:
                return QBrush(DIRTY_BG)
            if self._edit_mode:
                return QBrush(EDIT_BG)
            return QBrush(SAVED_BG)
        return QBrush(NEW_BG)

    def _flag_unknown_item(self, item: QTableWidgetItem, text: str) -> None:
        """Mark an unknown Item cell (restrict on) — keep text, no add dialog."""
        item.setForeground(QBrush(NEG_COLOR))
        item.setToolTip(
            f'"{text}" is not a known item. Pick an existing item from the list, '
            "or ask the accountant to add it in Manage Items."
        )

    def _clear_item_flag(self, row: int, item: QTableWidgetItem) -> None:
        tip = item.toolTip() or ""
        if "is not a known item" not in tip:
            return
        item.setToolTip("")
        item.setForeground(QBrush())
        # Restore row background in case an older build painted DUP_BG.
        if item.background().color() == DUP_BG:
            item.setBackground(self._item_row_background(row))

    def _validate_item_cell(self, row: int, item: QTableWidgetItem) -> None:
        text = item.text().strip()
        if not text:
            self._clear_item_flag(row, item)
            return
        cat = self._cat_by_name.get(text.lower())
        if cat is not None:
            # Known item — snap to uppercase (table-view convention).
            canonical = cat.name.upper()
            if item.text() != canonical:
                self._table.blockSignals(True)
                item.setText(canonical)
                self._table.blockSignals(False)
            self._clear_item_flag(row, item)
            return
        if not self._restrict_items:
            self._clear_item_flag(row, item)
            return
        # Do not flag every cell when the catalog failed to load — that paints
        # known names red and makes Restrict look broken.
        if not self._categories:
            return
        # Unknown item with restriction on — keep the typed text and flag the cell.
        # Do not prompt to add; save still rejects unknown items.
        self._flag_unknown_item(item, text)
    # ------------------------------------------------------------------
    # Truck-column validation (format + restrict to fleet registry)
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
            fleet_kinds=getattr(self, "_fleet_kinds", None) or {},
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
        from tahmeed.services.truck_service import add_fleet_by_collection

        for kind, number in adds:
            try:
                label = await add_fleet_by_collection(kind, number)
                self._fleet_kinds[number] = label
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
