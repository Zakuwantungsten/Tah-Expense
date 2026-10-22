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
import time
from datetime import datetime, date
from typing import List, Optional

from bson import ObjectId

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QToolButton, QLabel,
    QTableWidget, QTableWidgetItem, QApplication,
    QAbstractItemDelegate, QStyledItemDelegate, QStyleOptionViewItem, QMenu, QFileDialog,
    QMessageBox, QAbstractItemView, QHeaderView, QDateEdit, QLineEdit,
    QStyle, QComboBox, QDialog, QFrame, QListWidget, QListWidgetItem, QPushButton,
    QTableWidgetSelectionRange, QStackedWidget,
)
from PySide6.QtCore import (
    Qt, Signal, QDate, QEvent, QRect, QSize, QObject, QTimer, QEventLoop,
    QItemSelection, QItemSelectionModel, QSettings,
)
from PySide6.QtGui import (
    QAction, QKeyEvent, QColor, QBrush, QFont, QFontMetrics, QPen, QPainter,
    QMouseEvent, QCursor,
)

from tahmeed.models.category import Category
from tahmeed.models.transaction import Transaction, pack_money
from tahmeed.models.user import User
from tahmeed.services.truck_service import get_fleet_numbers
from tahmeed.services.truck_format import (
    normalize_truck_number, try_match_fleet, normalize_place_label,
    is_allowed_place_label, DEFAULT_PLACE_LABELS, merge_allowed_labels,
)
from tahmeed.services.cashier_service import (
    BULK_CREATE_CHUNK,
    get_transactions_by_date, save_transaction, request_or_delete_transaction,
    update_transaction, bulk_update_transactions, insert_pending_edit,
    check_for_duplicates_bulk, bulk_save_transactions,
    submit_day_for_verify, recount_day_order,
    next_day_order,
)
from tahmeed.services.category_service import item_key
from tahmeed.services.subtable_service import get_subtables
from tahmeed.services.settings_service import get_setting, set_setting
from tahmeed.services.register_draft_service import (
    SYNC_FAILED,
    SYNC_IN_FLIGHT,
    SYNC_PENDING,
    build_draft_payload,
    cells_for_json,
    cells_from_json,
    claim_fingerprint,
    clear_register_draft,
    draft_is_empty,
    draft_parked_rows,
    entry_client_row_id,
    entry_is_parked,
    expense_content_fingerprint,
    fingerprint_is_meaningful,
    hydrate_pending_meta,
    load_register_draft,
    new_client_row_id,
    normalize_sync_status,
    save_register_draft,
    serialize_pending_meta,
)
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit
from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit, accept_completion
from tahmeed.ui.widgets.excel_column_filter import chevron_hit_contains
from tahmeed.ui.dialogs.truck_correction_dialog import TruckCorrectionDialog, TruckIssue
from tahmeed.ui.dialogs.duplicate_review_dialog import DuplicateReviewDialog
from tahmeed.services.duplicate_review import (
    DuplicateReviewItem,
    format_amount_label,
    format_new_date,
)
from tahmeed.services.register_metrics import log_register_event, timed_op

# ---------------------------------------------------------------------------
# Column indices / colors / delegates (shared with RejectedView)
# ---------------------------------------------------------------------------
from tahmeed.ui.cashier.register_delegates import (  # noqa: E402
    COL_SNO, COL_DATE, COL_ITEM, COL_DESC, COL_TRUCK, COL_MEMO,
    COL_REF, COL_TZS, COL_USD, COL_RECEIPT, COL_OWN, COL_APR, COL_PAYEE, COL_CHEQUE,
    COL_CASHIER,
    HEADERS, CHECK_COLS, READONLY_COLS, _DATA_SKIP_COLS, _UPPER_SKIP_COLS,
    DEFAULT_EDITABLE_ROWS, _REF_FLOAT_OPTS, _COL_PREFERRED, _COL_FLEX, _COL_MIN,
    _is_refund_float, _ref_float_text, _parse_optional_date, format_register_date,
    SAVED_BG, NEW_BG, EMPTY_BG, NEG_COLOR, EDIT_BG, DIRTY_BG, DRAFT_BG, DUP_BG, MISMATCH_BG,
    TRUCK_REQUIRED_BG, PENDING_SYNC_BG, FAILED_SYNC_BG,
    _FOOTER_BTN_STYLE,
    _accept_editor_completion, _upper_text,
    _ExcelCellDelegate, _DescriptionDelegate, _TruckDelegate, _DateDelegate,
    _RefFloatDelegate, _norm_receipt_text, _receipt_paste_value, _parse_amount_text,
    _parse_optional_amount_text, _ReceiptDelegate,
    _ItemDelegate, _CurrencyLineEdit, _TZSDelegate,
    _RCPT_COLORS, _RCPT_LABEL, _RECEIPT_OPTS, _RCPT_OPT_KEY, _RCPT_NORM, _VALID_RCPT,
    _CELL_H_INSET,
)

from tahmeed.ui.cashier.excel_cursors import excel_hover_cursor, excel_drag_cursor
from tahmeed.ui.cashier.excel_row_header import (
    DEFAULT_ROW_HEIGHT,
    ExcelRowHeaderView,
    MIN_ROW_HEIGHT,
    sync_row_header_labels,
)

_TABLE_FONT_PT_DEFAULT = 11
_TABLE_FONT_PT_MIN = 8
_TABLE_FONT_PT_MAX = 18
_TABLE_FONT_SETTINGS_ORG = "Tahmeed"
_TABLE_FONT_SETTINGS_APP = "tahmeed-expense"
_TABLE_FONT_SETTINGS_KEY = "daily_register/table_font_pt"
# Soft cap so a single long wrapped cell cannot balloon the grid.
_WRAP_ROW_HEIGHT_MAX = 240


def _load_table_font_pt() -> int:
    raw = QSettings(_TABLE_FONT_SETTINGS_ORG, _TABLE_FONT_SETTINGS_APP).value(
        _TABLE_FONT_SETTINGS_KEY, _TABLE_FONT_PT_DEFAULT
    )
    try:
        pt = int(raw)
    except (TypeError, ValueError):
        return _TABLE_FONT_PT_DEFAULT
    return max(_TABLE_FONT_PT_MIN, min(_TABLE_FONT_PT_MAX, pt))


def _save_table_font_pt(pt: int) -> None:
    QSettings(_TABLE_FONT_SETTINGS_ORG, _TABLE_FONT_SETTINGS_APP).setValue(
        _TABLE_FONT_SETTINGS_KEY, int(pt)
    )


def _amount_item_from_raw(raw: str) -> QTableWidgetItem:
    amt = _parse_amount_text(raw)
    text = f"{amt:,.2f}" if (raw or "").strip() else ""
    it = QTableWidgetItem(text)
    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return it


def _display_money_cells(tx: Transaction) -> tuple[str, str]:
    tzs, usd = tx.money_parts()
    tzs_txt = f"{tzs:,.2f}" if tzs else ""
    usd_txt = f"{usd:,.2f}" if usd else ""
    return tzs_txt, usd_txt


def _money_item(text: str) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return it



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
# Table with Excel-like row gutter + fill handle
# ---------------------------------------------------------------------------

_ROWS_CLIP_PREFIX = "TAHMEED_ROWS_V1\n"
_FILL_HANDLE_SIZE = 7
_FILL_HANDLE_HIT = 14   # corner target for hover / click / drag start
# Excel autofill preview — green tint + ghost values (distinct from normal selection)
_FILL_RANGE_BORDER = QPen(QColor("#217346"), 2, Qt.DashLine)


class _ExcelTableViewport(QWidget):
    """Table viewport that paints the Excel fill handle and drag preview."""

    def __init__(self, table: "_ExcelTableWidget") -> None:
        super().__init__(table)
        self._table = table

    def mouseMoveEvent(self, event) -> None:
        table = self._table
        if table._fill_dragging and (event.buttons() & Qt.LeftButton):
            table._update_fill_drag_end(table._viewport_pos(event))
            # Viewport only — never table.setCursor (that leaks onto the header).
            table.viewport().setCursor(table._drag_cursor)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        table = self._table
        if table._fill_dragging and event.button() == Qt.LeftButton:
            pos = table._viewport_pos(event)
            table._update_fill_drag_end(pos)
            table._finish_fill_drag(pos)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            self._table._paint_excel_overlay(painter)
        finally:
            painter.end()


class _ExcelTableWidget(QTableWidget):
    """Excel-like grid: row gutter, cell cross cursor, fill handle + drag fill."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._grid_owner = None
        self._fill_dragging = False
        self._fill_anchor: tuple[int, int, int, int] | None = None
        self._fill_end: tuple[int, int] | None = None
        self._cursor_zone = "arrow"
        self.setViewport(_ExcelTableViewport(self))
        self.viewport().setMouseTracking(True)
        self.setMouseTracking(True)
        self._hover_cursor = excel_hover_cursor()
        self._drag_cursor = excel_drag_cursor()

    def leaveEvent(self, event) -> None:
        self.viewport().unsetCursor()
        self._cursor_zone = "arrow"
        super().leaveEvent(event)

    def _owner(self):
        return getattr(self, "_grid_owner", None)

    def _selection_bounds(self) -> tuple[int, int, int, int] | None:
        indexes = self.selectedIndexes()
        if not indexes:
            cur = self.currentIndex()
            if not cur.isValid():
                return None
            r, c = cur.row(), cur.column()
            return r, c, r, c
        rows = [i.row() for i in indexes]
        cols = [i.column() for i in indexes]
        return min(rows), min(cols), max(rows), max(cols)

    def _selection_viewport_rect(self) -> QRect | None:
        bounds = self._selection_bounds()
        if bounds is None:
            return None
        r0, c0, r1, c1 = bounds
        tl = self.visualRect(self.model().index(r0, c0))
        br = self.visualRect(self.model().index(r1, c1))
        if not tl.isValid() or not br.isValid():
            return None
        return tl.united(br)

    def _fill_handle_rect(self) -> QRect | None:
        owner = self._owner()
        if owner is None or not self._fill_handle_enabled():
            return None
        sel = self._selection_viewport_rect()
        if sel is None or sel.width() <= 0 or sel.height() <= 0:
            return None
        size = _FILL_HANDLE_SIZE
        return QRect(
            sel.right() - size + 2,
            sel.bottom() - size + 2,
            size,
            size,
        )

    def _fill_handle_enabled(self) -> bool:
        owner = self._owner()
        bounds = self._selection_bounds()
        if owner is None or bounds is None:
            return False
        r0, c0, r1, c1 = bounds
        for row in range(r0, r1 + 1):
            for col in range(c0, c1 + 1):
                if col in READONLY_COLS or col == COL_SNO:
                    continue
                if owner._cell_editable(row, col):
                    return True
        return False

    def _viewport_pos(self, pos) -> QPoint:
        if hasattr(pos, "position"):
            return pos.position().toPoint()
        return pos

    def _cell_at_viewport_pos(self, pos) -> tuple[int, int] | None:
        """Row/col under *pos* (viewport coords), even for empty cells."""
        pt = self._viewport_pos(pos)
        row = self.rowAt(pt.y())
        col = self.columnAt(pt.x())
        if col < 0 and self.columnCount():
            col = self.columnAt(max(0, min(pt.x(), self.viewport().width() - 1)))
        if row < 0 and self.rowCount():
            if pt.y() >= self.viewport().height() - 2:
                row = self.rowCount() - 1
            elif pt.y() <= 0:
                row = 0
        if row < 0 or col < 0:
            return None
        return row, col

    def _fill_handle_hit_rect(self) -> QRect | None:
        """Bottom-right corner hit zone — cursor + click (wider than the painted square)."""
        sel = self._selection_viewport_rect()
        if sel is None or not self._fill_handle_enabled():
            return None
        hit = _FILL_HANDLE_HIT
        return QRect(
            sel.right() - hit + 1,
            sel.bottom() - hit + 1,
            hit,
            hit,
        )

    def _pos_in_fill_handle(self, pos) -> bool:
        hit = self._fill_handle_hit_rect()
        return hit is not None and hit.contains(self._viewport_pos(pos))

    def _wants_fill_drag(self, pos) -> bool:
        return self._pos_in_fill_handle(pos) or self._cursor_zone == "fill"

    def _begin_fill_drag(self, pos) -> bool:
        if not self._wants_fill_drag(pos) or not self._fill_handle_enabled():
            return False
        bounds = self._selection_bounds()
        if bounds is None:
            return False
        r0, c0, r1, c1 = bounds
        self._fill_dragging = True
        self._fill_anchor = bounds
        self._fill_end = (r1, c1)
        self._cursor_zone = "drag"
        self.viewport().setCursor(self._drag_cursor)
        self.viewport().grabMouse()
        self._refresh_fill_drag_visual()
        return True

    def _is_fill_extension_cell(self, row: int, col: int) -> bool:
        if not self._fill_dragging or self._fill_anchor is None or self._fill_end is None:
            return False
        r0, c0, r1, c1 = self._fill_anchor
        er, ec = self._fill_end
        er = max(r1, er)
        ec = max(c1, ec)
        if er <= r1 and ec <= c1:
            return False
        if row < r0 or row > er or col < c0 or col > ec:
            return False
        return not (r0 <= row <= r1 and c0 <= col <= c1)

    def _update_fill_drag_end(self, pos) -> None:
        if self._fill_anchor is None:
            return
        cell = self._cell_at_viewport_pos(pos)
        if cell is None:
            return
        _r0, _c0, r1, c1 = self._fill_anchor
        row, col = cell
        self._fill_end = (max(row, r1), max(col, c1))
        self._restore_fill_source_selection()
        self._auto_scroll_for_fill(self._viewport_pos(pos))
        self._refresh_fill_drag_visual()

    def _refresh_fill_drag_visual(self) -> None:
        """Force an immediate paint pass so fill preview shows while dragging."""
        self.viewport().repaint()

    def _fill_drag_target_bounds(self) -> tuple[int, int, int, int] | None:
        if not self._fill_dragging or self._fill_anchor is None or self._fill_end is None:
            return None
        r0, c0, r1, c1 = self._fill_anchor
        er, ec = self._fill_end
        er = max(r1, er)
        ec = max(c1, ec)
        return r0, c0, er, ec

    def _restore_fill_source_selection(self) -> None:
        """Keep the original source block highlighted while previewing the extension."""
        if self._fill_anchor is None:
            return
        r0, c0, r1, c1 = self._fill_anchor
        sel = QItemSelection(
            self.model().index(r0, c0),
            self.model().index(r1, c1),
        )
        self.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)

    def _select_fill_result_range(self, anchor, end) -> None:
        r0, c0, r1, c1 = anchor
        er, ec = end
        er = max(r1, er)
        ec = max(c1, ec)
        sel = QItemSelection(
            self.model().index(r0, c0),
            self.model().index(er, ec),
        )
        self.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)

    def _fill_preview_source_coords(
        self, row: int, col: int,
    ) -> tuple[int, int] | None:
        if self._fill_anchor is None:
            return None
        r0, c0, r1, c1 = self._fill_anchor
        sel_h = r1 - r0 + 1
        sel_w = c1 - c0 + 1
        return r0 + (row - r0) % sel_h, c0 + (col - c0) % sel_w

    def _fill_preview_display_text(self, row: int, col: int) -> str:
        owner = self._owner()
        if owner is not None and (
            col in READONLY_COLS
            or col == COL_SNO
            or not owner._cell_editable(row, col)
        ):
            return ""
        src = self._fill_preview_source_coords(row, col)
        if src is None:
            return ""
        src_row, src_col = src
        item = self.item(src_row, src_col)
        if item is None:
            return ""
        if src_col in CHECK_COLS:
            return "✓" if item.data(Qt.UserRole) else ""
        return item.text()

    def _fill_preview_text_align(self, col: int) -> int:
        if col in (COL_TZS, COL_USD, COL_REF):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return int(Qt.AlignLeft | Qt.AlignVCenter)

    def _auto_scroll_for_fill(self, pos) -> None:
        vp = self.viewport()
        margin = 20
        bounds = self._fill_drag_target_bounds()
        if bounds is None:
            return
        _r0, _c0, er, ec = bounds
        if pos.y() >= vp.height() - margin and er < self.rowCount() - 1:
            self.scrollTo(
                self.model().index(er + 1, ec),
                QAbstractItemView.EnsureVisible,
            )
        elif pos.y() <= margin and er > self._fill_anchor[0]:
            self.scrollTo(
                self.model().index(max(self._fill_anchor[0], er - 1), ec),
                QAbstractItemView.EnsureVisible,
            )

    def _finish_fill_drag(self, pos) -> None:
        owner = self._owner()
        anchor = self._fill_anchor
        end = self._fill_end
        if owner is not None and anchor is not None and end is not None:
            owner._apply_fill_drag(anchor, end)
            self._select_fill_result_range(anchor, end)
        self._fill_dragging = False
        self._fill_anchor = None
        self._fill_end = None
        self.viewport().releaseMouse()
        self.viewport().update()
        self._update_hover_cursor(pos)

    def _update_hover_cursor(self, pos, buttons=Qt.NoButton) -> None:
        pos = self._viewport_pos(pos)
        if self._fill_dragging:
            zone = "drag"
        elif buttons & Qt.LeftButton:
            zone = "drag"
        elif self._pos_in_fill_handle(pos):
            zone = "fill"
        else:
            index = self.indexAt(pos)
            if index.isValid():
                zone = "hover"
            else:
                zone = "arrow"
        if zone == self._cursor_zone:
            return
        old_zone = self._cursor_zone
        self._cursor_zone = zone
        # Apply Excel plus/fill cursors on the viewport only so the header
        # keeps Qt's arrow / SizeHorCursor for column resize.
        if zone == "hover":
            self.viewport().setCursor(self._hover_cursor)
        elif zone in ("fill", "drag"):
            self.viewport().setCursor(self._drag_cursor)
        else:
            self.viewport().unsetCursor()
        if old_zone == "fill" or zone == "fill":
            self.viewport().update()

    def _paint_excel_overlay(self, painter: QPainter) -> None:
        handle = self._fill_handle_rect()
        show_handle = (
            handle is not None
            and not self._fill_dragging
            and self._cursor_zone == "fill"
        )
        if show_handle:
            painter.save()
            painter.setPen(QPen(QColor("#111827"), 1))
            painter.setBrush(QColor("#ffffff"))
            painter.drawRect(handle.adjusted(0, 0, -1, -1))
            painter.restore()

        if self._fill_dragging:
            self._paint_fill_drag_preview(painter)

    def _paint_fill_drag_preview(self, painter: QPainter) -> None:
        """Dashed outline around the full autofill range (cells painted by delegate)."""
        if self._fill_anchor is None or self._fill_end is None:
            return
        r0, c0, r1, c1 = self._fill_anchor
        er, ec = self._fill_end
        er = max(r1, er)
        ec = max(c1, ec)
        if er <= r1 and ec <= c1:
            return

        tl = self.visualRect(self.model().index(r0, c0))
        br = self.visualRect(self.model().index(er, ec))
        if tl.isNull() or br.isNull():
            return
        full = tl.united(br)

        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(_FILL_RANGE_BORDER)
        painter.drawRect(full.adjusted(0, 0, -1, -1))
        painter.restore()

    def _fill_preview_rect(self) -> QRect | None:
        bounds = self._fill_drag_target_bounds()
        if bounds is None:
            return None
        r0, c0, er, ec = bounds
        if er <= self._fill_anchor[2] and ec <= self._fill_anchor[3]:
            return None
        tl = self.visualRect(self.model().index(r0, c0))
        br = self.visualRect(self.model().index(er, ec))
        if tl.isNull() or br.isNull():
            return None
        return tl.united(br)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = self._viewport_pos(event)
        if event.button() == Qt.LeftButton and self._begin_fill_drag(pos):
            event.accept()
            return

        super().mousePressEvent(event)
        self._update_hover_cursor(pos)
        self.viewport().update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = self._viewport_pos(event)
        if self._fill_dragging and (event.buttons() & Qt.LeftButton):
            self._update_fill_drag_end(pos)
            self.viewport().setCursor(self._drag_cursor)
            event.accept()
            return

        super().mouseMoveEvent(event)
        self._update_hover_cursor(pos, event.buttons())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        pos = self._viewport_pos(event)
        if self._fill_dragging and event.button() == Qt.LeftButton:
            self._update_fill_drag_end(pos)
            self._finish_fill_drag(pos)
            event.accept()
            return

        super().mouseReleaseEvent(event)
        self._update_hover_cursor(pos)
        self.viewport().update()


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


# Columns the user must not hide (row identity + system-managed Cashier).
_COLUMN_HIDE_LOCKED = {COL_SNO, COL_CASHIER}
# Right-edge pixels that count as the Excel "hidden columns" gap affordance.
_HIDE_GAP_HIT_PX = 8
# Match excel_column_filter._FILTER_RESIZE_MARGIN — leave edge for Qt resize drag.
_COLUMN_RESIZE_HIT_PX = 6


class _FilterHeaderView(QHeaderView):
    """Horizontal header ▾ filters + Excel-style column hide / unhide."""

    filter_changed = Signal(int, set)   # (col_index, accepted_values); empty = cleared
    hide_columns_requested = Signal(object)    # list[int]
    unhide_columns_requested = Signal(object)  # list[int]

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._active: dict = {}   # col -> set of accepted values
        self._value_provider = None  # optional callable(col) -> set[str]
        self._filters_enabled = True  # toolbar AutoFilter on/off
        self._popup = None
        self._sel_anchor: int | None = None
        self.setSectionsClickable(True)
        self.setHighlightSections(True)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)

    def set_value_provider(self, provider) -> None:
        self._value_provider = provider

    def filters_enabled(self) -> bool:
        return bool(self._filters_enabled)

    def set_filters_enabled(self, enabled: bool) -> None:
        """Show/hide ▾ affordances; when off, close popup and clear active sets."""
        enabled = bool(enabled)
        self._filters_enabled = enabled
        if not enabled:
            if self._popup is not None:
                self._popup.close()
                self._popup = None
            self._active.clear()
        self.viewport().update()

    def clear_filters(self) -> None:
        self._active.clear()
        self.viewport().update()

    def sync_active(self, filters: dict) -> None:
        """Mirror DailyRegister._col_filters onto the chevron paint state."""
        if not self._filters_enabled:
            self._active.clear()
        else:
            self._active = {c: set(v) for c, v in (filters or {}).items() if v}
        self.viewport().update()

    def _table(self):
        return self.parent()

    def _is_section_hidden(self, col: int) -> bool:
        if col < 0 or col >= self.count():
            return True
        return self.isSectionHidden(col)

    def _next_visible_col(self, col: int) -> int | None:
        for c in range(col + 1, self.count()):
            if not self._is_section_hidden(c):
                return c
        return None

    def _prev_visible_col(self, col: int) -> int | None:
        for c in range(col - 1, -1, -1):
            if not self._is_section_hidden(c):
                return c
        return None

    def _hidden_between(self, left: int, right: int) -> list[int]:
        lo, hi = (left, right) if left <= right else (right, left)
        return [
            c for c in range(lo + 1, hi)
            if self._is_section_hidden(c) and c not in _COLUMN_HIDE_LOCKED
        ]

    def _gap_hidden_cols_at(self, col: int, pos_x: int) -> list[int]:
        """Hidden columns under the Excel-style thick gap near a section edge."""
        if col < 0 or self._is_section_hidden(col):
            return []
        col_x = self.sectionViewportPosition(col)
        col_w = self.sectionSize(col)
        # Near right edge → hidden between this and next visible
        if pos_x >= col_x + col_w - _HIDE_GAP_HIT_PX:
            nxt = self._next_visible_col(col)
            if nxt is not None:
                return self._hidden_between(col, nxt)
        # Near left edge → hidden between previous visible and this
        if pos_x <= col_x + _HIDE_GAP_HIT_PX:
            prev = self._prev_visible_col(col)
            if prev is not None:
                return self._hidden_between(prev, col)
        return []

    def _selected_columns(self) -> list[int]:
        table = self._table()
        if table is None:
            return []
        cols = sorted({i.column() for i in table.selectedIndexes()})
        return cols

    def _select_column_range(self, anchor: int, col: int) -> None:
        table = self._table()
        if table is None or table.rowCount() <= 0:
            return
        lo, hi = (anchor, col) if anchor <= col else (col, anchor)
        table.clearSelection()
        # ContiguousSelection: select a rectangle covering the column range.
        table.setRangeSelected(
            QTableWidgetSelectionRange(0, lo, table.rowCount() - 1, hi),
            True,
        )
        table.setCurrentCell(table.currentRow() if table.currentRow() >= 0 else 0, col)

    def paintSection(self, painter, rect, logical_index):
        super().paintSection(painter, rect, logical_index)
        if (
            self._filters_enabled
            and logical_index in _FILTER_COLS
            and rect.width() >= 28
        ):
            painter.save()
            is_active = bool(self._active.get(logical_index))
            painter.setPen(QColor("#EA580C") if is_active else QColor("#94A3B8"))
            f = painter.font()
            f.setPointSize(7)
            painter.setFont(f)
            painter.drawText(
                QRect(rect.right() - 16, rect.top(), 14, rect.height()),
                Qt.AlignVCenter | Qt.AlignHCenter,
                "▾",
            )
            painter.restore()

        # Excel-style thicker divider when columns are hidden after this section.
        nxt = self._next_visible_col(logical_index)
        if nxt is not None and nxt > logical_index + 1:
            painter.save()
            pen = QPen(QColor("#475569"), 3)
            pen.setCapStyle(Qt.FlatCap)
            painter.setPen(pen)
            x = rect.right() - 1
            painter.drawLine(x, rect.top() + 2, x, rect.bottom() - 2)
            painter.restore()

    def _on_column_resize_edge(self, pos) -> bool:
        """True when *pos* is on a section boundary (Excel / Qt resize grip)."""
        x = pos.x()
        for visual in range(self.count()):
            logical = self.logicalIndex(visual)
            if self.isSectionHidden(logical):
                continue
            edge = self.sectionViewportPosition(logical) + self.sectionSize(logical)
            if abs(x - edge) <= _COLUMN_RESIZE_HIT_PX:
                return True
        return False

    def mousePressEvent(self, event):
        # Excel: drag the boundary to resize — must reach QHeaderView first.
        if event.button() == Qt.LeftButton and self._on_column_resize_edge(event.pos()):
            super().mousePressEvent(event)
            return
        col = self.logicalIndexAt(event.pos())
        if event.button() == Qt.LeftButton and col >= 0:
            if self._filters_enabled and col in _FILTER_COLS:
                col_x = self.sectionViewportPosition(col)
                col_w = self.sectionSize(col)
                if chevron_hit_contains(col_x, col_w, event.pos().x()):
                    self._open_menu(col, event.globalPosition().toPoint())
                    return
            # Header click selects column(s) for bulk hide (Excel-style).
            mods = event.modifiers()
            if mods & Qt.ShiftModifier and self._sel_anchor is not None:
                self._select_column_range(self._sel_anchor, col)
            else:
                table = self._table()
                if table is not None:
                    table.selectColumn(col)
                self._sel_anchor = col
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        col = self.logicalIndexAt(event.pos())
        gap_hidden = self._gap_hidden_cols_at(col, event.pos().x()) if col >= 0 else []
        selected = self._selected_columns()
        if col >= 0 and col not in selected:
            # Right-click outside current selection → target this column only.
            selected = [col]
            table = self._table()
            if table is not None:
                table.selectColumn(col)
            self._sel_anchor = col

        hide_targets = [c for c in selected if c not in _COLUMN_HIDE_LOCKED]
        # Unhide: gap under cursor, or any user-hidden cols spanned by selection.
        span_hidden: list[int] = []
        if len(selected) >= 2:
            span_hidden = self._hidden_between(min(selected), max(selected))
        unhide_targets = list(dict.fromkeys(gap_hidden + span_hidden))

        menu = QMenu(self)
        if len(hide_targets) > 1:
            act_hide = menu.addAction(f"Hide Columns ({len(hide_targets)})")
        else:
            act_hide = menu.addAction("Hide Column")
        act_hide.setEnabled(bool(hide_targets))

        if unhide_targets:
            act_unhide = menu.addAction(
                f"Unhide Columns ({len(unhide_targets)})"
                if len(unhide_targets) > 1
                else "Unhide Column"
            )
        else:
            act_unhide = menu.addAction("Unhide")
            act_unhide.setEnabled(False)

        chosen = menu.exec(event.globalPos())
        if chosen is act_hide and hide_targets:
            self.hide_columns_requested.emit(hide_targets)
        elif chosen is act_unhide and unhide_targets:
            self.unhide_columns_requested.emit(unhide_targets)
        event.accept()

    def _open_menu(self, col: int, global_pos) -> None:
        if not self._filters_enabled or not callable(self._value_provider):
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
            if not self._filters_enabled:
                return
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
    # n, my_tzs, my_usd, my_refund, date, parked_tzs, parked_usd, parked_refund
    stats_updated     = Signal(
        int, float, float, float, object, float, float, float
    )
    register_status_updated = Signal(int, int)  # draft_count, submitted_count on saved rows
    drafts_changed        = Signal()
    drafts_submitted      = Signal(int)  # response updated; local badge delta
    day_issues_requested  = Signal(object)  # date | None — open Day Issues for this day
    # Item / Payee / Description / import staging changed — refresh Day Issues.
    day_issues_source_changed = Signal()
    undo_redo_changed     = Signal(bool, bool)  # can_undo, can_redo
    edit_state_changed = Signal(bool, int)         # (edit_mode_active, dirty_row_count)
    mode_changed      = Signal(str)                # "my" | "parked" | "merged"
    parked_count_changed = Signal(int)             # parked truck rows for the day
    attachment_count_changed = Signal(int)         # selected row attachment count
    save_busy_changed = Signal(bool)               # True while Save/Submit is in flight
    # Day-level Cheque for the always-visible header field
    active_cheque_changed = Signal(str, bool)  # cheque, editable
    # Table text zoom (Larger / Smaller toolbar) — (can_larger, can_smaller)
    font_size_changed = Signal(bool, bool)
    # Wrap Text toolbar — True when any column is wrapping
    wrap_state_changed = Signal(bool)
    # Excel AutoFilter toolbar — True when column ▾ filters are active
    column_filters_state_changed = Signal(bool)
    # Soft-trash / deletion-request changed — refresh Cashier Trash badge
    trash_changed = Signal()
    # Brief non-blocking status (e.g. after Save) — action bar flash pill
    transient_status = Signal(str)

    def __init__(self, user: User, categories: List[Category], parent=None):
        super().__init__(parent)
        self._user        = user
        self._categories  = categories
        self._table_font_pt = _load_table_font_pt()
        self._wrapped_cols: set = set()  # columns with Wrap Text on
        self._user_hidden_cols: set = set()  # Excel Hide Column (not Cashier)
        self._cat_by_name: dict = {c.name.lower(): c for c in categories}
        self._locked_subitems: dict = {}   # item name (lower) -> [sub-item names]
        self._restrict_items: bool = False
        self._restrict_payees: bool = False
        self._export_restrict_surfaces: set = set()
        self._defer_item_to_verify: bool = False
        # When True, typed/pasted descriptions fill blank Item/Payee from maps.
        self._auto_fill_from_maps: bool = True
        self._duplicate_check_days: int = 5
        self._restrict_trucks: bool = True  # always on — only registered fleet numbers
        self._fleet_numbers: set = set()   # uppercased valid fleet numbers
        self._fleet_kinds: dict = {}       # number → "truck" | "trailer" | "motor_vehicle"
        self._allowed_truck_labels: set = set(DEFAULT_PLACE_LABELS)
        self._people_names: list = []      # Ownership / APR BY suggestions (unrestricted)
        self._payee_names: list = []       # Payee catalog suggestions
        # Guards QuickBooks-style not-in-list prompts (row, col) while modal is open.
        self._not_in_list_prompting: set = set()
        self._cashier_names: dict = {}     # ObjectId -> display name
        self._view_mode: str = "my"        # "my" | "parked" | "merged"
        self._merged_mode: bool = False    # Shared/Merged day (all cashiers)
        self._parked_mode: bool = False    # Parked incorrect-truck follow-up
        self._parked_day_count: int = 0
        # Server rows already saved with register_status=parked.
        self._server_parked_count: int = 0
        # Local Parked bucket until Save persists them as register_status=parked.
        # Serialized draft entries ({client_row_id, cells, pending_meta}); the
        # My draft file (``parked_rows``) holds them between Park and Save.
        self._announce_parked_on_load: bool = False
        self._local_parked: list = []
        self._current_date: Optional[date] = date.today()
        self._saved_count: int   = 0
        self._saved_ids: dict    = {}   # row_index -> ObjectId
        self._saved_txs: dict    = {}   # row_index -> original Transaction (saved rows)
        self._edit_mode: bool    = False
        self._dirty_rows: set    = set()  # saved row indices modified while editing
        self._col_filters: dict   = {}   # col -> set of accepted values
        # Toolbar AutoFilter: when False, ▾ hidden and column filters cleared.
        self._column_filters_enabled: bool = True
        self._search_text: str    = ""
        self._pending_highlight: str = ""  # set by navigate_to_date; consumed in _populate
        self._load_upload_id: str = ""     # one-shot: load this Excel batch instead of a day
        # When True, skip local draft restore (used while staging a daily import).
        self._skip_draft_restore: bool = False
        # Cold My load → "notify"; intentional My↔Parked/Merged flip → "silent";
        # Parked/Merged surfaces always behave as "off".
        self._draft_restore_policy: str = "notify"  # notify | silent | off
        # Same-day My↔Parked: reuse last fetch and filter in memory (no network).
        self._day_tx_cache: list | None = None
        self._day_tx_cache_key: tuple | None = None
        # True after delete/discard until a successful ``_load_date`` finishes.
        # Table-tab entry and My view flips reload when this is set.
        self._day_tx_stale: bool = False
        # My↔Parked keep-alive: My grid stays mounted; session maps stashed while
        # Parked is showing so the flip never flush/wipe/restores My rows.
        self._my_session: dict | None = None
        self._my_table: _ExcelTableWidget | None = None
        self._parked_table: _ExcelTableWidget | None = None
        self._table_stack: QStackedWidget | None = None
        # Day-level Cheque stamp (header field → all data rows)
        self._header_cheque: str = ""
        # row_index -> import metadata stamped onto Transaction at save time
        self._pending_row_meta: dict = {}
        # When True, skip async side-effects from itemChanged (bulk paste/import).
        self._bulk_mutating: bool = False
        # Coalesce Day Issues badge updates: suppress per-cell during bulk, emit once after.
        self._day_issues_notify_pending: bool = False
        # When True, queue truck issues but do not open the correction dialog yet
        # (avoids nested asyncio during daily import modals on Python 3.14).
        self._suppress_truck_dialog: bool = False
        # Coalesce truck issues into one combined dialog (paste / import / edit).
        self._pending_truck_issues: dict = {}  # row -> TruckIssue
        # row -> partner/non-fleet truck accepted via "Allow anyway"
        self._truck_allow_anyway: dict[int, str] = {}
        self._truck_dialog_scheduled: bool = False
        self._open_truck_dialog: object = None
        # Excel cut marquee (cells stay until paste / Insert Cut Cells / Esc)
        self._cut_cells: set = set()          # {(row, col), ...}
        self._cut_payload: dict = {}          # serialized cut buffer
        self._cut_is_rows: bool = False
        # Undo / redo stacks (Excel-style cell + row operations)
        self._undo_stack: list = []
        self._redo_stack: list = []
        self._undo_limit: int = 100
        self._undo_redo_active: bool = False
        # Row-level edit transaction: snapshot whole row so side effects
        # (S/N, Date, Item auto-fill) undo together with the edited cell.
        self._pending_row_edit: dict | None = None
        self._closing_row_edit: dict | None = None
        self._auto_fill_row: int | None = None
        self._auto_fill_seq: int = 0  # ignore stale single-row map lookups
        self._edit_finalize_timer = QTimer(self)
        self._edit_finalize_timer.setSingleShot(True)
        self._edit_finalize_timer.setInterval(50)
        self._edit_finalize_timer.timeout.connect(self._finalize_pending_row_edit)
        # Re-entrancy guards — prevent double-click duplicate inserts.
        self._save_in_flight = False
        self._submit_in_flight = False
        # Phase 4/C — local outbox drain (reconnect + async online Save).
        self._outbox_drain_in_flight = False
        self._outbox_cancel_requested = False
        self._load_in_flight = False
        self._reload_after_drain = False
        self._outbox_backoff_attempt = 0
        self._kick_outbox_after_save = False
        self._outbox_drain_timer = QTimer(self)
        self._outbox_drain_timer.setSingleShot(True)
        self._outbox_drain_timer.timeout.connect(self._kick_outbox_drain)
        # Local draft autosave (crash / power-loss recovery).
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(1_500)
        self._draft_timer.timeout.connect(self._flush_local_draft)
        # Debounce row-height recompute while wrapping + column resize.
        self._wrap_height_timer = QTimer(self)
        self._wrap_height_timer.setSingleShot(True)
        self._wrap_height_timer.setInterval(50)
        self._wrap_height_timer.timeout.connect(self._refresh_wrapped_row_heights)
        # Debounce DB writes for day-level Cheque (saved rows only).
        self._cheque_persist_timer = QTimer(self)
        self._cheque_persist_timer.setSingleShot(True)
        self._cheque_persist_timer.setInterval(400)
        self._cheque_persist_timer.timeout.connect(
            self._kick_persist_header_cheque
        )
        self._restoring_draft = False
        self._load_gen = 0
        self._build_ui()
        self._show_register_loading("Loading…")
        self._emit_undo_redo_state()
        try:
            from tahmeed.signals import app_signals

            app_signals.connectivity_changed.connect(self._on_connectivity_changed)
        except Exception:
            pass
        asyncio.ensure_future(self._load_date(self._current_date))
        asyncio.ensure_future(self._load_register_catalog())
        asyncio.ensure_future(self._load_people_names())
        asyncio.ensure_future(self._load_description_mapping_cache())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _make_register_table(self) -> _ExcelTableWidget:
        """Build one Excel-style register grid (My or Parked surface)."""
        table = _ExcelTableWidget(DEFAULT_EDITABLE_ROWS, len(HEADERS))
        table._grid_owner = self
        _fhv = _FilterHeaderView(table)
        _fhv.set_value_provider(self._filter_menu_values)
        _fhv.filter_changed.connect(self._on_col_filter_changed)
        _fhv.hide_columns_requested.connect(self._hide_columns)
        _fhv.unhide_columns_requested.connect(self._unhide_columns)
        table.setHorizontalHeader(_fhv)
        table.setHorizontalHeaderLabels(HEADERS)
        sno_hdr = table.horizontalHeaderItem(COL_SNO)
        if sno_hdr is not None:
            sno_hdr.setTextAlignment(Qt.AlignCenter)
        hh = table.horizontalHeader()
        hh.setSectionsMovable(False)
        hh.setStretchLastSection(False)
        hh.setMinimumSectionSize(50)
        for col in range(len(HEADERS)):
            hh.setSectionResizeMode(col, QHeaderView.Interactive)
        hh.setSectionResizeMode(COL_SNO, QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_DESC, QHeaderView.Stretch)
        hh.sectionResized.connect(lambda *_: self._schedule_wrap_height_refresh())

        for col, width in _COL_PREFERRED.items():
            table.setColumnWidth(col, width)

        table.setSelectionMode(QAbstractItemView.ContiguousSelection)
        table.setVerticalHeader(ExcelRowHeaderView(table, owner=self))
        table.setTabKeyNavigation(False)
        sm = table.selectionModel()
        sm.selectionChanged.connect(lambda *_: table.viewport().update())
        sm.currentChanged.connect(lambda *_: table.viewport().update())

        table.setItemDelegate(_ExcelCellDelegate(table))
        table.setItemDelegateForColumn(
            COL_ITEM,
            _ItemDelegate(lambda: [c.name for c in self._categories], table),
        )
        table.setItemDelegateForColumn(
            COL_DESC,
            _DescriptionDelegate(
                cat_getter=lambda name: self._cat_by_name.get(name.lower()),
                subs_getter=lambda name: self._locked_subitems.get(name.lower(), []),
                parent=table,
            ),
        )
        table.setItemDelegateForColumn(
            COL_TRUCK,
            _TruckDelegate(lambda: sorted(self._fleet_numbers), table),
        )
        table.setItemDelegateForColumn(
            COL_DATE, _DateDelegate(lambda: self._current_date, table)
        )
        table.setItemDelegateForColumn(COL_REF, _RefFloatDelegate(table))
        table.setItemDelegateForColumn(COL_TZS, _TZSDelegate(table))
        table.setItemDelegateForColumn(COL_USD, _TZSDelegate(table))
        table.setItemDelegateForColumn(COL_RECEIPT, _ReceiptDelegate(table))
        people_del = _ItemDelegate(lambda: list(self._people_names), table)
        table.setItemDelegateForColumn(COL_OWN, people_del)
        table.setItemDelegateForColumn(COL_APR, people_del)
        table.setItemDelegateForColumn(
            COL_PAYEE,
            _ItemDelegate(lambda: list(self._payee_names), table),
        )

        table.itemChanged.connect(self._on_item_changed)
        table.model().dataChanged.connect(self._on_model_data_changed)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_context_menu)
        table.itemSelectionChanged.connect(self._emit_attachment_badge)
        table.currentCellChanged.connect(lambda *_: self._emit_active_cheque())
        table.currentCellChanged.connect(self._on_current_cell_changed)
        return table

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Tables: My stays alive; Parked is a separate small surface ──
        self._my_table = self._make_register_table()
        self._parked_table = self._make_register_table()
        self._table = self._my_table
        self._table_stack = QStackedWidget(self)
        self._table_stack.addWidget(self._my_table)
        self._table_stack.addWidget(self._parked_table)
        self._table_stack.setCurrentWidget(self._my_table)

        QTimer.singleShot(0, self._fit_table_columns)
        self._apply_table_font_size(persist=False, emit=False)
        self._apply_column_visibility(show_cashier=False)

        self._table_host = QWidget(self)
        host_lay = QVBoxLayout(self._table_host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.setSpacing(0)
        host_lay.addWidget(self._table_stack)
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
        self._sync_row_header_labels()
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

        tables = [
            t for t in (self._my_table, self._parked_table, self._table)
            if t is not None
        ]
        seen: set[int] = set()
        for table in tables:
            key = id(table)
            if key in seen:
                continue
            seen.add(key)
            hh = table.horizontalHeader()
            hh.setSectionResizeMode(COL_DESC, QHeaderView.Interactive)
            for col, width in widths.items():
                table.setColumnWidth(col, width)
            hh.setSectionResizeMode(COL_SNO, QHeaderView.Fixed)
            hh.setSectionResizeMode(COL_DESC, QHeaderView.Stretch)

    def _apply_navigate_view_mode(self, view_mode: str | None) -> None:
        """Bind My/Parked/Merged flags + surface before a date load."""
        if view_mode not in ("my", "parked", "merged"):
            return
        self._view_mode = view_mode
        self._merged_mode = view_mode == "merged"
        self._parked_mode = view_mode == "parked"
        self.mode_changed.emit(view_mode)
        if view_mode == "parked" and self._parked_table is not None:
            self._clear_my_session()
            self._activate_table_surface(self._parked_table)
            self._reset_parked_surface_maps()
            self._draft_restore_policy = "off"
        elif self._my_table is not None:
            self._ensure_my_surface_active()
            if view_mode == "my":
                self._draft_restore_policy = "silent"

    def navigate_to_date(
        self,
        d: date,
        highlight_term: str = "",
        *,
        merged: bool | None = None,
        view_mode: str | None = None,
        announce_parked: bool = False,
    ) -> bool:
        """Called by dashboard when TransactionBrowser 'Go To' is used.

        highlight_term — if provided, the register scrolls to the first row
        containing this text after the date loads and briefly flashes it.
        merged=True shows every cashier's rows (Browse is a merged view).
        view_mode — optional ``my`` / ``parked`` / ``merged`` applied before load
        (e.g. Trash restore of parked rows).
        announce_parked — after load, toast if parked truck rows remain for the day
        (Drafts → Open in Register).
        """
        if isinstance(d, datetime):
            d = d.date()
        if not isinstance(d, date):
            return False

        self._announce_parked_on_load = bool(announce_parked)

        # Blank New table → user picked a Reconciled Date.
        if self._current_date is None:
            self._pending_highlight = highlight_term
            self._commit_open_editor()
            if merged is True:
                self._apply_navigate_view_mode("merged")
            elif view_mode in ("my", "parked", "merged"):
                self._apply_navigate_view_mode(view_mode)
            if self.has_unsaved_work():
                # Keep typed rows; stamp them onto the chosen day.
                self._current_date = d
                self._stamp_empty_row_dates()
                self._update_footer()
                self._schedule_draft_autosave()
                return True
            self._reset_edit_state()
            self._current_date = d
            self._show_register_loading(f"Loading {d.strftime('%d %b %Y')}…")
            asyncio.ensure_future(self._load_date(d))
            return True

        if (
            d == self._current_date
            and not highlight_term
            and not getattr(self, "_load_upload_id", "")
            and merged is not True
            and view_mode is None
        ):
            return True
        if (
            d == self._current_date
            and view_mode in ("my", "parked", "merged")
            and not highlight_term
            and not getattr(self, "_load_upload_id", "")
        ):
            # Same day, just flip the surface (Trash restore on open day).
            self.set_view_mode(view_mode)
            return True
        self._pending_highlight = highlight_term
        self._commit_open_editor()
        if self.has_unsaved_work():
            self._flush_local_draft()
            choice = self._prompt_unsaved_work(
                "You have unsaved changes on this date.\n"
                "Save as draft before leaving?"
            )
            if choice == "cancel":
                self._pending_highlight = ""
                self._load_upload_id = ""
                return False
            if choice == "save":
                asyncio.ensure_future(self._save_then_navigate(d))
                return True
            # Discard → clear local recovery draft for this date, then leave.
            self._clear_local_draft()
        self._reset_edit_state()
        if merged is True:
            self._apply_navigate_view_mode("merged")
        elif view_mode in ("my", "parked", "merged"):
            self._apply_navigate_view_mode(view_mode)
        self._current_date = d
        if self._load_upload_id:
            self._show_register_loading("Loading upload…")
        else:
            self._show_register_loading(f"Loading {d.strftime('%d %b %Y')}…")
        asyncio.ensure_future(self._load_date(d))
        return True

    def current_date(self) -> Optional[date]:
        """Reconciled / register day Simple is currently open on (or None if unset)."""
        return self._current_date

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
        if not isinstance(d, date):
            d = date.today()
        self.navigate_to_date(d, merged=True)

    async def _save_then_navigate(self, d: date) -> None:
        ok = await self._do_save()
        if not ok or self.has_unsaved_work():
            self._pending_highlight = ""
            self._load_upload_id = ""
            return
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

    def _invalidate_day_tx_cache(self) -> None:
        self._day_tx_cache = None
        self._day_tx_cache_key = None
        self._day_tx_stale = True
        # Day data changed — My keep-alive session is no longer trustworthy.
        self._clear_my_session()

    def _make_day_cache_key(
        self,
        d: date,
        *,
        merged: bool,
        upload_id: str = "",
    ) -> tuple:
        if upload_id:
            return ("upload", str(upload_id))
        if merged:
            return ("merged", d.isoformat())
        return ("cashier", d.isoformat(), str(self._user._id))

    def _snapshot_grid_session(self) -> dict:
        """Capture row maps bound to the active table (for My keep-alive)."""
        return {
            "saved_count": self._saved_count,
            "saved_ids": dict(self._saved_ids),
            "saved_txs": dict(self._saved_txs),
            "pending_row_meta": dict(self._pending_row_meta),
            "dirty_rows": set(self._dirty_rows),
            "edit_mode": bool(self._edit_mode),
            "truck_allow_anyway": dict(self._truck_allow_anyway),
            "pending_truck_issues": dict(self._pending_truck_issues),
            "undo_stack": list(self._undo_stack),
            "redo_stack": list(self._redo_stack),
            "cut_cells": set(self._cut_cells),
            "cut_payload": dict(self._cut_payload),
            "cut_is_rows": bool(self._cut_is_rows),
        }

    def _restore_grid_session(self, session: dict) -> None:
        self._saved_count = int(session.get("saved_count") or 0)
        self._saved_ids = dict(session.get("saved_ids") or {})
        self._saved_txs = dict(session.get("saved_txs") or {})
        self._pending_row_meta = dict(session.get("pending_row_meta") or {})
        self._dirty_rows = set(session.get("dirty_rows") or ())
        self._edit_mode = bool(session.get("edit_mode"))
        self._truck_allow_anyway = dict(session.get("truck_allow_anyway") or {})
        self._pending_truck_issues = dict(session.get("pending_truck_issues") or {})
        self._undo_stack = list(session.get("undo_stack") or [])
        self._redo_stack = list(session.get("redo_stack") or [])
        self._cut_cells = set(session.get("cut_cells") or ())
        self._cut_payload = dict(session.get("cut_payload") or {})
        self._cut_is_rows = bool(session.get("cut_is_rows"))

    def _clear_my_session(self) -> None:
        self._my_session = None

    def _activate_table_surface(self, table: _ExcelTableWidget) -> None:
        """Point ``self._table`` + stack at My or Parked without wiping cells."""
        if table is None or table is self._table:
            if (
                self._table_stack is not None
                and table is not None
                and self._table_stack.currentWidget() is not table
            ):
                self._table_stack.setCurrentWidget(table)
            return
        self._table = table
        if self._table_stack is not None:
            self._table_stack.setCurrentWidget(table)

    def _reset_parked_surface_maps(self) -> None:
        """Fresh maps for the Parked table before a small populate."""
        self._saved_count = 0
        self._saved_ids = {}
        self._saved_txs = {}
        self._pending_row_meta = {}
        self._dirty_rows = set()
        self._edit_mode = False
        self._truck_allow_anyway = {}
        self._pending_truck_issues = {}
        self._undo_stack = []
        self._redo_stack = []
        self._cut_cells = set()
        self._cut_payload = {}
        self._cut_is_rows = False
        self.edit_state_changed.emit(False, 0)
        self._emit_undo_redo_state()

    def _ensure_my_surface_active(self) -> None:
        """Leave Parked keep-alive (if any) and bind the My table for loads."""
        if self._parked_mode and self._my_table is not None:
            if self._table is self._parked_table:
                self._local_parked = self._table_parked_entries()
            self._clear_my_session()
            self._activate_table_surface(self._my_table)
        elif self._my_table is not None and self._table is not self._my_table:
            self._activate_table_surface(self._my_table)

    def _flip_to_parked_keep_my(self) -> bool:
        """My → Parked without flush/wipe/restore. Returns False if unavailable."""
        if self._my_table is None or self._parked_table is None:
            return False
        if self._current_date is None:
            return False
        # Don't stash a My grid that still shows deleted server rows.
        if self._day_tx_stale:
            return False
        self._draft_timer.stop()
        self._commit_open_editor()
        self._my_session = self._snapshot_grid_session()
        self._activate_table_surface(self._parked_table)
        self._reset_parked_surface_maps()
        self._view_mode = "parked"
        self._merged_mode = False
        self._parked_mode = True
        self.mode_changed.emit("parked")
        self._draft_restore_policy = "off"
        if self._day_tx_cache is not None:
            self._apply_txs_to_view(
                list(self._day_tx_cache),
                upload_id="",
                reload_local_parked=False,
            )
        else:
            # Local parked immediately; server parked arrives when load returns.
            self._apply_txs_to_view(
                [], upload_id="", reload_local_parked=False
            )
            self._show_register_loading("Loading…")
            asyncio.ensure_future(self._load_date(self._current_date))
        self._schedule_outbox_drain(delay_ms=800)
        return True

    def _flip_to_my_keep_alive(self) -> bool:
        """Parked → My using the preserved My table. Returns False to slow-path."""
        if self._my_table is None or self._my_session is None:
            return False
        # Delete/discard while away — force a real My reload, not the stash.
        if self._day_tx_stale:
            return False
        self._draft_timer.stop()
        self._commit_open_editor()
        prev_parked = list(self._local_parked)
        self._local_parked = self._table_parked_entries()
        self._activate_table_surface(self._my_table)
        self._restore_grid_session(self._my_session)
        self._clear_my_session()
        self._view_mode = "my"
        self._merged_mode = False
        self._parked_mode = False
        self.mode_changed.emit("my")
        self._draft_restore_policy = "notify"
        self._emit_parked_count()
        self._update_footer()
        self.edit_state_changed.emit(
            bool(self._edit_mode), len(self._dirty_rows)
        )
        self._emit_undo_redo_state()
        self._emit_active_cheque()
        if prev_parked != self._local_parked:
            self._flush_day_issues_source_changed(force=True)
        self._schedule_outbox_drain(delay_ms=800)
        return True

    def _apply_txs_to_view(
        self,
        txs: list,
        *,
        upload_id: str = "",
        reload_local_parked: bool = True,
    ) -> None:
        """Update parked badge, filter for My/Parked/Merged, populate the grid."""
        parked_all = [
            tx for tx in txs
            if (getattr(tx, "register_status", "") or "") == "parked"
        ]
        if not upload_id and reload_local_parked:
            # Disk is the durable copy of the local Parked bucket (My file).
            self._load_local_parked_from_disk()
        if not self._merged_mode:
            own_parked = [
                tx for tx in parked_all
                if tx.cashier_id == self._user._id
                or str(tx.cashier_id) == str(self._user._id)
            ]
            self._server_parked_count = len(own_parked)
            self._emit_parked_count()
        if self._parked_mode:
            view_txs = [
                tx for tx in txs
                if (getattr(tx, "register_status", "") or "") == "parked"
            ]
        else:
            view_txs = [
                tx for tx in txs
                if (getattr(tx, "register_status", "") or "") != "parked"
            ]
        self._pending_row_meta.clear()
        self._populate(view_txs)
        if self._parked_mode and not upload_id:
            self._append_local_parked_rows()
        show_cashier = self._merged_mode or bool(upload_id)
        self._apply_column_visibility(show_cashier=show_cashier)

    # ------------------------------------------------------------------
    # Local Parked bucket (incorrect trucks awaiting Save → server parked)
    # ------------------------------------------------------------------

    def _emit_parked_count(self) -> None:
        self._parked_day_count = int(self._server_parked_count) + len(
            self._local_parked
        )
        self.parked_count_changed.emit(self._parked_day_count)

    def _load_local_parked_from_disk(self) -> None:
        """Refresh ``_local_parked`` from the My draft file for the open day."""
        if self._current_date is None:
            self._local_parked = []
            return
        try:
            draft = load_register_draft(
                self._user._id, self._current_date, merged=False
            )
        except Exception:
            draft = None
        self._local_parked = draft_parked_rows(draft)

    def _table_parked_entries(self) -> list:
        """Serialize unsaved rows marked parked that are on the grid right now."""
        out: list = []
        for row in range(self._saved_count, self._table.rowCount()):
            if not self._row_has_data(row) or not self._row_marked_parked(row):
                continue
            rid = self._ensure_row_client_id(row)
            meta = dict(self._pending_row_meta.get(row) or {})
            meta["parked"] = True
            meta["client_row_id"] = rid
            # Keep outbox sync_status when queued; drop stale empty values.
            if not meta.get("sync_status"):
                meta.pop("sync_status", None)
            out.append({
                "client_row_id": rid,
                "cells": cells_for_json(self._row_value_map(row)),
                "pending_meta": serialize_pending_meta(meta),
            })
        return out

    def _materialize_local_parked_for_save(self) -> list[int]:
        """Append ``_local_parked`` onto the current grid for the Save insert pass.

        Used when Save runs from My: parked rows already left the My table into
        the local bucket. Returns the row indices that were added.
        """
        if self._parked_mode or self._merged_mode or not self._local_parked:
            return []
        existing: set[str] = set()
        for row in range(self._saved_count, self._table.rowCount()):
            if self._row_has_data(row) and self._row_marked_parked(row):
                rid = entry_client_row_id(
                    {"client_row_id": (self._pending_row_meta.get(row) or {}).get(
                        "client_row_id"
                    )}
                ) or self._ensure_row_client_id(row)
                if rid:
                    existing.add(rid)
        to_add = [
            e for e in self._local_parked
            if isinstance(e, dict) and entry_client_row_id(e) not in existing
        ]
        if not to_add:
            return []
        start = self._table.rowCount()
        self._bulk_mutating = True
        self._restoring_draft = True
        prev = self._table.blockSignals(True)
        try:
            self._table.setRowCount(start + len(to_add))
            self._init_editable_rows(start, start + len(to_add))
            for offset, entry in enumerate(to_add):
                row = start + offset
                values = cells_from_json(entry.get("cells"))
                self._write_row_values(row, values)
                meta = hydrate_pending_meta(entry.get("pending_meta")) or {}
                meta["parked"] = True
                rid = entry_client_row_id(entry) or meta.get("client_row_id")
                meta["client_row_id"] = rid or new_client_row_id()
                # Preserve PENDING when offline-queued; otherwise clear.
                if not meta.get("sync_status"):
                    meta.pop("sync_status", None)
                self._pending_row_meta[row] = meta
                self._activate_row(row)
                self._sync_row_date(row)
        finally:
            self._table.blockSignals(prev)
            self._restoring_draft = False
            self._bulk_mutating = False
        self._renumber()
        return list(range(start, start + len(to_add)))

    def _drop_local_parked_by_client_ids(self, client_ids: set[str]) -> None:
        """Remove local parked entries that were ACKed by the server."""
        if not client_ids:
            return
        self._local_parked = [
            e for e in self._local_parked
            if entry_client_row_id(e) not in client_ids
        ]
        self._emit_parked_count()

    def _append_local_parked_rows(self) -> None:
        """Parked view: show locally parked rows as editable unsaved rows."""
        entries = list(self._local_parked)
        if not entries:
            return
        self._bulk_mutating = True
        self._restoring_draft = True
        prev = self._table.blockSignals(True)
        try:
            start = self._table.rowCount()
            self._table.setRowCount(start + len(entries))
            self._init_editable_rows(start, start + len(entries))
            for offset, entry in enumerate(entries):
                row = start + offset
                values = cells_from_json(entry.get("cells"))
                self._write_row_values(row, values)
                meta = hydrate_pending_meta(entry.get("pending_meta")) or {}
                meta["parked"] = True
                meta.pop("sync_status", None)
                rid = entry_client_row_id(entry) or meta.get("client_row_id")
                meta["client_row_id"] = rid or new_client_row_id()
                self._pending_row_meta[row] = meta
                self._activate_row(row)
                self._sync_row_date(row)
        finally:
            self._table.blockSignals(prev)
            self._restoring_draft = False
            self._bulk_mutating = False
        self._renumber()
        for row in range(start, self._table.rowCount()):
            self._update_truck_required_highlight(row)
        self._update_footer()
        self._flush_day_issues_source_changed(force=True)

    def _append_entries_onto_my_surface(self, entries: list) -> int:
        """Write promoted parked entries onto the keep-alive My table + session."""
        if not entries or self._my_table is None:
            return 0
        parked_snap = self._snapshot_grid_session()
        parked_table = self._table
        parked_mode = self._parked_mode
        try:
            self._table = self._my_table
            self._parked_mode = False
            if self._my_session is not None:
                self._restore_grid_session(self._my_session)
            start = self._first_empty_editable_row()
            needed = start + len(entries) - self._table.rowCount()
            if needed > 0:
                self._append_editable_rows(needed + 5)
            truck_cells: list = []
            prev = self._table.blockSignals(True)
            self._bulk_mutating = True
            self._restoring_draft = True
            suppress = self._suppress_truck_dialog
            self._suppress_truck_dialog = True
            try:
                for offset, entry in enumerate(entries):
                    row = start + offset
                    values = cells_from_json(entry.get("cells"))
                    allow = (entry.get("allow_anyway") or "").strip()
                    if allow:
                        self._truck_allow_anyway[row] = allow
                    truck_cells.extend(self._write_row_values(row, values))
                    meta = hydrate_pending_meta(entry.get("pending_meta")) or {}
                    meta.pop("parked", None)
                    meta.pop("sync_status", None)
                    rid = (
                        entry_client_row_id(entry)
                        or meta.get("client_row_id")
                        or new_client_row_id()
                    )
                    meta["client_row_id"] = rid
                    self._pending_row_meta[row] = meta
                    tx_id = entry.get("tx_id")
                    if tx_id:
                        self._saved_ids[row] = tx_id
                    self._activate_row(row)
                    self._sync_row_date(row)
                self._finalize_truck_cells(truck_cells)
            finally:
                self._suppress_truck_dialog = suppress
                self._table.blockSignals(prev)
                self._bulk_mutating = False
                self._restoring_draft = False
            self._renumber()
            self._update_footer()
            self._my_session = self._snapshot_grid_session()
            return len(entries)
        finally:
            self._parked_mode = parked_mode
            self._table = parked_table
            self._restore_grid_session(parked_snap)

    def _promote_accepted_parked_rows_to_my(self) -> int:
        """Move Parked rows with an accepted truck onto My entries immediately.

        Used after Fix parked trucks (Allow / Allow all / Apply). Local parked
        rows are appended to the keep-alive My grid; saved ``register_status=
        parked`` leftovers are unparked to draft on the server (truck + status).
        """
        if not self._parked_mode:
            return 0
        self._commit_open_editor()
        local_rows: list[int] = []
        # (tx_id, updates) so Correct persists the new truck with the status flip.
        saved_patches: list[tuple] = []
        for row in range(self._table.rowCount()):
            if row >= self._saved_count and not self._row_has_data(row):
                continue
            it = self._table.item(row, COL_TRUCK)
            raw = (it.text().strip() if it else "")
            accept_empty = self._row_accepts_empty_truck(row)
            if not raw and not accept_empty:
                continue
            if raw and not self._row_truck_is_valid(row):
                continue
            if row in self._saved_ids:
                tx_id = self._saved_ids[row]
                try:
                    updates = self._updates_from_row(row) or {}
                except ValueError:
                    updates = {}
                updates = dict(updates)
                updates["register_status"] = "draft"
                saved_patches.append((tx_id, updates))
                self._dirty_rows.discard(row)
            elif row >= self._saved_count:
                local_rows.append(row)

        if not local_rows and not saved_patches:
            return 0

        entries: list = []
        for row in sorted(local_rows):
            rid = self._ensure_row_client_id(row)
            meta = dict(self._pending_row_meta.get(row) or {})
            meta.pop("parked", None)
            meta.pop("sync_status", None)
            meta["client_row_id"] = rid
            allow = self._truck_allow_anyway.get(row)
            entry = {
                "client_row_id": rid,
                "cells": cells_for_json(self._row_value_map(row)),
                "pending_meta": serialize_pending_meta(meta),
            }
            if allow:
                entry["allow_anyway"] = allow
            entries.append(entry)

        if entries:
            self._append_entries_onto_my_surface(entries)

        if local_rows:
            prev = self._table.blockSignals(True)
            self._bulk_mutating = True
            try:
                for row in reversed(sorted(local_rows)):
                    self._shift_row_maps_on_remove(row)
                    self._table.removeRow(row)
            finally:
                self._bulk_mutating = False
                self._table.blockSignals(prev)
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._emit_undo_redo_state()
            self._renumber()

        self._local_parked = self._table_parked_entries()
        self._flush_local_draft()
        self._emit_parked_count()
        self._update_footer()
        self._flush_day_issues_source_changed(force=True)

        n_local = len(entries)
        n_saved = len(saved_patches)
        total = n_local + n_saved
        if saved_patches:
            asyncio.ensure_future(
                self._unpark_saved_rows(
                    saved_patches,
                    loading_message=(
                        f"Moving {n_saved} accepted "
                        f"row{'s' if n_saved != 1 else ''} to My…"
                    ),
                    status_message=(
                        f"Moved {total} accepted "
                        f"row{'s' if total != 1 else ''} to My entries"
                    ),
                )
            )
            return total

        if self._my_session is not None:
            self._flip_to_my_keep_alive()
        else:
            self.set_view_mode("my")
        self.transient_status.emit(
            f"Moved {n_local} accepted "
            f"row{'s' if n_local != 1 else ''} to My entries"
        )
        return total

    def _merge_local_parked_entries(self, entries: list) -> int:
        """Append parked entries without duplicating the same client_row_id.

        Returns how many *new* entries were added to ``_local_parked``.
        """
        if not entries:
            return 0
        existing_ids = {
            entry_client_row_id(e)
            for e in self._local_parked
            if entry_client_row_id(e)
        }
        added = 0
        for entry in entries:
            rid = entry_client_row_id(entry)
            if rid and rid in existing_ids:
                continue
            self._local_parked.append(entry)
            if rid:
                existing_ids.add(rid)
            added += 1
        return added

    def _park_rows_locally(self) -> int:
        """Move unsaved rows marked parked off My into the local Parked bucket.

        Nothing is sent to the server: correct rows stay on My entries ready
        for Save; parked rows wait in Parked until their truck is fixed.
        Returns the number of rows parked.
        """
        self._commit_open_editor()
        if self._parked_mode:
            # Already on the Parked surface — never re-append; just persist.
            self._local_parked = self._table_parked_entries()
            self._flush_local_draft()
            self._emit_parked_count()
            return 0
        rows = sorted(
            row
            for row in range(self._saved_count, self._table.rowCount())
            if self._row_has_data(row) and self._row_marked_parked(row)
        )
        if not rows:
            return 0
        entries = self._table_parked_entries()
        self._draft_timer.stop()
        prev = self._table.blockSignals(True)
        self._bulk_mutating = True
        try:
            for row in reversed(rows):
                self._shift_row_maps_on_remove(row)
                self._pending_truck_issues = {
                    (r - 1 if r > row else r): issue
                    for r, issue in self._pending_truck_issues.items()
                    if r != row
                }
                self._table.removeRow(row)
            min_rows = self._saved_count + DEFAULT_EDITABLE_ROWS
            if self._table.rowCount() < min_rows:
                start = self._table.rowCount()
                self._table.setRowCount(min_rows)
                self._init_editable_rows(start, min_rows)
        finally:
            self._bulk_mutating = False
            self._table.blockSignals(prev)
        # Row indices changed under any recorded undo ops — drop history.
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._emit_undo_redo_state()
        self._renumber()
        n = self._merge_local_parked_entries(entries)
        self._flush_local_draft()
        self._update_footer()
        self._emit_parked_count()
        self._flush_day_issues_source_changed(force=True)
        if n:
            self.transient_status.emit(
                f"{n} row{'s' if n != 1 else ''} parked for truck follow-up — "
                "click Save to store them. Fix trucks under Parked anytime."
            )
        return n

    def _row_truck_is_valid(self, row: int) -> bool:
        """True when the truck cell resolves to a fleet / allowed value."""
        it = self._table.item(row, COL_TRUCK)
        raw = it.text().strip() if it else ""
        if not raw:
            return False
        status, _value = self._resolve_truck_text(raw, row=row)
        return status == "ok"

    def _maybe_restore_draft_after_populate(self) -> None:
        """Apply local draft per policy. Parked/Merged never restore."""
        policy = "off" if self._skip_draft_restore else self._draft_restore_policy
        if self._parked_mode or self._merged_mode:
            policy = "off"
        try:
            if policy == "off":
                return
            restored = self._restore_local_draft()
            if restored and policy == "notify":
                self._show_draft_restored_notice(*restored)
        finally:
            # Next cold day open should warn again unless set_view_mode overrides.
            self._draft_restore_policy = "notify"

    def _try_apply_cached_view(self) -> bool:
        """Same-day My↔Parked (or Merged cache hit): filter in memory, no network."""
        if (
            self._day_tx_cache is None
            or self._day_tx_cache_key is None
            or self._current_date is None
        ):
            return False
        key = self._make_day_cache_key(
            self._current_date,
            merged=self._merged_mode,
            upload_id="",
        )
        if self._day_tx_cache_key != key:
            return False
        self._apply_txs_to_view(list(self._day_tx_cache), upload_id="")
        self._maybe_restore_draft_after_populate()
        self._maybe_announce_parked_after_load()
        self._schedule_outbox_drain(delay_ms=800)
        return True

    def _maybe_announce_parked_after_load(self) -> None:
        """Toast parked follow-up count after Drafts → Open in Register."""
        if not self._announce_parked_on_load:
            return
        self._announce_parked_on_load = False
        n = int(self._parked_day_count or 0)
        if n <= 0:
            return
        plural = "s" if n != 1 else ""
        self.transient_status.emit(
            f"{n} parked truck row{plural} on this day — "
            "open Parked to fix before submit"
        )

    async def _load_date(self, d: date) -> None:
        from tahmeed.ui.async_utils import pause_background_polls

        with pause_background_polls(self):
            await self._load_date_body(d)

    async def _load_date_body(self, d: date) -> None:
        # Settle in-flight drain before wiping the grid (do not block on FAILED leftovers).
        if self._outbox_drain_in_flight or self._outbox_pending_rows():
            await self._await_outbox_idle(timeout_s=180, require_empty=False)

        self._load_gen += 1
        seq = self._load_gen
        self._load_in_flight = True
        if getattr(self, "_description_cache_day", None) != d:
            from tahmeed.services.cashier_service import invalidate_description_cache

            invalidate_description_cache()
            self._description_cache_day = d
            await self._load_description_cache()
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
            # Cache full day (incl. parked) for fast My↔Parked flips.
            self._day_tx_cache = list(txs)
            self._day_tx_cache_key = self._make_day_cache_key(
                d, merged=self._merged_mode, upload_id=upload_id or ""
            )
            if self._parked_mode:
                view_for_names = [
                    tx for tx in txs
                    if (getattr(tx, "register_status", "") or "") == "parked"
                ]
            else:
                view_for_names = [
                    tx for tx in txs
                    if (getattr(tx, "register_status", "") or "") != "parked"
                ]
            ids = [tx.cashier_id for tx in view_for_names if tx.cashier_id]
            cashier_names = {}
            if ids:
                from tahmeed.services.accountant_service import get_cashier_names
                cashier_names = await get_cashier_names(ids)
            if seq != self._load_gen:
                return
            self._cashier_names = cashier_names
            # Keep in-memory Parked bucket when My is stashed (My↔Parked flip).
            self._apply_txs_to_view(
                txs,
                upload_id=upload_id or "",
                reload_local_parked=(self._my_session is None),
            )
            self._maybe_restore_draft_after_populate()
            self._maybe_announce_parked_after_load()
            # Successful paint — Table / My no longer need a forced refresh.
            self._day_tx_stale = False
            # Reconnect / open-day: push any PENDING/FAILED outbox rows.
            self._schedule_outbox_drain(delay_ms=800)
        except Exception as exc:
            if seq == self._load_gen:
                # Keep ``_day_tx_stale`` so the next Table/My entry retries.
                QMessageBox.critical(self, "Error", f"Failed to load:\n{exc}")
        finally:
            if seq == self._load_gen:
                self._load_in_flight = False
                self._hide_register_loading()

    def set_merged_mode(self, merged: bool) -> None:
        """Compat: My ↔ Merged. Prefer ``set_view_mode``."""
        self.set_view_mode("merged" if merged else "my")

    def set_view_mode(self, mode: str) -> None:
        """Switch My entries / Parked / Merged.

        My ↔ Parked is an in-memory view flip: the My grid stays mounted, Parked
        uses a separate small surface, and the flip never flushes drafts, saves
        to the server, or wipe/restores My rows. Merged still reloads the day
        browser (flush + cache/network) because it is a different scope.
        """
        mode = mode if mode in ("my", "parked", "merged") else "my"
        if mode == self._view_mode:
            return
        prev = self._view_mode

        # Fast path: My ↔ Parked keep-alive (no save / no My rebuild).
        if prev == "my" and mode == "parked":
            if self._flip_to_parked_keep_my():
                return
        if prev == "parked" and mode == "my":
            if self._flip_to_my_keep_alive():
                return

        self._draft_timer.stop()
        self._commit_open_editor()
        # Leaving Parked without a keep-alive session: sync bucket, then use My.
        if self._parked_mode and self._table is self._parked_table:
            self._local_parked = self._table_parked_entries()
        if prev == "parked" or mode == "merged" or prev == "merged":
            # Merged (or slow Parked→My) still needs a durable draft snapshot.
            if self.has_unsaved_work() or self._parked_mode or self._local_parked:
                self._flush_local_draft()
        self._ensure_my_surface_active()
        self._view_mode = mode
        self._merged_mode = mode == "merged"
        self._parked_mode = mode == "parked"
        self._reset_edit_state()
        self.mode_changed.emit(self._view_mode)
        if self._current_date is None:
            self._populate([])
            return
        if mode in ("parked", "merged"):
            self._draft_restore_policy = "off"
        elif mode == "my" and prev in ("parked", "merged"):
            self._draft_restore_policy = "silent"
        else:
            self._draft_restore_policy = "notify"
        # Slow Parked (no My session): bind parked surface before populate.
        if mode == "parked" and self._parked_table is not None:
            self._activate_table_surface(self._parked_table)
            self._reset_parked_surface_maps()
        # After delete/discard, never reuse a stale in-memory day cache.
        if not self._day_tx_stale and self._try_apply_cached_view():
            return
        self._show_register_loading("Loading…")
        asyncio.ensure_future(self._load_date(self._current_date))

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
        from tahmeed.ui.async_utils import pause_background_polls

        try:
            with pause_background_polls(self):
                await self._submit_for_verify_body()
        finally:
            self._submit_in_flight = False
            if not self._save_in_flight:
                self.save_busy_changed.emit(False)

    async def _submit_for_verify_body(self) -> None:
        self._commit_open_editor()
        if self._current_date is None:
            QMessageBox.warning(
                self,
                "Reconciled Date required",
                "Set Reconciled Date before submitting for verify.",
            )
            return
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
        if not await self._confirm_parked_trucks_for_submit(d):
            return
        if not await self._confirm_day_issues_for_submit(d):
            return
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
            self.drafts_submitted.emit(int(n or 0))
            self.drafts_changed.emit()
            await self._load_date(d)
        except Exception as exc:
            QMessageBox.critical(self, "Submit Failed", str(exc))

    async def _confirm_parked_trucks_for_submit(self, d: date) -> bool:
        """Block submit while parked incorrect-truck rows remain for this cashier."""
        if self._parked_mode:
            # Grid is the live copy of the local bucket while Parked is open.
            self._local_parked = self._table_parked_entries()
        count = int(getattr(self, "_parked_day_count", 0) or 0)
        if count <= 0 and self._current_date == d:
            # Refresh count from server if we have not loaded Parked yet.
            try:
                txs = await get_transactions_by_date(d, cashier_id=self._user._id)
                self._server_parked_count = sum(
                    1
                    for tx in txs
                    if (getattr(tx, "register_status", "") or "") == "parked"
                )
                self._emit_parked_count()
                count = self._parked_day_count
            except Exception:
                count = len(self._local_parked)
        if count <= 0:
            return True
        label = d.strftime("%d %b %Y")
        plural = "s" if count != 1 else ""
        resp = QMessageBox.warning(
            self,
            "Parked trucks block submit",
            f"{count} parked truck row{plural} on {label} still need a correct "
            "fleet number.\n\n"
            "Open the Parked tab, fix the truck(s), then submit again.\n\n"
            "Open Parked now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if resp == QMessageBox.Yes:
            self.set_view_mode("parked")
        return False

    async def _confirm_day_issues_for_submit(self, d: date) -> bool:
        """Block submit when Day Issues remain, unless the accountant setting allows it."""
        from tahmeed.services.day_issues_service import collect_day_issues
        from tahmeed.services.settings_service import get_setting

        try:
            snap = await collect_day_issues(d)
        except Exception:
            return True
        if snap.total <= 0:
            return True
        allow = False
        try:
            allow = bool(await get_setting("allow_submit_with_day_issues"))
        except Exception:
            allow = False
        label = d.strftime("%d %b %Y")
        summary = (
            f"{snap.total} Day Issue(s) on {label}:\n"
            f"  · {snap.unmapped_count} unmapped\n"
            f"  · {snap.bad_map_count} bad map\n\n"
        )
        if not allow:
            resp = QMessageBox.warning(
                self,
                "Day Issues block submit",
                summary
                + "Map descriptions on the Day Issues tab before submitting.\n\n"
                "Open Day Issues now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if resp == QMessageBox.Yes:
                self.day_issues_requested.emit(d)
            return False
        resp = QMessageBox.question(
            self,
            "Submit with Day Issues?",
            summary
            + "Submit is allowed with issues (accountant setting).\n\n"
            "Submit anyway?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return resp == QMessageBox.Yes

    def refresh(self) -> None:
        if self._current_date is None:
            self._populate([])
        else:
            asyncio.ensure_future(self._load_date(self._current_date))
        from tahmeed.services.catalog_service import catalog_is_stale

        if catalog_is_stale():
            asyncio.ensure_future(self._load_register_catalog(refresh=True))

    def invalidate_after_server_draft_change(self, day: Optional[date] = None) -> None:
        """Drafts/Saved discarded or submitted — drop stale grid / local recovery.

        If this register day is open, reload from Mongo so deleted drafts cannot
        linger as ghost rows. Preserves typed-but-unsaved work via local draft
        restore; clears recovery files when there is nothing to keep.
        """
        if not isinstance(day, date):
            return
        open_here = self._current_date == day
        try:
            if open_here and self.has_unsaved_work():
                # Keep only unsaved editable / dirty cells for restore after reload.
                self._flush_local_draft()
            else:
                # Server drafts changed — local Parked rows were never on the
                # server, so they stay.
                self._clear_day_draft_keep_parked(day, clear_merged=True)
        except Exception:
            pass
        if not open_here:
            return
        self._draft_timer.stop()
        self._dirty_rows.clear()
        self._invalidate_day_tx_cache()
        # Discard removed server rows — don't reinject them from local recovery.
        if not self.has_unsaved_work():
            self._draft_restore_policy = "off"
        else:
            self._draft_restore_policy = "silent"
        asyncio.ensure_future(self._load_date(day))

    def ensure_fresh_day_view(self) -> None:
        """Reload the open day when delete/discard left the grid / cache stale.

        Called when entering the Table tab so My entries cannot keep showing
        rows that already left the server until a Merged flip.
        """
        if self._current_date is None:
            return
        if not self._day_tx_stale and self._day_tx_cache is not None:
            return
        if self._load_in_flight:
            return
        asyncio.ensure_future(self._load_date(self._current_date))

    async def _reload_after_server_row_change(self) -> None:
        """Invalidate + reload the open day after Table delete / trash."""
        day = self._current_date
        if not isinstance(day, date):
            return
        keep_typed = self.has_unsaved_work()
        try:
            if keep_typed:
                self._flush_local_draft()
            else:
                self._clear_day_draft_keep_parked(day, clear_merged=True)
        except Exception:
            pass
        self._draft_timer.stop()
        self._dirty_rows.clear()
        self._invalidate_day_tx_cache()
        self._draft_restore_policy = "silent" if keep_typed else "off"
        await self._load_date(day)

    def note_catalog_return(self) -> None:
        """The user is coming back from Manage Items or fleet."""
        self._catalog_return = True

    def reload_settings(self) -> None:
        """Re-read the register catalog after a catalog write or a return from
        Manage Items / fleet. Day rows still refresh so deletes stay current.
        Description history reloads only when the open day changes.
        """
        from tahmeed.services.catalog_service import catalog_is_stale

        refresh = bool(getattr(self, "_catalog_return", False) or catalog_is_stale())
        self._catalog_return = False
        if refresh or not getattr(self, "_catalog_applied", False):
            asyncio.ensure_future(self._load_register_catalog(refresh=refresh))
        self.ensure_fresh_day_view()

    def update_people(self, names: list) -> None:
        """Refresh Ownership / APR BY suggestion list (free text still allowed)."""
        self._people_names = [str(n).strip().upper() for n in (names or []) if str(n).strip()]

    async def _load_people_names(self) -> None:
        try:
            from tahmeed.services.people_service import get_people_names
            self.update_people(await get_people_names())
        except Exception:
            self._people_names = []

    def update_payees(self, names: list) -> None:
        """Refresh Payee column suggestion list (free text still allowed)."""
        self._payee_names = [str(n).strip().upper() for n in (names or []) if str(n).strip()]

    async def _load_payee_names(self) -> None:
        try:
            from tahmeed.services.payee_service import get_payee_names
            self.update_payees(await get_payee_names())
        except Exception:
            self._payee_names = []

    def _populate(self, transactions: List[Transaction]) -> None:
        # A fresh load always returns the grid to read-only state.
        self._cheque_persist_timer.stop()
        self._edit_mode = False
        self._dirty_rows = set()

        self._table.blockSignals(True)
        self._table.clearContents()
        self._saved_count = len(transactions)
        self._saved_ids   = {}
        self._saved_txs   = {}

        # Merged / Parked are not blank-entry surfaces — only My has a typed pad.
        editable_tail = (
            0 if (self._merged_mode or self._parked_mode) else DEFAULT_EDITABLE_ROWS
        )
        total_rows = self._saved_count + editable_tail
        self._table.setRowCount(total_rows)

        for i, tx in enumerate(transactions):
            self._fill_saved_row(i, tx)
            self._saved_ids[i] = tx._id
            self._saved_txs[i] = tx
            if (getattr(tx, "register_status", "") or "") == "parked":
                meta = dict(self._pending_row_meta.get(i) or {})
                meta["parked"] = True
                self._pending_row_meta[i] = meta

        if editable_tail:
            self._init_editable_rows(self._saved_count, total_rows)
        self._table.blockSignals(False)
        self._renumber()
        self._clear_column_filters()
        self._clear_cut_marquee()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._emit_undo_redo_state()
        self._update_footer()
        self._apply_filters()
        if self._search_text:
            self._scroll_to_first_filter_match()
        self.edit_state_changed.emit(False, 0)
        if (
            not self._search_text
            and self._table.currentRow() < 0
            and self._table.rowCount() > 0
        ):
            focus_row = (
                self._saved_count if editable_tail else max(0, self._saved_count - 1)
            )
            self._table.setCurrentCell(focus_row, COL_DESC)
        self._sync_header_cheque_from_grid()
        self._emit_active_cheque()

        if self._pending_highlight:
            term = self._pending_highlight
            self._pending_highlight = ""
            # Small delay so Qt finishes laying out the rows before we scroll.
            QTimer.singleShot(80, lambda: self.scroll_and_highlight(term))
        self._refresh_truck_required_highlights()

    # ------------------------------------------------------------------
    # Row initialisation helpers
    # ------------------------------------------------------------------

    def _fill_saved_row(self, row: int, tx: Transaction) -> None:
        status = getattr(tx, "register_status", "") or "submitted"
        if status == "draft":
            bg = QBrush(DRAFT_BG)
            status_tip = "Draft — saved but not submitted to Verify"
        elif status == "parked":
            bg = QBrush(DRAFT_BG)
            status_tip = (
                "Parked — fix the truck (table or Fix trucks), then Save "
                "to return to My entries"
            )
        else:
            bg = QBrush(SAVED_BG)
            status_tip = "Submitted — awaiting accountant Verify"
        ro = Qt.ItemIsEnabled | Qt.ItemIsSelectable

        def saved_item(text: str, align=Qt.AlignVCenter | Qt.AlignLeft) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            it.setFlags(ro)
            it.setBackground(bg)
            it.setTextAlignment(align)
            it.setToolTip(status_tip)
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

        tzs_txt, usd_txt = _display_money_cells(tx)
        # Same saved/draft chrome as sibling columns (was _money_item → white).
        money_align = Qt.AlignRight | Qt.AlignVCenter
        self._table.setItem(row, COL_TZS, saved_item(tzs_txt, money_align))
        self._table.setItem(row, COL_USD, saved_item(usd_txt, money_align))

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

    @staticmethod
    def _blank_money_bucket() -> dict:
        return {"n": 0, "tzs": 0.0, "usd": 0.0, "refund": 0.0}

    @staticmethod
    def _add_money_buckets(a: dict, b: dict) -> dict:
        return {
            "n": int(a.get("n") or 0) + int(b.get("n") or 0),
            "tzs": float(a.get("tzs") or 0.0) + float(b.get("tzs") or 0.0),
            "usd": float(a.get("usd") or 0.0) + float(b.get("usd") or 0.0),
            "refund": float(a.get("refund") or 0.0) + float(b.get("refund") or 0.0),
        }

    def _accumulate_raw_money(
        self,
        bucket: dict,
        raw_tzs: str,
        raw_usd: str,
        raw_ref: str,
    ) -> bool:
        """Add one money line into *bucket*. Returns False when the line is skipped."""
        raw_tzs = (raw_tzs or "").strip()
        raw_usd = (raw_usd or "").strip()
        if not raw_tzs and not raw_usd:
            return False
        tzs_amt = _parse_amount_text(raw_tzs) if raw_tzs else 0.0
        usd_amt = _parse_amount_text(raw_usd) if raw_usd else 0.0
        # Skip non-numeric leftovers that parse as 0
        if (
            raw_tzs
            and tzs_amt == 0.0
            and not any(ch.isdigit() for ch in raw_tzs)
            and not raw_usd
        ):
            return False
        if (
            raw_usd
            and usd_amt == 0.0
            and not any(ch.isdigit() for ch in raw_usd)
            and not raw_tzs
        ):
            return False
        bucket["n"] = int(bucket.get("n") or 0) + 1
        bucket["tzs"] = float(bucket.get("tzs") or 0.0) + tzs_amt
        bucket["usd"] = float(bucket.get("usd") or 0.0) + usd_amt
        if _is_refund_float(raw_ref or ""):
            # Refund-to-float stays TZS-only.
            bucket["refund"] = float(bucket.get("refund") or 0.0) + tzs_amt
        return True

    def _money_bucket_from_table(
        self,
        table,
        *,
        saved_count: int,
        saved_txs: dict | None = None,
        current_date=None,
    ) -> dict:
        """Sum entries / TZS / USD / refund from one register table surface."""
        bucket = self._blank_money_bucket()
        if table is None:
            return bucket
        saved_txs = saved_txs if saved_txs is not None else {}
        # Multiset of saved-row fingerprints so editable copies of already-saved
        # expenses (partial-save / bad restore) do not inflate money totals.
        saved_fps: dict = {}
        for row in range(int(saved_count or 0)):
            tx = saved_txs.get(row)
            if tx is not None:
                fp = self._expense_fingerprint_for_tx(tx)
            else:
                fp = self._expense_fingerprint_for_table_row(
                    table, row, current_date=current_date
                )
            if not fingerprint_is_meaningful(fp):
                continue
            saved_fps[fp] = saved_fps.get(fp, 0) + 1
        for row in range(table.rowCount()):
            tzs_it = table.item(row, COL_TZS)
            usd_it = table.item(row, COL_USD)
            raw_tzs = tzs_it.text().strip() if tzs_it else ""
            raw_usd = usd_it.text().strip() if usd_it else ""
            if row >= int(saved_count or 0):
                fp = self._expense_fingerprint_for_table_row(
                    table, row, current_date=current_date
                )
                if claim_fingerprint(saved_fps, fp):
                    continue
            ref_it = table.item(row, COL_REF)
            raw_ref = ref_it.text() if ref_it else ""
            self._accumulate_raw_money(bucket, raw_tzs, raw_usd, raw_ref)
        return bucket

    def _expense_fingerprint_for_table_row(
        self, table, row: int, *, current_date=None
    ):
        """Content fingerprint for a row on any register table surface."""
        def txt(col: int) -> str:
            it = table.item(row, col)
            return it.text().strip() if it else ""

        day = current_date if current_date is not None else self._current_date
        year = day.year if day else date.today().year
        raw_date = txt(COL_DATE)
        parsed = _parse_optional_date(raw_date, default_year=year) if raw_date else None
        if parsed is not None:
            row_date = parsed.date().isoformat()
        elif day is not None:
            row_date = day.isoformat()
        else:
            row_date = ""
        tzs_raw = txt(COL_TZS)
        usd_raw = txt(COL_USD)
        return expense_content_fingerprint(
            description=txt(COL_DESC),
            truck_number=txt(COL_TRUCK),
            item=txt(COL_ITEM),
            tzs=_parse_amount_text(tzs_raw) if tzs_raw else 0.0,
            usd=_parse_amount_text(usd_raw) if usd_raw else 0.0,
            row_date_iso=row_date,
        )

    def _money_bucket_from_txs(self, txs) -> dict:
        bucket = self._blank_money_bucket()
        for tx in txs or []:
            try:
                tzs_amt, usd_amt = tx.money_parts()
            except Exception:
                tzs_amt = float(getattr(tx, "amount", 0) or 0)
                usd_amt = float(getattr(tx, "amount_usd", 0) or 0)
            if not tzs_amt and not usd_amt:
                continue
            bucket["n"] = int(bucket["n"]) + 1
            bucket["tzs"] = float(bucket["tzs"]) + float(tzs_amt or 0.0)
            bucket["usd"] = float(bucket["usd"]) + float(usd_amt or 0.0)
            ref = getattr(tx, "ref_float", None) or ""
            if _is_refund_float(ref) or bool(getattr(tx, "notes_flag", False)):
                bucket["refund"] = float(bucket["refund"]) + float(tzs_amt or 0.0)
        return bucket

    def _money_bucket_from_local_parked(self, entries) -> dict:
        bucket = self._blank_money_bucket()
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            values = cells_from_json(entry.get("cells"))
            raw_tzs = (values.get(COL_TZS) or "").strip()
            raw_usd = (values.get(COL_USD) or "").strip()
            raw_ref = (values.get(COL_REF) or "").strip()
            self._accumulate_raw_money(bucket, raw_tzs, raw_usd, raw_ref)
        return bucket

    def _server_parked_txs_for_totals(self) -> list:
        """Parked server txs for the open day (own cashier unless Merged)."""
        txs = list(self._day_tx_cache or [])
        parked = [
            tx for tx in txs
            if (getattr(tx, "register_status", "") or "") == "parked"
        ]
        if self._merged_mode:
            return parked
        uid = self._user._id
        return [
            tx for tx in parked
            if tx.cashier_id == uid or str(tx.cashier_id) == str(uid)
        ]

    def _my_money_bucket(self) -> dict:
        """Totals for non-parked My entries (live My table or keep-alive session)."""
        if self._parked_mode and self._my_table is not None and self._my_session:
            session = self._my_session
            return self._money_bucket_from_table(
                self._my_table,
                saved_count=int(session.get("saved_count") or 0),
                saved_txs=session.get("saved_txs") or {},
                current_date=self._current_date,
            )
        if self._parked_mode:
            # Parked open without a keep-alive My session — use day cache drafts.
            txs = [
                tx for tx in (self._day_tx_cache or [])
                if (getattr(tx, "register_status", "") or "") != "parked"
            ]
            if not self._merged_mode:
                uid = self._user._id
                txs = [
                    tx for tx in txs
                    if tx.cashier_id == uid or str(tx.cashier_id) == str(uid)
                ]
            return self._money_bucket_from_txs(txs)
        # Active surface is My (or Merged): live grid is the source of truth.
        return self._money_bucket_from_table(
            self._table,
            saved_count=self._saved_count,
            saved_txs=self._saved_txs,
            current_date=self._current_date,
        )

    def _parked_money_bucket(self) -> dict:
        """Totals for parked follow-up rows (server + local bucket / Parked grid)."""
        if self._parked_mode:
            return self._money_bucket_from_table(
                self._table,
                saved_count=self._saved_count,
                saved_txs=self._saved_txs,
                current_date=self._current_date,
            )
        # Prefer the mounted Parked table when it already holds rows (edits stay live).
        parked_table = self._parked_table
        if parked_table is not None and parked_table.rowCount() > 0:
            # Maps were reset when leaving Parked; scan cells without fingerprint skip.
            live = self._money_bucket_from_table(
                parked_table,
                saved_count=0,
                saved_txs={},
                current_date=self._current_date,
            )
            if live["n"]:
                return live
        server = self._money_bucket_from_txs(self._server_parked_txs_for_totals())
        local = self._money_bucket_from_local_parked(self._local_parked)
        return self._add_money_buckets(server, local)

    def _update_footer(self) -> None:
        """Recompute My / Parked / Combined money KPIs and the grid footer strip."""
        my = self._my_money_bucket()
        parked = self._parked_money_bucket()
        combined = self._add_money_buckets(my, parked)

        # Footer strip stays scoped to the visible surface (current tab).
        if self._parked_mode:
            view = parked
        else:
            view = my
        amount_str = f"TZS {view['tzs']:,.0f}" if view["tzs"] else "—"
        usd_str = f"USD {view['usd']:,.2f}" if view["usd"] else "—"
        self._totals_label.setText(
            f"{view['n']} entr{'y' if view['n'] == 1 else 'ies'}"
            f"   ·   {usd_str}   ·   {amount_str}"
        )
        self.stats_updated.emit(
            int(combined["n"]),
            float(my["tzs"]),
            float(my["usd"]),
            float(my["refund"]),
            self._current_date,
            float(parked["tzs"]),
            float(parked["usd"]),
            float(parked["refund"]),
        )
        draft_n = submitted_n = 0
        # Draft badge counts saved rows on the active surface.
        for row in range(self._saved_count):
            tx = self._saved_txs.get(row)
            if tx is None:
                continue
            if (getattr(tx, "register_status", "") or "submitted") == "draft":
                draft_n += 1
            else:
                submitted_n += 1
        self.register_status_updated.emit(draft_n, submitted_n)

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
        day = self._current_date or date.today()
        tx_date = _parse_optional_date(date_str, default_year=day.year)
        if tx_date is None:
            tx_date = datetime(day.year, day.month, day.day)

        raw_tzs = txt(COL_TZS)
        raw_usd = txt(COL_USD)
        amount, amount_usd, currency = pack_money(
            _parse_optional_amount_text(raw_tzs),
            _parse_optional_amount_text(raw_usd),
        )

        rcpt_status = txt(COL_RECEIPT).strip()

        item_name = txt(COL_ITEM)
        meta = self._pending_row_meta.get(row) or {}
        # Save-as-draft allows blank Item/Payee so rows land in Drafts + Day Issues.
        # Submit-for-Verify still gated by Day Issues. Entry form may still require
        # Item unless defer_item_to_verify is on.
        cat = self._cat_by_name.get(item_name.lower()) if item_name else None
        if cat is not None:
            item_name = cat.name.upper()
        elif item_name and self._restrict_items:
            raise ValueError(f'"{item_name}" is not a known item.')
        elif item_name:
            item_name = item_name.upper()

        payee_name = txt(COL_PAYEE).strip()
        if payee_name and self._restrict_payees:
            known_payee = next(
                (p for p in self._payee_names if p.upper() == payee_name.upper()),
                None,
            )
            if known_payee is None and self._payee_names:
                raise ValueError(f'"{payee_name}" is not a known payee.')
            if known_payee is not None:
                payee_name = known_payee.upper()
            else:
                payee_name = payee_name.upper()
        else:
            payee_name = payee_name.upper()

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
        parking = bool(meta.get("parked")) or self._parked_mode
        accept_empty = bool(meta.get("accept_empty_truck"))
        if (
            cat is not None
            and getattr(cat, "requires_truck", True)
            and not truck_raw
            and not parking
            and not accept_empty
        ):
            raise ValueError(
                f'Truck number is required for item "{item_name}".'
            )
        truck_number = ""
        truck_ok = True
        reg_status = "draft"
        if truck_raw:
            allowed_anyway = self._truck_allow_anyway.get(row)
            if (
                allowed_anyway
                and self._truck_key(truck_raw) == self._truck_key(allowed_anyway)
            ):
                truck_number = allowed_anyway
            elif is_allowed_place_label(truck_raw, self._allowed_truck_labels):
                truck_number = normalize_place_label(truck_raw)
            else:
                matched = try_match_fleet(truck_raw, self._fleet_numbers)
                if matched is None:
                    norm = normalize_truck_number(
                        truck_raw, allowed_labels=self._allowed_truck_labels
                    )
                    label = norm.value if norm.status != "empty" else truck_raw
                    if norm.status == "place_label":
                        truck_number = norm.value
                    elif parking:
                        # Parked follow-up: keep the value even when not in fleet.
                        truck_number = (
                            norm.value
                            if norm.status in ("ok", "normalized", "invalid")
                            else " ".join(truck_raw.upper().split())
                        )
                        truck_ok = False
                    elif norm.status == "invalid":
                        raise ValueError(
                            f'"{label}" is not a valid truck number '
                            f"(expected T + number + space + suffix, e.g. T688 EAF)."
                        )
                    else:
                        raise ValueError(
                            f'"{norm.value}" is not a registered fleet vehicle.'
                        )
                else:
                    truck_number = matched
            it_truck = self._table.item(row, COL_TRUCK)
            if it_truck and truck_number and it_truck.text() != truck_number:
                self._table.blockSignals(True)
                it_truck.setText(truck_number)
                self._table.blockSignals(False)
        # Parked while truck still bad; promote to draft once the truck is valid.
        if meta.get("parked") and not truck_ok:
            reg_status = "parked"
        elif self._parked_mode and not truck_ok:
            reg_status = "parked"
        elif meta.get("parked") and not truck_raw and not accept_empty:
            reg_status = "parked"
        else:
            reg_status = "draft"
            if meta.get("parked") and (truck_ok or accept_empty):
                meta.pop("parked", None)
                self._pending_row_meta[row] = meta

        ref_text = txt(COL_REF).upper()
        orig = self._saved_txs.get(row)
        primary_dt = self._resolve_import_primary_date(row)
        primary_day = primary_dt.date() if primary_dt else self._current_date
        tx_day = tx_date.date() if hasattr(tx_date, "date") else tx_date
        date_discrep = bool(meta.get("date_discrepancy")) or (tx_day != primary_day)
        try:
            _meta_day_order = (
                int(meta["day_order"])
                if meta.get("day_order") is not None
                else row
            )
        except (TypeError, ValueError):
            _meta_day_order = row
        return Transaction(
            date=tx_date,
            description=description,
            item=item_name,
            category_name=item_name or None,
            category_id=meta.get("category_id"),
            truck_number=truck_number,
            amount=amount,
            currency=currency,
            amount_usd=amount_usd,
            memo=txt(COL_MEMO).upper(),
            receipt_status=rcpt_status,
            ref_float=ref_text,
            notes_flag=_is_refund_float(ref_text),
            ownership=txt(COL_OWN).upper(),
            approver=txt(COL_APR).upper(),
            payee=payee_name,
            cheque=txt(COL_CHEQUE).upper(),
            cashier_id=self._user._id,
            day_order=_meta_day_order,
            possible_duplicate=bool(meta.get("possible_duplicate")),
            duplicate_of_id=(
                ObjectId(meta["duplicate_of_id"])
                if meta.get("duplicate_of_id")
                and ObjectId.is_valid(str(meta.get("duplicate_of_id")))
                else None
            ),
            register_status=reg_status,
            daily_import_id=meta.get("daily_import_id"),
            daily_import_source=meta.get("daily_import_source"),
            date_discrepancy=date_discrep,
            import_primary_date=primary_dt,
            lpo_do=(meta.get("lpo_do") or "").upper(),
            do_number=(meta.get("do_number") or "").upper(),
            reported_date=getattr(orig, "reported_date", None) if orig is not None else None,
            client_row_id=self._ensure_row_client_id(row) or None,
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
        self._emit_active_cheque()
        self._refresh_truck_required_highlights()

    def _exit_edit_mode(self, discard: bool) -> None:
        """Leave edit mode. When discard is True the date is reloaded so the grid
        reverts to the stored values; otherwise the caller reloads after saving."""
        if discard:
            # Keep typed new rows in the local draft; drop dirty saved-row edits.
            self._flush_local_draft(include_dirty=False)
        self._reset_edit_state()
        if discard:
            if self._current_date is None:
                return
            asyncio.ensure_future(self._load_date(self._current_date))

    def _reset_edit_state(self) -> None:
        self._edit_mode = False
        self._dirty_rows = set()
        self.edit_state_changed.emit(False, 0)
        self._emit_active_cheque()

    def _emit_active_cheque(self) -> None:
        """Push the day-level Cheque stamp into the Table header field."""
        self.active_cheque_changed.emit(self._header_cheque, True)

    def _sync_header_cheque_from_grid(self) -> None:
        """Refresh day-level header Cheque from rows that already have data."""
        rows = self._data_rows()
        if not rows:
            self._header_cheque = ""
            return
        cheques = {self._cell_text(r, COL_CHEQUE) for r in rows}
        # Shared value when unanimous; blank when the day has mixed stamps.
        self._header_cheque = next(iter(cheques)) if len(cheques) == 1 else ""

    def set_active_cheque(self, text: str) -> None:
        """Stamp Cheque from the header onto every saved + filled unsaved row."""
        self._header_cheque = (text or "").upper()
        self._stamp_cheque_on_data_rows()
        self._schedule_cheque_persist()
        self._schedule_draft_autosave()

    def _stamp_cheque_on_data_rows(self) -> None:
        """Write header Cheque onto all data rows without dirtying saved rows."""
        rows = self._data_rows()
        if not rows:
            return
        prev = self._table.blockSignals(True)
        try:
            for row in rows:
                self._write_cheque_cell(row, self._header_cheque)
        finally:
            self._table.blockSignals(prev)

    def _write_cheque_cell(self, row: int, value: str) -> None:
        """Set Cheque cell, preserving row chrome (saved / edit / new)."""
        col = COL_CHEQUE
        it = self._table.item(row, col)
        if it is None:
            it = QTableWidgetItem("")
            flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            if row >= self._saved_count or self._edit_mode:
                flags |= Qt.ItemIsEditable
            it.setFlags(flags)
            if row >= self._saved_count:
                it.setBackground(QBrush(NEW_BG))
            elif self._edit_mode:
                bg = DIRTY_BG if row in self._dirty_rows else EDIT_BG
                it.setBackground(QBrush(bg))
            else:
                it.setBackground(QBrush(SAVED_BG))
            self._table.setItem(row, col, it)
        if it.text() != value:
            it.setText(value)

    def _schedule_cheque_persist(self) -> None:
        """Debounce soft DB updates for saved rows (no Verify → Edited)."""
        if self._saved_count <= 0:
            return
        self._cheque_persist_timer.start()

    def _kick_persist_header_cheque(self) -> None:
        asyncio.ensure_future(self._persist_header_cheque())

    async def _persist_header_cheque(self) -> None:
        """Persist day-level Cheque on saved rows without re-verify flags."""
        target_date = self._current_date
        cheque = self._header_cheque
        snapshot = [
            (row, self._saved_ids.get(row), self._saved_txs.get(row))
            for row in range(self._saved_count)
        ]
        for _row, tx_id, tx in snapshot:
            if self._current_date != target_date:
                return
            if tx_id is None:
                continue
            cur_cheque = (getattr(tx, "cheque", "") or "") if tx is not None else ""
            if cur_cheque == cheque:
                continue
            try:
                await update_transaction(tx_id, {"cheque": cheque})
            except Exception:
                continue
            if tx is not None:
                tx.cheque = cheque

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
        self._update_truck_required_highlight(row)

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
            "amount_usd": tx.amount_usd,
            "memo": tx.memo,
            "receipt_status": tx.receipt_status,
            "notes_flag": tx.notes_flag,
            "ref_float": tx.ref_float,
            "ownership": tx.ownership,
            "approver": tx.approver,
            "payee": tx.payee,
            "cheque": tx.cheque,
            "import_primary_date": tx.import_primary_date,
            "register_status": tx.register_status or "draft",
        }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    _EXPORT_COLS = [
        COL_DATE, COL_ITEM, COL_DESC, COL_TRUCK, COL_MEMO,
        COL_REF, COL_TZS, COL_USD, COL_RECEIPT, COL_OWN, COL_APR, COL_PAYEE, COL_CHEQUE,
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
        date_slug = (
            self._current_date.isoformat()
            if isinstance(self._current_date, date)
            else "new"
        )
        default_name = f"register_{date_slug}{ext}"
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
        rows = self._apply_export_restriction(rows, fmt)
        if not rows:
            QMessageBox.information(self, "Nothing to export",
                                    "All visible rows belong to items restricted "
                                    f"from {fmt.upper()} export.")
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

    def _apply_export_restriction(self, rows: List[list], fmt: str) -> List[list]:
        from tahmeed.services.export_restriction_service import (
            filter_register_rows,
            restricted_names_from_categories,
        )

        if "cashier_register" not in getattr(self, "_export_restrict_surfaces", set()):
            return rows
        export_fmt = "pdf" if fmt == "pdf" else "excel"
        restricted = restricted_names_from_categories(self._categories, export_fmt)
        if not restricted:
            return rows
        item_idx = self._EXPORT_COLS.index(COL_ITEM)
        return filter_register_rows(rows, item_col_index=item_idx, restricted=restricted)

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
        # Keep matches in view: blank editable rows no longer bury results,
        # and Clear must immediately restore the full grid.
        if self._search_text:
            self._scroll_to_first_filter_match()
        self._table.viewport().update()

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
        if not self._column_filters_enabled:
            return
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

    def column_filters_enabled(self) -> bool:
        return bool(self._column_filters_enabled)

    def set_column_filters_enabled(self, enabled: bool) -> None:
        """Excel-style AutoFilter: on shows ▾; off clears filters and hides ▾."""
        enabled = bool(enabled)
        if enabled == self._column_filters_enabled:
            return
        self._column_filters_enabled = enabled
        hdr = self._table.horizontalHeader()
        if isinstance(hdr, _FilterHeaderView):
            hdr.set_filters_enabled(enabled)
        if not enabled:
            self._clear_column_filters()
            self._apply_filters()
        else:
            self._sync_filter_header()
        self.column_filters_state_changed.emit(enabled)

    def toolbar_toggle_column_filters(self) -> None:
        """Toolbar Filter button — toggle AutoFilter on/off."""
        self.set_column_filters_enabled(not self._column_filters_enabled)

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

    def _row_matches_search(self, row: int, search: str) -> bool:
        if not search:
            return True
        for col in range(self._table.columnCount()):
            it = self._table.item(row, col)
            if not it:
                continue
            if col == COL_RECEIPT:
                label = _RCPT_LABEL.get(it.text().strip().lower(), it.text())
                if search in label.lower() or search in it.text().lower():
                    return True
            elif search in it.text().lower():
                return True
        return False

    def _apply_filters(self) -> None:
        search = self._search_text
        for row in range(self._table.rowCount()):
            # Blank entry rows: keep visible for typing when not searching.
            # While searching, hide them so matches aren't buried under empties.
            if row >= self._saved_count and not self._row_has_data(row):
                self._table.setRowHidden(row, bool(search))
                continue

            # ── Search ───────────────────────────────────────────────
            if search and not self._row_matches_search(row, search):
                self._table.setRowHidden(row, True)
                continue

            # ── Column filters ───────────────────────────────────────
            visible = True
            if self._column_filters_enabled:
                for col, accepted in self._col_filters.items():
                    if not accepted:
                        continue
                    if self._cell_filter_value(row, col) not in accepted:
                        visible = False
                        break

            self._table.setRowHidden(row, not visible)

    def _scroll_to_first_filter_match(self) -> None:
        """Scroll to the first visible saved/data row after a search apply."""
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            if row >= self._saved_count and not self._row_has_data(row):
                continue
            index = self._table.model().index(row, COL_DESC)
            self._table.scrollTo(index, QAbstractItemView.PositionAtTop)
            if self._table.currentRow() < 0 or self._table.isRowHidden(
                self._table.currentRow()
            ):
                self._table.setCurrentCell(row, COL_DESC)
            return

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

    def _sync_row_header_labels(self) -> None:
        sync_row_header_labels(self._table)

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
        self._sync_row_header_labels()
        # Keep header KPIs (entries / refund / total) in sync after row ops
        # that block itemChanged (delete, clear, paste, import).
        self._update_footer()

    # ------------------------------------------------------------------
    # Dynamic row expansion
    # ------------------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        col = item.column()
        if col in self._wrapped_cols:
            self._schedule_wrap_height_refresh()

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
            if col == COL_ITEM:
                self._validate_item_cell(row, item)
            elif col == COL_PAYEE:
                self._validate_payee_cell(row, item)
            elif col == COL_DESC and item.text().strip():
                self._validate_locked_description(row, item)
            elif col == COL_TRUCK and item.text().strip():
                self._validate_truck_cell(row, item)
            if col in (COL_ITEM, COL_DESC, COL_TRUCK):
                self._update_truck_required_highlight(row)
            if col == COL_TZS or col == COL_USD:
                self._update_footer()
            if col in (COL_ITEM, COL_PAYEE, COL_DESC):
                self._notify_day_issues_source_changed()
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

        # Item / Payee / Description / Truck validation (canonicalise, restrict, locked)
        if col == COL_ITEM:
            self._validate_item_cell(row, item)
        elif col == COL_PAYEE:
            self._validate_payee_cell(row, item)
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
        if col in (COL_ITEM, COL_DESC, COL_TRUCK):
            self._update_truck_required_highlight(row)

        # Dynamic row expansion near the bottom
        if row >= self._table.rowCount() - 5 and item.text().strip():
            self._append_editable_rows(10)

        if col in (COL_TZS, COL_USD, COL_REF):
            self._update_footer()

        self._schedule_draft_autosave()
        if col in (COL_ITEM, COL_PAYEE, COL_DESC):
            self._notify_day_issues_source_changed()

    def _on_model_data_changed(self, top_left, bottom_right, roles=()) -> None:
        # Kept for receipt/other UserRole updates if added later.
        pass

    def _ensure_row_client_id(self, row: int) -> str:
        """Stable UUID for an unsaved editable row (first edit / import / save)."""
        if row < self._saved_count:
            return ""
        meta = self._pending_row_meta.get(row) or {}
        rid = str(meta.get("client_row_id") or "").strip()
        if rid:
            return rid
        rid = new_client_row_id()
        meta = dict(meta)
        meta["client_row_id"] = rid
        self._pending_row_meta[row] = meta
        return rid

    def _activate_row(self, row: int) -> None:
        """Make a blank editable row visible: set S/NO number and create input items."""
        if row < self._saved_count:
            return
        self._ensure_row_client_id(row)
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
        # Inherit the day-level Cheque stamp onto newly started rows.
        if self._header_cheque:
            self._write_cheque_cell(row, self._header_cheque)
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

    def _register_primary_dt(self) -> datetime:
        """Register calendar day as datetime (same shape as daily-import primary)."""
        d = self._current_date or date.today()
        return datetime(d.year, d.month, d.day)

    def _stamp_empty_row_dates(self) -> None:
        """Fill blank Date cells on typed rows after Reconciled Date is chosen."""
        if not isinstance(self._current_date, date):
            return
        stamp = self._register_date_str()
        if not stamp:
            return
        prev = self._table.blockSignals(True)
        try:
            for row in range(self._saved_count, self._table.rowCount()):
                if not self._row_has_data(row):
                    continue
                date_it = self._table.item(row, COL_DATE)
                if date_it is not None and date_it.text().strip():
                    continue
                new_it = QTableWidgetItem(stamp)
                new_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                self._table.setItem(row, COL_DATE, new_it)
        finally:
            self._table.blockSignals(prev)

    def _resolve_import_primary_date(self, row: int):
        """Register day ownership for this row (upload meta, existing stamp, or open day).

        Prior Excel/system dates stay on the open register day — same as uploads.
        """
        meta = self._pending_row_meta.get(row) or {}
        stamped = meta.get("import_primary_date")
        if stamped is not None:
            return stamped
        orig = self._saved_txs.get(row)
        if orig is not None and getattr(orig, "import_primary_date", None) is not None:
            return orig.import_primary_date
        return self._register_primary_dt()

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
        # Parked / Merged are follow-up browsers — never grow an entry pad there.
        if self._merged_mode or self._parked_mode:
            return
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

        if key == Qt.Key_Escape and self._outbox_drain_in_flight:
            if self.cancel_outbox_sync():
                return

        if mod == Qt.ControlModifier:
            if key == Qt.Key_C:    self._copy();                               return
            if key == Qt.Key_X:    self._cut();                                return
            if key == Qt.Key_V:    self._paste();                              return
            if key == Qt.Key_Z:
                if mod & Qt.ShiftModifier:
                    self._redo()
                else:
                    self._undo()
                return
            if key in (Qt.Key_Y,):
                self._redo()
                return
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
            sel_rows = sorted({i.row() for i in self._table.selectedIndexes()})
            if sel_rows and self._selection_is_full_rows(sel_rows):
                saved = [r for r in sel_rows if r < self._saved_count]
                owned = self._owned_saved_rows(saved)
                if owned:
                    self._delete_saved_rows(owned)
                elif saved:
                    QMessageBox.information(
                        self,
                        "Delete",
                        "You can only delete your own register entries.",
                    )
                # Unsaved full rows still use the existing row-remove path.
                if any(r >= self._saved_count for r in sel_rows):
                    self._delete_rows()
                return
            self._clear_selected()
            return

        if key == Qt.Key_Tab:
            self._commit_date_suggestion()
            self._tab_forward(); return

        if key == Qt.Key_Backtab:
            self._step(0, -1, skip=CHECK_COLS | READONLY_COLS); return

        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._commit_date_suggestion()
            self._step(+1, 0); return

        QTableWidget.keyPressEvent(self._table, event)

    def _tab_skips_col(self, col: int, skip: set) -> bool:
        """True if Tab/Backtab should not land on this column."""
        return col in skip or self._table.isColumnHidden(col)

    def _tab_forward(self) -> None:
        """Advance Tab: skip readonly/hidden cols, wrap to next row at last column."""
        row, col = self._table.currentRow(), self._table.currentColumn()
        skip = READONLY_COLS
        next_col = col + 1
        while next_col < self._table.columnCount() and self._tab_skips_col(next_col, skip):
            next_col += 1
        if next_col >= self._table.columnCount():
            next_row = row + 1
            if next_row >= self._table.rowCount():
                self._append_editable_rows(10)
            first_col = 0
            while first_col < self._table.columnCount() and self._tab_skips_col(first_col, skip):
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
        vh = self._table.verticalHeader()
        if vh is not None:
            vh.installEventFilter(self._key_filter)

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
                if not self._tab_skips_col(new_col, skip):
                    break
                new_col += dc
        new_row = max(0, min(new_row, self._table.rowCount() - 1))
        new_col = max(0, min(new_col, self._table.columnCount() - 1))
        self._table.setCurrentCell(new_row, new_col)

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Undo / redo (Excel-style)
    # ------------------------------------------------------------------

    def _normalize_cell_val(self, val) -> tuple:
        if isinstance(val, tuple) and len(val) >= 2:
            return val
        return ("text", "" if val is None else str(val))

    def _serialize_cell(self, row: int, col: int) -> tuple:
        if row < 0 or row >= self._table.rowCount() or col == COL_SNO:
            return ("text", "")
        it = self._table.item(row, col)
        if col in CHECK_COLS:
            checked = bool(it.data(Qt.UserRole)) if it else False
            return ("check", checked)
        return ("text", it.text() if it else "")

    def _cell_editable(self, row: int, col: int) -> bool:
        if col in READONLY_COLS or col == COL_SNO:
            return False
        # Merged has no entry surface below the saved block.
        if self._merged_mode and row >= self._saved_count:
            return False
        if row < self._saved_count and not self._edit_mode:
            return False
        return True

    def _write_cell_value(self, row: int, col: int, val: tuple) -> None:
        kind, payload = self._normalize_cell_val(val)
        if col in CHECK_COLS or kind == "check":
            it = self._table.item(row, col) or QTableWidgetItem()
            it.setData(Qt.UserRole, bool(payload))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._table.setItem(row, col, it)
            return
        text = str(payload)
        if col == COL_RECEIPT:
            it = QTableWidgetItem(_receipt_paste_value(text))
        elif col in (COL_TZS, COL_USD):
            it = _amount_item_from_raw(text)
        elif col == COL_CASHIER:
            it = QTableWidgetItem(text)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        else:
            it = QTableWidgetItem(_upper_text(col, text))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        self._table.setItem(row, col, it)

    def _normalize_undo_entry(self, entry: dict) -> dict:
        if "cells" in entry or "row_ops" in entry:
            return entry
        cells = {
            (int(r), int(c)): self._normalize_cell_val(v)
            for (r, c), v in entry.items()
        }
        return {"cells": cells}

    def _record_undo(self, entry: dict) -> None:
        normalized = self._normalize_undo_entry(entry)
        if not normalized.get("cells") and not normalized.get("row_ops"):
            return
        self._undo_stack.append(normalized)
        self._redo_stack.clear()
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack = self._undo_stack[-self._undo_limit:]
        self._emit_undo_redo_state()

    def _push_undo_cells(self, cells: dict) -> None:
        """Snapshot cell values before a mutating edit."""
        if not cells:
            return
        mapped = {}
        for (row, col), val in cells.items():
            if isinstance(val, tuple):
                mapped[(row, col)] = val
            else:
                mapped[(row, col)] = self._normalize_cell_val(val)
        self._record_undo({"cells": mapped})

    def _snapshot_selection(self) -> dict:
        snap = {}
        for it in self._table.selectedItems():
            if it.column() in READONLY_COLS:
                continue
            snap[(it.row(), it.column())] = self._serialize_cell(it.row(), it.column())
        return snap

    def _snapshot_rows(self, rows: list) -> dict:
        snap = {}
        for row in rows:
            snap.update(self._snapshot_row_cells(row))
        return snap

    def _snapshot_row_cells(self, row: int) -> dict:
        """Full row snapshot including S/N for undo of row activation."""
        snap = {}
        for col in range(self._table.columnCount()):
            if col == COL_SNO:
                sno_it = self._table.item(row, COL_SNO)
                snap[(row, col)] = ("text", sno_it.text() if sno_it else "")
            else:
                snap[(row, col)] = self._serialize_cell(row, col)
        return snap

    def _snapshot_cells_for_keys(self, keys) -> dict:
        return {(r, c): self._serialize_cell(r, c) for (r, c) in keys}

    def _apply_cells(self, cells: dict) -> None:
        if not cells:
            return
        self._undo_redo_active = True
        self._table.blockSignals(True)
        try:
            touched_rows: set = set()
            for (row, col), val in cells.items():
                if row < 0 or row >= self._table.rowCount():
                    continue
                if col == COL_SNO:
                    if row >= self._saved_count:
                        sno_it = self._table.item(row, COL_SNO)
                        if sno_it is not None:
                            kind, payload = self._normalize_cell_val(val)
                            sno_it.setText(str(payload))
                            touched_rows.add(row)
                    continue
                if not self._cell_editable(row, col) and row < self._saved_count:
                    continue
                self._write_cell_value(row, col, val)
                touched_rows.add(row)
                if row < self._saved_count and self._edit_mode:
                    self._dirty_rows.add(row)
            for row in touched_rows:
                self._sync_row_date(row)
        finally:
            self._table.blockSignals(False)
            self._undo_redo_active = False
        self._renumber()
        self._update_footer()
        self._table.viewport().update()
        self._schedule_draft_autosave()
        self._flush_day_issues_source_changed(force=True)

    def _apply_row_ops(self, ops: list, *, forward: bool) -> None:
        """Apply row insert/remove ops. ``forward=True`` runs as recorded; False inverts."""
        if not ops:
            return
        self._undo_redo_active = True
        self._bulk_mutating = True
        self._table.blockSignals(True)
        try:
            removes = [o for o in ops if o.get("op") == "remove"]
            inserts = [o for o in ops if o.get("op") == "insert"]
            if forward:
                for op in sorted(removes, key=lambda o: int(o.get("at", 0)), reverse=True):
                    at = int(op.get("at", 0))
                    if at < self._table.rowCount():
                        self._shift_row_maps_on_remove(at)
                        self._table.removeRow(at)
                for op in inserts:
                    at = int(op.get("at", 0))
                    count = int(op.get("count") or 1)
                    for _ in range(count):
                        self._shift_row_maps_on_insert(at)
                        self._table.insertRow(at)
                        self._init_editable_rows(at, at + 1)
            else:
                for op in sorted(removes, key=lambda o: int(o.get("at", 0))):
                    at = int(op.get("at", 0))
                    values = dict(op.get("values") or {})
                    meta = dict(op.get("meta") or {})
                    self._shift_row_maps_on_insert(at)
                    self._table.insertRow(at)
                    self._init_editable_rows(at, at + 1)
                    truck_cells = self._write_row_values(at, values)
                    self._restore_moved_row_meta(at, meta)
                    self._sync_row_date(at)
                    self._finalize_truck_cells(truck_cells)
                for op in reversed(inserts):
                    at = int(op.get("at", 0))
                    count = int(op.get("count") or 1)
                    for _ in range(count):
                        if at < self._table.rowCount():
                            self._shift_row_maps_on_remove(at)
                            self._table.removeRow(at)
            min_rows = self._saved_count + DEFAULT_EDITABLE_ROWS
            if self._table.rowCount() < min_rows:
                start = self._table.rowCount()
                self._table.setRowCount(min_rows)
                self._init_editable_rows(start, min_rows)
        finally:
            self._table.blockSignals(False)
            self._bulk_mutating = False
            self._undo_redo_active = False
        self._renumber()
        self._update_footer()
        self._schedule_draft_autosave()
        self._flush_day_issues_source_changed(force=True)

    def _invert_undo_entry(self, entry: dict) -> dict:
        entry = self._normalize_undo_entry(entry)
        inverted: dict = {}
        cells = entry.get("cells") or {}
        if cells:
            inverted["cells"] = self._snapshot_cells_for_keys(cells.keys())
        ops = entry.get("row_ops") or []
        if ops:
            inv_ops = []
            for op in reversed(ops):
                action = op.get("op")
                if action == "insert":
                    inv_ops.append({
                        "op": "insert",
                        "at": int(op.get("at", 0)),
                        "count": int(op.get("count") or 1),
                    })
                elif action == "remove":
                    inv_ops.append({"op": "remove", "at": int(op.get("at", 0))})
            inverted["row_ops"] = inv_ops
        return inverted

    def _apply_undo_entry(self, entry: dict, *, forward: bool) -> None:
        entry = self._normalize_undo_entry(entry)
        if not forward:
            ops = entry.get("row_ops") or []
            if ops:
                self._apply_row_ops(ops, forward=False)
            cells = entry.get("cells") or {}
            if cells:
                self._apply_cells(cells)
            return
        cells = entry.get("cells") or {}
        if cells:
            self._apply_cells(cells)
        ops = entry.get("row_ops") or []
        if ops:
            self._apply_row_ops(ops, forward=True)

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        self._flush_pending_row_edits()
        self._clear_cut_marquee()
        entry = self._undo_stack.pop()
        redo_entry = self._invert_undo_entry(entry)
        self._apply_undo_entry(entry, forward=False)
        self._redo_stack.append(redo_entry)
        if len(self._redo_stack) > self._undo_limit:
            self._redo_stack = self._redo_stack[-self._undo_limit:]
        self._emit_undo_redo_state()

    def _redo(self) -> None:
        if not self._redo_stack:
            return
        self._flush_pending_row_edits()
        self._clear_cut_marquee()
        entry = self._redo_stack.pop()
        undo_entry = self._invert_undo_entry(entry)
        self._apply_undo_entry(entry, forward=True)
        self._undo_stack.append(undo_entry)
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack = self._undo_stack[-self._undo_limit:]
        self._emit_undo_redo_state()

    def _on_current_cell_changed(
        self, row: int, col: int, prev_row: int, prev_col: int,
    ) -> None:
        if prev_row >= 0 and prev_col >= 0:
            self._begin_closing_row_edit()
        if row >= 0 and col >= 0 and self._cell_editable(row, col):
            self._pending_row_edit = {
                "row": row,
                "col": col,
                "before": self._snapshot_row_cells(row),
            }
        else:
            self._pending_row_edit = None

    def _begin_closing_row_edit(self) -> None:
        if self._pending_row_edit is None:
            return
        self._closing_row_edit = self._pending_row_edit
        self._pending_row_edit = None
        self._schedule_finalize_row_edit()

    def _schedule_finalize_row_edit(self) -> None:
        self._edit_finalize_timer.start()

    def _row_edit_cell_value(self, row: int, col: int) -> tuple:
        if col == COL_SNO:
            sno_it = self._table.item(row, COL_SNO)
            return ("text", sno_it.text() if sno_it else "")
        return self._serialize_cell(row, col)

    def _finalize_one_row_edit(self, pending: dict) -> None:
        if self._undo_redo_active or self._bulk_mutating:
            return
        row = pending["row"]
        before = pending["before"]
        changes = {}
        for (r, c), old_val in before.items():
            if r != row:
                continue
            new_val = self._row_edit_cell_value(r, c)
            if new_val != old_val:
                changes[(r, c)] = old_val
        if changes:
            self._record_undo({"cells": changes})

    def _finalize_pending_row_edit(self) -> None:
        pending = self._closing_row_edit
        if pending is None:
            return
        row = pending["row"]
        if self._auto_fill_row == row:
            self._edit_finalize_timer.start(100)
            return
        self._finalize_one_row_edit(pending)
        self._closing_row_edit = None

    def _flush_pending_row_edits(self) -> None:
        """Finalize any open edit, waiting briefly for async Item auto-fill."""
        self._edit_finalize_timer.stop()
        if self._pending_row_edit is not None:
            self._closing_row_edit = self._pending_row_edit
            self._pending_row_edit = None
        deadline = time.monotonic() + 2.0
        while self._closing_row_edit is not None:
            row = self._closing_row_edit["row"]
            if self._auto_fill_row == row:
                if time.monotonic() > deadline:
                    break
                QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
                continue
            self._finalize_one_row_edit(self._closing_row_edit)
            self._closing_row_edit = None
            break

    def _record_row_remove_undo(self, rows: list) -> None:
        ops = []
        for row in sorted(rows):
            ops.append({
                "op": "remove",
                "at": row,
                "values": self._row_value_map(row),
                "meta": self._capture_row_meta(row),
            })
        self._record_undo({"row_ops": ops})

    def _record_row_insert_undo(self, at: int, count: int = 1) -> None:
        self._record_undo({"row_ops": [{"op": "insert", "at": at, "count": count}]})

    def _emit_undo_redo_state(self) -> None:
        self.undo_redo_changed.emit(bool(self._undo_stack), bool(self._redo_stack))

    def toolbar_undo(self) -> None:
        self._commit_open_editor()
        self._undo()

    def toolbar_redo(self) -> None:
        self._commit_open_editor()
        self._redo()

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
        # Full-row copy must match `_serialize_row` / cut maps: absolute column
        # indices including empty S/NO + Cashier. Omitting READONLY cols used to
        # shift Date→Item on `_paste_rows` (enumerate assumed table indices).
        if self._selection_is_full_rows(rows):
            lines = ["\t".join(self._serialize_row(r)) for r in rows]
            QApplication.clipboard().setText("\n".join(lines))
            return
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
        if not rows:
            return False
        indexes = self._table.selectedIndexes()
        if not indexes:
            return False
        sel_rows = {i.row() for i in indexes}
        if sel_rows != set(rows):
            return False
        sel_cols = {i.column() for i in indexes}
        ncols = self._table.columnCount()
        data_cols = {c for c in range(ncols) if c not in READONLY_COLS}
        if not data_cols:
            return False
        if data_cols.issubset(sel_cols):
            return True
        # Row gutter select: empty cells may be omitted from selectedIndexes().
        if COL_SNO in sel_cols and max(sel_cols) >= max(data_cols):
            span = set(range(min(sel_cols), max(sel_cols) + 1))
            return data_cols.issubset(span)
        return False

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

    def _tsv_cells_to_column_map(self, cells: list) -> dict:
        """Map a TSV row to table column→text without shifting fields.

        Prefers full-width serialize (len == columnCount). Also accepts the
        older "data columns only" shape that omitted S/NO + Cashier.
        """
        ncols = self._table.columnCount()
        if not cells:
            return {}
        if len(cells) >= ncols:
            return {
                col: cells[col]
                for col in range(ncols)
                if col != COL_SNO
            }
        data_cols = [c for c in range(ncols) if c not in READONLY_COLS]
        if len(cells) == len(data_cols):
            return {col: cells[i] for i, col in enumerate(data_cols)}
        return {
            col: cell
            for col, cell in enumerate(cells)
            if col < ncols and col != COL_SNO
        }

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
            elif col == COL_TZS or col == COL_USD:
                self._table.setItem(row, col, _amount_item_from_raw(str(cell)))
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
            pad = 0 if self._merged_mode else DEFAULT_EDITABLE_ROWS
            min_rows = self._saved_count + pad
            if self._table.rowCount() < min_rows:
                start = self._table.rowCount()
                self._table.setRowCount(min_rows)
                if pad:
                    self._init_editable_rows(start, min_rows)

        self._record_row_insert_undo(insert_at, len(maps))
        truck_cells: list = []
        written_rows: list = []
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
                    if self._merged_mode:
                        # Merged is not an entry surface — skip unsaved row inserts.
                        continue
                    insert_at = max(insert_at, self._saved_count)

                self._shift_row_maps_on_insert(insert_at)
                self._table.insertRow(insert_at)
                if was_saved:
                    # Saved move: read-only style until _restore_moved_row_meta.
                    for col in range(self._table.columnCount()):
                        it = QTableWidgetItem("")
                        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        self._table.setItem(insert_at, col, it)
                else:
                    self._init_editable_rows(insert_at, insert_at + 1)
                truck_cells.extend(self._write_row_values(insert_at, values))
                self._restore_moved_row_meta(insert_at, meta)
                self._sync_row_date(insert_at)
                written_rows.append(insert_at)
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
        self._schedule_bulk_auto_fill_from_maps(written_rows)

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
                pad = 0 if self._merged_mode else DEFAULT_EDITABLE_ROWS
                min_rows = self._saved_count + pad
                if self._table.rowCount() < min_rows:
                    start = self._table.rowCount()
                    self._table.setRowCount(min_rows)
                    if pad:
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
        self._truck_allow_anyway = {
            (k + 1 if k >= at_row else k): v
            for k, v in self._truck_allow_anyway.items()
        }
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
        self._truck_allow_anyway = {
            (k - 1 if k > at_row else k): v
            for k, v in self._truck_allow_anyway.items()
            if k != at_row
        }
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

        # TSV row insert creates new entry rows — My entries only.
        if self._merged_mode:
            return

        cur = self._table.currentRow()
        insert_at = max(cur, self._saved_count)

        self._record_row_insert_undo(insert_at, len(lines))
        truck_cells: list = []
        written_rows: list = []
        self._bulk_mutating = True
        prev = self._table.blockSignals(True)
        try:
            for line in lines:
                self._shift_row_maps_on_insert(insert_at)
                self._table.insertRow(insert_at)
                self._init_editable_rows(insert_at, insert_at + 1)
                cells = line.split("\t")
                values = self._tsv_cells_to_column_map(cells)
                truck_cells.extend(self._write_row_values(insert_at, values))
                self._sync_row_date(insert_at)
                written_rows.append(insert_at)
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
        self._schedule_bulk_auto_fill_from_maps(written_rows)

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
        autofill_rows: set = set()
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
                    if self._merged_mode and row >= self._saved_count:
                        continue
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
                        elif col in (COL_TZS, COL_USD):
                            self._table.setItem(row, col, _amount_item_from_raw(cell_value))
                        elif col == COL_TRUCK:
                            self._table.setItem(row, col, QTableWidgetItem(cell_value.upper()))
                            if cell_value:
                                truck_cells.append((row, cell_value))
                        else:
                            self._table.setItem(row, col, QTableWidgetItem(_upper_text(col, cell_value)))
                            if col == COL_DESC and cell_value:
                                autofill_rows.add(row)
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
                            if self._merged_mode:
                                break
                            self._append_editable_rows(20)
                        if self._merged_mode and row >= self._saved_count:
                            continue
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
                        elif col in (COL_TZS, COL_USD):
                            self._table.setItem(row, col, _amount_item_from_raw(cell))
                        elif col == COL_TRUCK:
                            raw = cell.strip()
                            self._table.setItem(row, col, QTableWidgetItem(raw.upper() if raw else ""))
                            if raw:
                                truck_cells.append((row, raw))
                        else:
                            self._table.setItem(row, col, QTableWidgetItem(_upper_text(col, cell.strip())))
                            if col == COL_DESC and cell.strip():
                                autofill_rows.add(row)
                for row in touched_rows:
                    if row < self._saved_count and self._edit_mode:
                        self._mark_dirty(row)
                    self._sync_row_date(row)
                # If paste was Descs-only column or multi-col including Desc via row maps
                # of blank item/payee, also consider all touched rows that now have Desc.
                if not autofill_rows:
                    autofill_rows = set(touched_rows)
                self._table.blockSignals(prev)
                self._renumber()
                self._finalize_truck_cells(truck_cells)
        finally:
            self._bulk_mutating = False

        if self._has_cut_buffer() and not self._cut_is_rows:
            self._clear_cut_source_cells()
        self._update_footer()
        self._schedule_draft_autosave()
        # blockSignals skipped itemChanged — map-fill then one badge update.
        self._schedule_bulk_auto_fill_from_maps(autofill_rows)

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
        if cleared_rows:
            self._flush_day_issues_source_changed(force=True)

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
        # Fill-down may copy Description into blank Item/Payee rows.
        self._schedule_bulk_auto_fill_from_maps(rows[1:])

    def _copy_cell_from_to(
        self, src_row: int, src_col: int, dst_row: int, dst_col: int,
    ) -> list:
        """Copy one cell's value onto another editable cell. Returns truck finalize pairs."""
        if src_col in READONLY_COLS or dst_col in READONLY_COLS:
            return []
        if not self._cell_editable(dst_row, dst_col):
            return []
        src = self._table.item(src_row, src_col)
        truck_cells: list = []
        if dst_col in CHECK_COLS:
            it = self._table.item(dst_row, dst_col) or QTableWidgetItem()
            checked = bool(src.data(Qt.UserRole)) if src else False
            it.setData(Qt.UserRole, checked)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._table.setItem(dst_row, dst_col, it)
        elif dst_col == COL_TRUCK:
            raw = src.text().strip() if src else ""
            self._table.setItem(
                dst_row, dst_col, QTableWidgetItem(raw.upper() if raw else "")
            )
            if raw:
                truck_cells.append((dst_row, raw))
        elif dst_col == COL_RECEIPT:
            text = src.text().strip() if src else ""
            it = QTableWidgetItem(_receipt_paste_value(text) if text else "")
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self._table.setItem(dst_row, dst_col, it)
        elif dst_col in (COL_TZS, COL_USD):
            self._table.setItem(
                dst_row, dst_col, _amount_item_from_raw(src.text() if src else "")
            )
        else:
            text = src.text() if src else ""
            self._table.setItem(
                dst_row, dst_col, QTableWidgetItem(_upper_text(dst_col, text))
            )
        if dst_row < self._saved_count and self._edit_mode:
            self._dirty_rows.add(dst_row)
        return truck_cells

    def _apply_fill_drag(
        self,
        anchor: tuple[int, int, int, int],
        end: tuple[int, int],
    ) -> None:
        """Fill down/right from the selection when the user drags the fill handle."""
        r0, c0, r1, c1 = anchor
        end_row, end_col = end
        er = max(r1, end_row)
        ec = max(c1, end_col)
        if er <= r1 and ec <= c1:
            return

        snap: dict = {}
        for row in range(r0, er + 1):
            for col in range(c0, ec + 1):
                if r0 <= row <= r1 and c0 <= col <= c1:
                    continue
                if not self._cell_editable(row, col) or col in READONLY_COLS:
                    continue
                snap[(row, col)] = self._serialize_cell(row, col)
        if not snap:
            return
        self._push_undo_cells(snap)

        sel_h = r1 - r0 + 1
        sel_w = c1 - c0 + 1
        truck_cells: list = []
        self._table.blockSignals(True)
        try:
            for row in range(r0, er + 1):
                for col in range(c0, ec + 1):
                    if r0 <= row <= r1 and c0 <= col <= c1:
                        continue
                    if not self._cell_editable(row, col) or col in READONLY_COLS:
                        continue
                    src_row = r0 + (row - r0) % sel_h
                    src_col = c0 + (col - c0) % sel_w
                    truck_cells.extend(
                        self._copy_cell_from_to(src_row, src_col, row, col)
                    )
                if row >= self._saved_count:
                    self._sync_row_date(row)
                elif row in self._dirty_rows:
                    for col_i in range(self._table.columnCount()):
                        it = self._table.item(row, col_i)
                        if it is not None:
                            it.setBackground(QBrush(DIRTY_BG))
        finally:
            self._table.blockSignals(False)

        self._renumber()
        self._finalize_truck_cells(truck_cells)
        self._update_footer()
        self._schedule_draft_autosave()
        self._table.viewport().update()

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

    def _owns_saved_row(self, row: int) -> bool:
        """True when the signed-in user may delete/edit this saved register row.

        Cashiers: own rows only. Accountants/admins: any row (Merged oversight).
        """
        role = (getattr(self._user, "role", "") or "").lower()
        if role in ("admin", "accountant"):
            return True
        tx = self._saved_txs.get(row)
        if tx is None:
            return False
        owner = getattr(tx, "cashier_id", None)
        uid = getattr(self._user, "_id", None)
        if owner is None or uid is None:
            return False
        return str(owner) == str(uid)

    def _owned_saved_rows(self, rows: list) -> list:
        return [r for r in rows if r in self._saved_ids and self._owns_saved_row(r)]

    def _add_delete_own_saved_actions(
        self, menu: QMenu, *, row: int, saved_sel: list, owned_sel: list
    ) -> bool:
        """Add Delete for owned saved rows. Returns True if an action was added."""
        if owned_sel and self._selection_is_full_rows(saved_sel):
            label = (
                "Delete Saved Entr"
                + ("ies" if len(owned_sel) > 1 else "y")
            )
            act = menu.addAction(label)
            act.triggered.connect(lambda: self._delete_saved_rows(owned_sel))
            return True
        if 0 <= row < self._saved_count and self._owns_saved_row(row):
            act = menu.addAction("Delete Saved Entry")
            act.triggered.connect(lambda: self._delete_saved_row(row))
            return True
        return False

    def _populate_context_menu(self, menu: QMenu) -> None:
        row = self._table.currentRow()
        sel_rows = sorted({i.row() for i in self._table.selectedIndexes()})
        saved_sel = [r for r in sel_rows if r < self._saved_count]
        owned_sel = self._owned_saved_rows(saved_sel)

        # Copy is always useful in Merged browse and My entry.
        menu.addAction("Copy", self._copy)

        if self._merged_mode and not self._edit_mode:
            # Day browser: copy + delete own only. No entry / insert actions.
            self._add_delete_own_saved_actions(
                menu, row=row, saved_sel=saved_sel, owned_sel=owned_sel
            )
            return

        if self._merged_mode and self._edit_mode:
            # Reorder saved drafts (cut/insert); still no blank-row entry.
            menu.addAction("Cut", self._cut)
            menu.addAction("Paste", self._paste)
            if self._has_cut_buffer():
                menu.addAction("Insert Cut Cells", self._insert_cut_cells)
            if self._add_delete_own_saved_actions(
                menu, row=row, saved_sel=saved_sel, owned_sel=owned_sel
            ):
                pass
            if self._undo_stack or self._redo_stack:
                menu.addSeparator()
            if self._undo_stack:
                menu.addAction("Undo", self._undo)
            if self._redo_stack:
                menu.addAction("Redo", self._redo)
            return

        # My entries — full entry surface.
        if not self._edit_mode and (
            owned_sel
            or (0 <= row < self._saved_count and self._owns_saved_row(row))
        ):
            menu.addSeparator()
            self._add_delete_own_saved_actions(
                menu, row=row, saved_sel=saved_sel, owned_sel=owned_sel
            )
            return

        menu.addAction("Cut", self._cut)
        menu.addAction("Paste", self._paste)
        if self._has_cut_buffer():
            menu.addAction("Insert Cut Cells", self._insert_cut_cells)
        menu.addSeparator()
        menu.addAction("Insert Row Above", self._insert_above)
        menu.addAction("Insert Row Below", self._insert_below)
        menu.addAction("Delete Selected Row(s)", self._delete_rows)
        if self._undo_stack:
            menu.addSeparator()
            menu.addAction("Undo", self._undo)
        if self._redo_stack:
            menu.addAction("Redo", self._redo)

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self._table)
        self._populate_context_menu(menu)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _show_row_header_context_menu(self, global_pos) -> None:
        menu = QMenu(self._table)
        self._populate_context_menu(menu)
        menu.exec(global_pos)

    def _insert_above(self) -> None:
        if self._merged_mode:
            return
        row = max(self._table.currentRow(), self._saved_count)
        self._record_row_insert_undo(row, 1)
        self._shift_row_maps_on_insert(row)
        self._table.insertRow(row)
        self._init_editable_rows(row, row + 1)
        self._renumber()

    def _insert_below(self) -> None:
        if self._merged_mode:
            return
        row = max(self._table.currentRow() + 1, self._saved_count)
        self._record_row_insert_undo(row, 1)
        self._shift_row_maps_on_insert(row)
        self._table.insertRow(row)
        self._init_editable_rows(row, row + 1)
        self._renumber()

    def _delete_rows(self) -> None:
        if self._merged_mode:
            return
        rows = sorted(
            {i.row() for i in self._table.selectedIndexes()
             if i.row() >= self._saved_count},
            reverse=True,
        )
        if not rows:
            return
        self._record_row_remove_undo(sorted(rows))
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
    # Delete saved row(s)
    # ------------------------------------------------------------------

    def _delete_saved_row(self, row: int) -> None:
        self._delete_saved_rows([row])

    def _delete_saved_rows(self, rows: list) -> None:
        requested = sorted({r for r in rows if r in self._saved_ids})
        rows = self._owned_saved_rows(requested)
        skipped = len(requested) - len(rows)
        n_rows = len(rows)
        if not n_rows:
            if skipped:
                QMessageBox.information(
                    self,
                    "Delete",
                    "You can only delete your own register entries.",
                )
            return
        if skipped:
            QMessageBox.information(
                self,
                "Delete",
                f"{skipped} selected entr{'y belongs' if skipped == 1 else 'ies belong'} "
                "to another cashier and will be skipped.",
            )

        verified_n = 0
        pending_n = 0
        draft_n = 0
        submitted_n = 0
        for row in rows:
            tx = self._saved_txs.get(row)
            is_pending_edit = bool(
                tx is not None
                and getattr(tx, "original_transaction_id", None)
                and not getattr(tx, "verified", False)
            )
            is_verified = bool(tx is not None and getattr(tx, "verified", False))
            if is_verified:
                verified_n += 1
            elif is_pending_edit:
                pending_n += 1
            elif (
                tx is not None
                and (getattr(tx, "register_status", "") or "submitted") == "draft"
            ):
                draft_n += 1
            else:
                submitted_n += 1

        if n_rows == 1:
            row = rows[0]
            it = self._table.item(row, COL_DESC)
            desc = it.text() if it else "?"
            if verified_n:
                msg = (
                    f'Request deletion of approved expense:\n"{desc}"?\n\n'
                    "It leaves Master Expenses and goes to the accountant's "
                    "Verify → Deleted tab. After they confirm, it moves to Trash."
                )
            elif pending_n:
                msg = (
                    f'Delete pending edit:\n"{desc}"?\n\n'
                    "This undoes the edit. The original approved expense stays in Master."
                )
            elif submitted_n:
                msg = (
                    f'Move submitted expense to Trash:\n"{desc}"?\n\n'
                    "It leaves the Verify inbox and appears in your Trash "
                    "(you can restore or delete it there)."
                )
            else:
                msg = (
                    f'Move draft expense to Trash:\n"{desc}"?\n\n'
                    "It will appear in your Trash so you can restore or "
                    "delete it permanently."
                )
        else:
            parts = [f"Delete {n_rows} selected saved row(s)?"]
            if verified_n:
                parts.append(
                    f"{verified_n} approved row(s) → Verify → Deleted "
                    "(accountant confirms → Trash)."
                )
            if pending_n:
                parts.append(
                    f"{pending_n} pending edit(s) will be removed "
                    "(originals stay on Master)."
                )
            if draft_n:
                parts.append(
                    f"{draft_n} draft row(s) → your Trash (restore / purge)."
                )
            if submitted_n:
                parts.append(
                    f"{submitted_n} submitted row(s) leave Verify → your Trash."
                )
            msg = "\n\n".join(parts)

        if (
            QMessageBox.question(
                self, "Delete Entry",
                msg,
                QMessageBox.Yes | QMessageBox.No,
            )
            == QMessageBox.Yes
        ):
            tx_ids = [self._saved_ids[r] for r in rows if r in self._saved_ids]
            from tahmeed.ui.async_utils import schedule_coro
            schedule_coro(self._do_delete_saved_many(tx_ids))

    async def _do_delete_saved(self, tx_id) -> None:
        await self._do_delete_saved_many([tx_id])

    async def _do_delete_saved_many(self, tx_ids: list) -> None:
        if not tx_ids:
            return
        try:
            cashier_id = getattr(self._user, "_id", None)
            requested = 0
            trashed = 0
            deleted = 0
            missing = 0
            forbidden = 0
            for tx_id in tx_ids:
                result = await request_or_delete_transaction(tx_id, cashier_id)
                if result == "not_found":
                    missing += 1
                elif result == "forbidden":
                    forbidden += 1
                elif result == "deletion_requested":
                    requested += 1
                elif result in ("trashed", "already_trashed"):
                    trashed += 1
                elif result == "deleted":
                    deleted += 1
            if forbidden and not (requested or trashed or deleted):
                QMessageBox.warning(
                    self, "Delete",
                    "You can only delete your own register entries.",
                )
            elif missing and not (requested or trashed or deleted or forbidden):
                QMessageBox.warning(
                    self, "Not Found",
                    "Those entries were already removed.",
                )
            elif requested or trashed or deleted:
                parts = []
                if trashed:
                    parts.append(
                        f"{trashed} expense(s) moved to your Trash.\n"
                        "Open Trash to restore or delete them permanently."
                    )
                if requested:
                    parts.append(
                        f"{requested} approved expense(s) sent to Verify → Deleted.\n"
                        "An accountant must confirm (then Trash) or restore to Master."
                    )
                if deleted:
                    parts.append(f"{deleted} pending-edit clone(s) removed.")
                if forbidden:
                    parts.append(
                        f"{forbidden} entr{'y was' if forbidden == 1 else 'ies were'} "
                        "skipped (not yours)."
                    )
                title = (
                    "Moved to Trash"
                    if trashed and not requested
                    else "Deletion Requested"
                    if requested
                    else "Deleted"
                )
                QMessageBox.information(self, title, "\n\n".join(parts))
            if trashed or requested or deleted:
                self.trash_changed.emit()
                # Same invalidate + reload as Drafts discard so My cannot keep
                # painting deleted rows until a Merged flip.
                await self._reload_after_server_row_change()
            elif missing:
                # Already gone on server — still refresh if the grid is stale.
                await self._reload_after_server_row_change()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to delete:\n{exc}")

    # ------------------------------------------------------------------
    # Import from Excel (daily MATUMIZI)
    # ------------------------------------------------------------------

    def import_from_file(self) -> None:
        if self._merged_mode:
            QMessageBox.information(
                self,
                "My entries only",
                "Switch to My entries to import.\n\n"
                "Merged shows every cashier’s saved rows for the day — "
                "it is not used for data entry.",
            )
            return
        asyncio.ensure_future(self._run_daily_import())

    async def _run_daily_import(self) -> None:
        from tahmeed.ui.async_utils import pause_background_polls
        from tahmeed.ui.cashier.daily_import_flow import run_daily_import_flow

        with pause_background_polls(self):
            try:
                preview = await run_daily_import_flow(self)
            except Exception as exc:
                QMessageBox.critical(
                    self, "Import Error", f"Could not import this file:\n\n{exc}"
                )
                return
            if preview is None:
                return
            await self.apply_daily_import_preview(preview)

    async def apply_daily_import_preview(self, preview) -> None:
        """Navigate to the Excel main date and stage rows for the user to Save."""
        from tahmeed.services.daily_import_service import staged_row_payload
        from tahmeed.ui.widgets.upload_busy import UploadBusy

        primary = preview.primary_date or self._current_date
        if not isinstance(primary, date):
            QMessageBox.warning(
                self,
                "Reconciled Date required",
                "This import has no reconciled date. Set Reconciled Date and try again.",
            )
            return

        # Always resolve unsaved work first — same-date imports used to append on
        # top of recovered local-draft / typed rows (extra rows vs the Excel file).
        if self.has_unsaved_work():
            ok = await self.confirm_leave()
            if not ok:
                return

        # Drop any crash-recovery draft for the target day so load cannot re-inject
        # old rows under the fresh import. The local Parked bucket survives,
        # except rows parked from an earlier import of this same file.
        self._clear_day_draft_keep_parked(primary, source=preview.source_filename)
        self._invalidate_day_tx_cache()

        self._skip_draft_restore = True
        try:
            if primary != self._current_date:
                self._reset_edit_state()
                self._current_date = primary
                with UploadBusy(
                    self,
                    "Opening import date…",
                    title="Importing Daily Register",
                ) as busy:
                    await busy.tick()
                    busy.update("Opening import date…", value=20, maximum=100)
                    await self._load_date(primary)
                    busy.update("Import date ready", value=100, maximum=100)
                    await busy.tick()
            else:
                # Same day: wipe leftover unsaved editable rows before staging.
                self._wipe_unsaved_editable_rows()
        finally:
            self._skip_draft_restore = False

        payloads = [staged_row_payload(row, preview) for row in preview.rows]
        # Queue truck issues now, but open the correction dialog only after this
        # async import task finishes — nested dialog.exec() + ensure_future crashes
        # under Python 3.14 / qasync.
        self._suppress_truck_dialog = True
        try:
            total = max(1, len(payloads))
            with UploadBusy(
                self,
                f"Loading {len(payloads):,} row(s) into table…",
                title="Importing Daily Register",
            ) as busy:
                # This coroutine runs inside an asyncio Task, where
                # processEvents is a no-op — only real awaits let the dialog
                # paint. Tick once so the horse / bar show before the loop.
                await busy.tick()
                busy.update(
                    f"Loading 0 / {len(payloads):,} row(s)…",
                    value=0,
                    maximum=total,
                )
                await busy.tick()

                async def _on_load_progress(done: int, count: int) -> None:
                    busy.update(
                        f"Loading {done:,} / {count:,} row(s)…",
                        value=done,
                        maximum=max(1, count),
                    )
                    await busy.tick()

                await self._load_staged_import_rows_async(
                    payloads, on_progress=_on_load_progress
                )
                busy.update(
                    f"Loaded {len(payloads):,} row(s)",
                    value=total,
                    maximum=total,
                )
                await busy.tick()
            QMessageBox.information(
                self,
                "Import ready",
                f"Loaded {len(payloads):,} row(s) from \"{preview.source_filename}\" "
                f"under reconciled date {primary.strftime('%d/%m/%Y')}.\n\n"
                "Excel row dates are kept as written. Open this upload (or this "
                "reconciled day in Simple) anytime to see every row in the batch.\n\n"
                "Review the Table, make any edits, then click Save.\n"
                "Saved entries go to the accountant Verify inbox.",
            )
            self.day_issues_source_changed.emit()
        finally:
            self._suppress_truck_dialog = False
        QTimer.singleShot(0, self._flush_truck_correction)

    def _wipe_unsaved_editable_rows(self) -> None:
        """Clear typed / staged unsaved rows without undo (import staging prep)."""
        self._commit_open_editor()
        self._draft_timer.stop()
        txs = [
            self._saved_txs[r]
            for r in range(self._saved_count)
            if self._saved_txs.get(r) is not None
        ]
        show_cashier = not self._table.isColumnHidden(COL_CASHIER)
        self._pending_row_meta.clear()
        self._truck_allow_anyway.clear()
        self._pending_truck_issues.clear()
        self._populate(txs)
        self._apply_column_visibility(show_cashier=show_cashier)

    def _kick_auto_fill_item(self, row: int, description: str = "") -> None:
        """Schedule exact Description-Map fill for one row (type / Tab / Enter)."""
        if self._bulk_mutating:
            return
        if not getattr(self, "_auto_fill_from_maps", True):
            return
        # Prefer live cell text — Tab accept + uppercase may have just settled.
        live = self._cell_text(row, COL_DESC).strip() or (description or "").strip()
        if not live:
            return
        self._auto_fill_seq += 1
        seq = self._auto_fill_seq
        self._auto_fill_row = row
        asyncio.ensure_future(self._auto_fill_from_mapping(row, live, seq=seq))

    def set_auto_fill_from_maps(self, enabled: bool) -> None:
        """Toggle live cache used by type/paste auto-fill (setting persisted elsewhere)."""
        self._auto_fill_from_maps = bool(enabled)

    def _schedule_bulk_auto_fill_from_maps(self, rows) -> None:
        """After paste/bulk desc edits: one batched map lookup, then badge flush."""
        row_list = sorted({int(r) for r in (rows or []) if r is not None and int(r) >= 0})
        if not row_list:
            self._flush_day_issues_source_changed(force=True)
            return
        if not getattr(self, "_auto_fill_from_maps", True):
            self._flush_day_issues_source_changed(force=True)
            return
        from tahmeed.ui.async_utils import schedule_coro

        schedule_coro(self._auto_fill_rows_from_mappings(row_list))

    def _clear_day_draft_keep_parked(
        self,
        primary: date,
        *,
        source: str = "",
        clear_merged: bool | None = None,
    ) -> None:
        """Clear typed / recovered draft rows for *primary*, keep local Parked.

        ``source``: rows parked from an earlier import of that same file are
        dropped so a re-import does not double them up under Parked.
        ``clear_merged``: also drop the Merged recovery file (default: only when
        Merged is the current view).
        """
        if clear_merged is None:
            clear_merged = self._merged_mode
        try:
            draft = load_register_draft(self._user._id, primary, merged=False)
            parked = draft_parked_rows(draft)
            if source:
                key = (source or "").strip().lower()
                parked = [
                    e for e in parked
                    if (
                        (hydrate_pending_meta(e.get("pending_meta")) or {}).get(
                            "daily_import_source"
                        )
                        or ""
                    ).strip().lower()
                    != key
                ]
            if parked:
                save_register_draft(
                    build_draft_payload(
                        user_id=self._user._id,
                        username=getattr(self._user, "username", "") or "",
                        register_date=primary,
                        merged=False,
                        edit_mode=False,
                        dirty_saved=[],
                        new_rows=[],
                        parked_rows=parked,
                    )
                )
            else:
                clear_register_draft(self._user._id, primary, merged=False)
            if clear_merged:
                clear_register_draft(self._user._id, primary, merged=True)
            if primary == self._current_date:
                self._local_parked = parked
                self._emit_parked_count()
        except Exception:
            pass

    @staticmethod
    def _import_progress_due(r: int, total: int) -> bool:
        return r == 0 or r + 1 == total or (r + 1) % 25 == 0

    def _begin_staged_import(self, payloads: list) -> dict:
        """Lock the grid for a bulk import; returns loop state for ``_stage_import_row``."""
        self._draft_timer.stop()
        self._bulk_mutating = True
        return {
            "start": self._first_empty_editable_row(),
            "prev_block": self._table.blockSignals(True),
            "truck_cells": [],
            "loaded_rows": set(),
            "total": len(payloads),
        }

    def _stage_import_row(self, state: dict, r: int, data: dict) -> None:
        """Write one staged import payload onto row ``start + r``."""
        target = state["start"] + r
        if target >= self._table.rowCount():
            self._append_editable_rows(max(20, state["total"] - r + 5))
        if target < self._saved_count:
            return
        state["loaded_rows"].add(target)
        self._pending_row_meta[target] = {
            "client_row_id": new_client_row_id(),
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

        payee_name = data.get("payee") or ""
        if payee_name:
            self._table.setItem(
                target,
                COL_PAYEE,
                QTableWidgetItem(_upper_text(COL_PAYEE, payee_name)),
            )

        desc = data.get("description") or ""
        self._table.setItem(
            target, COL_DESC, QTableWidgetItem(_upper_text(COL_DESC, desc))
        )

        truck = data.get("truck_number") or ""
        if truck:
            self._table.setItem(target, COL_TRUCK, QTableWidgetItem(truck.upper()))
            state["truck_cells"].append((target, truck))

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

        # Split amount into TZS / USD columns (legacy USD-only rows land in USD).
        staged = Transaction(
            date=data.get("date") or datetime.utcnow(),
            description=data.get("description") or "",
            truck_number=data.get("truck_number") or "",
            amount=float(data.get("amount") or 0),
            currency=data.get("currency") or "TZS",
            amount_usd=(
                float(data["amount_usd"])
                if data.get("amount_usd") is not None
                else None
            ),
        )
        tzs_txt, usd_txt = _display_money_cells(staged)
        self._table.setItem(target, COL_TZS, _money_item(tzs_txt))
        self._table.setItem(target, COL_USD, _money_item(usd_txt))

        rcpt = data.get("receipt_status") or ""
        rcpt_it = QTableWidgetItem(str(rcpt))
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

    def _finish_staged_import(self, state: dict) -> None:
        """Unlock the grid after a bulk import and queue truck validation."""
        try:
            for row in state["loaded_rows"]:
                self._sync_row_date(row)
        finally:
            self._table.blockSignals(state["prev_block"])
            self._bulk_mutating = False
        self._renumber()
        self._finalize_truck_cells(state["truck_cells"])
        self._update_footer()
        self._schedule_draft_autosave()
        self._flush_day_issues_source_changed(force=True)

    def _load_staged_import_rows(self, payloads: list, *, on_progress=None) -> None:
        """Synchronous staging (tests / small batches)."""
        if not payloads:
            return
        state = self._begin_staged_import(payloads)
        try:
            for r, data in enumerate(payloads):
                self._stage_import_row(state, r, data)
                if on_progress is not None and self._import_progress_due(
                    r, state["total"]
                ):
                    on_progress(r + 1, state["total"])
        finally:
            self._finish_staged_import(state)

    async def _load_staged_import_rows_async(
        self, payloads: list, *, on_progress=None
    ) -> None:
        """Stage rows in slices, yielding to Qt between them.

        The import runs inside an asyncio Task where ``processEvents`` is a
        no-op (Python 3.14 / qasync), so a blocking loop leaves the busy dialog
        blank. ``on_progress`` is awaited so it can ``tick`` the event loop and
        paint the horse + percentage bar.
        """
        if not payloads:
            return
        from tahmeed.ui.async_utils import pause_background_polls

        # Yielding lets timers fire — hold off the outbox drain (it reads the
        # half-staged grid) exactly like a date load does.
        prev_load_flag = self._load_in_flight
        self._load_in_flight = True
        with pause_background_polls(self):
            state = self._begin_staged_import(payloads)
            try:
                for r, data in enumerate(payloads):
                    self._stage_import_row(state, r, data)
                    if self._import_progress_due(r, state["total"]):
                        if on_progress is not None:
                            await on_progress(r + 1, state["total"])
                        else:
                            await asyncio.sleep(0)
            finally:
                self._finish_staged_import(state)
                self._load_in_flight = prev_load_flag

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
            COL_USD:      12,
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
                    it = self._table.item(target, grid_col) or QTableWidgetItem()
                    it.setText(raw)
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
                elif grid_col in (COL_TZS, COL_USD):
                    if raw and raw != "None":
                        self._table.setItem(target, grid_col, _amount_item_from_raw(raw))
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
            self._ensure_row_client_id(row)
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
        if self._current_date is None:
            QMessageBox.warning(
                self,
                "Reconciled Date required",
                "Set Reconciled Date before saving.",
            )
            return
        if self._merged_mode and not self._dirty_rows:
            QMessageBox.information(
                self,
                "My entries only",
                "Switch to My entries to add new drafts.\n\n"
                "Merged is for browsing everyone’s saved rows for this day.",
            )
            return
        if self._save_in_flight or self._submit_in_flight:
            return
        asyncio.ensure_future(self._do_save())

    # ------------------------------------------------------------------
    # QuickBooks-style toolbar actions
    # ------------------------------------------------------------------

    def _table_stylesheet(self, font_pt: int) -> str:
        hdr_px = max(9, int(round(11 * font_pt / _TABLE_FONT_PT_DEFAULT)))
        return f"""
            QTableWidget {{
                background: #ffffff;
                gridline-color: #e5e7eb;
                border: none;
                font-family: Calibri;
                font-size: {font_pt}pt;
                selection-background-color: #cde0f5;
                selection-color: #1B2B4B;
            }}
            QHeaderView::section:horizontal {{
                background: #253A5C;
                color: #F9FAFB;
                font-weight: 600;
                font-size: {hdr_px}px;
                padding: 5px 4px;
                border: none;
                border-right: 1px solid #1B2B4B;
                border-bottom: 2px solid #0077C5;
            }}
            QHeaderView::section:vertical {{
                background: #F2F2F2;
                color: #333333;
                font-family: Calibri;
                font-size: {font_pt}pt;
                padding: 0 2px;
                border: none;
                border-right: 1px solid #D4D4D4;
                border-bottom: 1px solid #D4D4D4;
            }}
            QHeaderView:vertical {{
                background: #ffffff;
                border: none;
            }}
            QTableCornerButton::section {{
                background: #F2F2F2;
                border: none;
                border-right: 1px solid #D4D4D4;
                border-bottom: 2px solid #0077C5;
            }}
            QTableWidget::item         {{ padding: 2px 3px; color: #111827; }}
            QTableWidget::item:selected {{ color: #1B2B4B; font-weight: 500; }}
            QLineEdit {{
                color: #111827;
                background: #ffffff;
                font-family: Calibri;
                font-size: {font_pt}pt;
            }}
            QComboBox {{
                color: #111827;
                background: #ffffff;
                font-family: Calibri;
                font-size: {font_pt}pt;
            }}
        """

    def _row_height_for_font(self, font_pt: int) -> int:
        scaled = int(round(DEFAULT_ROW_HEIGHT * font_pt / _TABLE_FONT_PT_DEFAULT))
        return max(MIN_ROW_HEIGHT, scaled)

    def can_increase_font(self) -> bool:
        return self._table_font_pt < _TABLE_FONT_PT_MAX

    def can_decrease_font(self) -> bool:
        return self._table_font_pt > _TABLE_FONT_PT_MIN

    def _emit_font_size_state(self) -> None:
        self.font_size_changed.emit(
            self.can_increase_font(),
            self.can_decrease_font(),
        )

    def _apply_table_font_size(
        self, *, persist: bool = True, emit: bool = True
    ) -> None:
        """Restyle the register grid + row heights for ``_table_font_pt``."""
        pt = self._table_font_pt
        font = QFont("Calibri")
        font.setPointSize(pt)
        row_h = self._row_height_for_font(pt)
        tables = [
            t for t in (self._my_table, self._parked_table, self._table)
            if t is not None
        ]
        seen: set[int] = set()
        for table in tables:
            key = id(table)
            if key in seen:
                continue
            seen.add(key)
            table.setFont(font)
            table.setStyleSheet(self._table_stylesheet(pt))
            vh = table.verticalHeader()
            if isinstance(vh, ExcelRowHeaderView):
                vh.set_font_pt(pt)
            vh.setDefaultSectionSize(row_h)
            if table is self._table and self._wrapped_cols:
                self._refresh_wrapped_row_heights()
            else:
                for row in range(table.rowCount()):
                    table.setRowHeight(row, row_h)

        if persist:
            _save_table_font_pt(pt)
        if emit:
            self._emit_font_size_state()

    def toolbar_font_larger(self) -> None:
        if not self.can_increase_font():
            return
        self._table_font_pt += 1
        self._apply_table_font_size()

    def toolbar_font_smaller(self) -> None:
        if not self.can_decrease_font():
            return
        self._table_font_pt -= 1
        self._apply_table_font_size()

    def _apply_column_visibility(self, *, show_cashier: bool | None = None) -> None:
        """Apply Cashier visibility + user Hide Column state."""
        if show_cashier is None:
            show_cashier = bool(self._merged_mode or self._load_upload_id)
        for col in range(self._table.columnCount()):
            if col == COL_CASHIER:
                self._table.setColumnHidden(col, not show_cashier)
            elif col in self._user_hidden_cols:
                self._table.setColumnHidden(col, True)
            else:
                self._table.setColumnHidden(col, False)
        hdr = self._table.horizontalHeader()
        if hdr is not None:
            hdr.viewport().update()

    def _hide_columns(self, cols) -> None:
        """Excel Hide Column(s) — keeps S/NO and Cashier locked."""
        show_cashier = not self._table.isColumnHidden(COL_CASHIER)
        for col in cols or []:
            col = int(col)
            if col in _COLUMN_HIDE_LOCKED:
                continue
            if col < 0 or col >= self._table.columnCount():
                continue
            self._user_hidden_cols.add(col)
        self._apply_column_visibility(show_cashier=show_cashier)

    def _unhide_columns(self, cols) -> None:
        """Excel Unhide — restore columns hidden via Hide Column."""
        show_cashier = not self._table.isColumnHidden(COL_CASHIER)
        for col in cols or []:
            self._user_hidden_cols.discard(int(col))
        self._apply_column_visibility(show_cashier=show_cashier)

    def _wrap_target_columns(self) -> set:
        """Columns touched by the current selection, or all columns if none."""
        cols = {i.column() for i in self._table.selectedIndexes()}
        if cols:
            return cols
        return set(range(self._table.columnCount()))

    def wrap_is_active(self) -> bool:
        return bool(self._wrapped_cols)

    def _schedule_wrap_height_refresh(self) -> None:
        if not self._wrapped_cols:
            return
        self._wrap_height_timer.start()

    def _refresh_wrapped_row_heights(self) -> None:
        """Grow/shrink rows so wrapped columns show their full text."""
        base = self._row_height_for_font(self._table_font_pt)
        vh = self._table.verticalHeader()
        vh.setDefaultSectionSize(base)
        if not self._wrapped_cols:
            for row in range(self._table.rowCount()):
                self._table.setRowHeight(row, base)
            return

        fm = QFontMetrics(self._table.font())
        pad = 4
        for row in range(self._table.rowCount()):
            h = base
            for col in self._wrapped_cols:
                w = self._table.columnWidth(col) - (2 * _CELL_H_INSET)
                if w < 16:
                    continue
                item = self._table.item(row, col)
                text = item.text() if item is not None else ""
                if not text:
                    continue
                br = fm.boundingRect(
                    QRect(0, 0, w, _WRAP_ROW_HEIGHT_MAX * 4),
                    int(Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap),
                    text,
                )
                h = max(h, br.height() + pad)
            self._table.setRowHeight(row, min(h, _WRAP_ROW_HEIGHT_MAX))

    def toolbar_toggle_wrap(self) -> None:
        """Toggle Wrap Text for selected columns (or all if nothing selected)."""
        cols = self._wrap_target_columns()
        if cols and cols.issubset(self._wrapped_cols):
            self._wrapped_cols -= cols
        else:
            self._wrapped_cols |= cols
        self._refresh_wrapped_row_heights()
        self._table.viewport().update()
        self.wrap_state_changed.emit(self.wrap_is_active())

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

    def toolbar_new_table(self) -> None:
        """Open a blank register for fresh entry (Reconciled Date shows '-')."""
        asyncio.ensure_future(self._toolbar_new_table())

    # Back-compat alias — New no longer inserts a row.
    toolbar_new_row = toolbar_new_table

    async def _toolbar_new_table(self) -> None:
        self._commit_open_editor()
        if self.has_unsaved_work():
            if self._current_date is None:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle("Unsaved changes")
                box.setText(
                    "This blank table has unsaved entries but no Reconciled Date.\n\n"
                    "Set Reconciled Date to save as draft, or discard this work."
                )
                discard_btn = box.addButton("Discard", QMessageBox.DestructiveRole)
                cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
                box.setDefaultButton(cancel_btn)
                box.exec()
                if box.clickedButton() != discard_btn:
                    return
            else:
                choice = self._prompt_unsaved_work(
                    "You have unsaved entries in the Daily Register.\n"
                    "Save as draft under the current Reconciled Date, "
                    "discard, or cancel?"
                )
                if choice == "cancel":
                    return
                if choice == "save":
                    if not await self._do_save():
                        return
                else:
                    self._clear_local_draft()
        else:
            self._clear_local_draft()
        self._open_blank_register()

    def _open_blank_register(self) -> None:
        """Empty editable grid with unset reconciled date (New table)."""
        self._draft_timer.stop()
        if isinstance(self._current_date, date):
            # Drops typed rows; the local Parked bucket for that day survives.
            self._clear_local_draft()
        # Leave Parked surface without re-importing its rows into the bucket.
        self._clear_my_session()
        if self._my_table is not None:
            self._activate_table_surface(self._my_table)
        self._local_parked = []
        self._server_parked_count = 0
        self._emit_parked_count()
        self._load_gen += 1  # cancel any in-flight date load
        self._load_upload_id = ""
        self._skip_draft_restore = False
        self._draft_restore_policy = "notify"
        self._invalidate_day_tx_cache()
        self._reset_edit_state()
        self._pending_row_meta.clear()
        self._truck_allow_anyway.clear()
        self._pending_truck_issues.clear()
        self._header_cheque = ""
        self._current_date = None
        # New table is always My entries (Merged / Parked have no entry surface).
        if self._view_mode != "my":
            self._view_mode = "my"
            self._merged_mode = False
            self._parked_mode = False
            self.mode_changed.emit("my")
        if self._parked_table is not None and self._parked_table is not self._table:
            prev = self._table
            self._table = self._parked_table
            try:
                self._populate([])
            finally:
                self._table = prev
        self._populate([])
        self._apply_column_visibility(show_cashier=False)
        self._hide_register_loading()
        if self._table.rowCount() > 0:
            self._table.setCurrentCell(0, COL_DESC)

    def toolbar_delete(self) -> None:
        """Delete selected saved entries or clear/remove selected unsaved rows."""
        sel_rows = sorted({i.row() for i in self._table.selectedIndexes()})
        saved = [r for r in sel_rows if r < self._saved_count]
        if saved and self._selection_is_full_rows(saved):
            self._delete_saved_rows(saved)
            if any(r >= self._saved_count for r in sel_rows):
                self._delete_rows()
            return
        row = self._table.currentRow()
        if row < 0:
            return
        if row < self._saved_count:
            self._delete_saved_row(row)
            return
        self._delete_rows()

    def _unsaved_parked_clear_count(self) -> int:
        """Parked truck rows still only in the grid that Clear Table would wipe."""
        count = 0
        for row, meta in self._pending_row_meta.items():
            if not isinstance(meta, dict) or not meta.get("parked"):
                continue
            if row >= self._saved_count:
                count += 1
        return count

    def toolbar_clear_table(self) -> None:
        """Clear every unsaved new row and revert unsaved edits on saved rows."""
        if not self.has_unsaved_work():
            QMessageBox.information(
                self,
                "Clear Table",
                "There is no unsaved data to clear.",
            )
            return
        from tahmeed.ui.dialogs.confirm_dialog import ask_confirm

        parked_n = self._unsaved_parked_clear_count()
        if parked_n:
            plural = "s" if parked_n != 1 else ""
            body = (
                "All unsaved rows and typed data will be removed.\n\n"
                f"This also clears {parked_n} parked truck row{plural} that "
                "have not finished saving — they will leave the Parked tab "
                "as well."
            )
            note = (
                "Already-saved entries (including saved parked trucks) "
                "will not be affected."
            )
        else:
            body = "All unsaved rows and typed data will be removed."
            note = "Saved entries on this register will not be affected."

        if not ask_confirm(
            self,
            title="Clear Table",
            heading="Clear this table?",
            body=body,
            note=note,
            accept_label="Clear table",
            cancel_label="Cancel",
            destructive=True,
            default_cancel=True,
        ):
            return
        self._commit_open_editor()
        self._clear_unsaved_with_undo()

    def _clear_unsaved_with_undo(self) -> None:
        """Wipe unsaved work while recording one undo step."""
        undo_cells: dict = {}
        row_ops: list = []

        for row in sorted(self._dirty_rows):
            undo_cells.update(self._snapshot_row_cells(row))

        min_rows = self._saved_count + DEFAULT_EDITABLE_ROWS
        extra_rows = list(range(min_rows, self._table.rowCount()))
        for row in range(self._saved_count, self._table.rowCount()):
            if self._row_has_data(row):
                undo_cells.update(self._snapshot_row_cells(row))
        for row in extra_rows:
            row_ops.append({
                "op": "remove",
                "at": row,
                "values": self._row_value_map(row),
                "meta": self._capture_row_meta(row),
            })

        entry: dict = {}
        if undo_cells:
            entry["cells"] = undo_cells
        if row_ops:
            entry["row_ops"] = row_ops
        if entry:
            self._record_undo(entry)

        self._bulk_mutating = True
        self._table.blockSignals(True)
        truck_cells: list = []
        try:
            for row in sorted(self._dirty_rows, reverse=True):
                tx = self._saved_txs.get(row)
                if tx is not None:
                    truck_cells.extend(
                        self._write_row_values(row, self._tx_to_row_values(tx))
                    )
                self._dirty_rows.discard(row)
                for col in range(self._table.columnCount()):
                    it = self._table.item(row, col)
                    if it is not None and self._edit_mode:
                        it.setBackground(QBrush(EDIT_BG))

            for row in range(self._saved_count, min(self._table.rowCount(), min_rows)):
                self._clear_editable_row(row)

            for row in sorted(extra_rows, reverse=True):
                self._shift_row_maps_on_remove(row)
                self._table.removeRow(row)
                self._pending_row_meta.pop(row, None)

            if self._table.rowCount() < min_rows:
                start = self._table.rowCount()
                self._table.setRowCount(min_rows)
                self._init_editable_rows(start, min_rows)
        finally:
            self._table.blockSignals(False)
            self._bulk_mutating = False

        self._renumber()
        self._finalize_truck_cells(truck_cells)
        self._clear_cut_marquee()
        self._clear_local_draft()
        self._update_footer()
        self._refresh_truck_required_highlights()
        self.edit_state_changed.emit(self._edit_mode, len(self._dirty_rows))
        self._table.setCurrentCell(
            max(self._saved_count, 0),
            COL_DESC,
        )

    def _tx_to_row_values(self, tx: Transaction) -> dict:
        tzs_txt, usd_txt = _display_money_cells(tx)
        cashier = (
            self._cashier_names.get(tx.cashier_id, "—") if tx.cashier_id else "—"
        )
        return {
            COL_DATE: format_register_date(tx.date) if tx.date else "",
            COL_ITEM: tx.item or "",
            COL_DESC: tx.description or "",
            COL_TRUCK: tx.truck_number or "",
            COL_MEMO: tx.memo or "",
            COL_REF: _ref_float_text(tx),
            COL_TZS: tzs_txt,
            COL_USD: usd_txt,
            COL_RECEIPT: tx.receipt_status or "pending",
            COL_OWN: tx.ownership or "",
            COL_APR: tx.approver or "",
            COL_PAYEE: getattr(tx, "payee", "") or "",
            COL_CHEQUE: getattr(tx, "cheque", "") or "",
            COL_CASHIER: cashier,
        }

    def _clear_editable_row(self, row: int) -> None:
        """Blank one unsaved editable row back to the default empty grid state."""
        for col in range(self._table.columnCount()):
            if col in (COL_SNO, COL_CASHIER) or col in READONLY_COLS:
                continue
            self._table.takeItem(row, col)
        self._pending_row_meta.pop(row, None)
        self._sync_row_date(row)
        self._deactivate_row(row)

    def _mark_new_row_saved(self, row: int, *, flush: bool = True) -> None:
        """After a successful insert: clear the editable row and prune local draft.

        Keeps mid-save crashes from recovering rows that already reached Mongo.
        Pass ``flush=False`` when clearing many rows, then call
        ``_flush_local_draft()`` once after the batch.
        """
        if row < self._saved_count:
            return
        prev = self._table.blockSignals(True)
        try:
            self._clear_editable_row(row)
        finally:
            self._table.blockSignals(prev)
        if flush:
            self._flush_local_draft()

    def _append_saved_transactions(self, txs: list) -> None:
        """Grow the saved prefix from ACK'd transactions (no full day reload)."""
        if not txs:
            return
        prev = self._table.blockSignals(True)
        try:
            for tx in txs:
                at = self._saved_count
                self._table.insertRow(at)
                self._shift_row_maps_on_insert(at)
                self._fill_saved_row(at, tx)
                self._saved_ids[at] = tx._id
                self._saved_txs[at] = tx
                self._saved_count += 1
        finally:
            self._table.blockSignals(prev)
        self._renumber()
        self._update_footer()
        self._emit_active_cheque()

    def _enqueue_inserts_for_outbox(
        self, to_save: list[tuple[int, "Transaction"]]
    ) -> int:
        """Mark new rows PENDING + flush draft (durable before any network)."""
        for row, tx in to_save:
            self._ensure_row_client_id(row)
            meta = dict(self._pending_row_meta.get(row) or {})
            meta["sync_status"] = SYNC_PENDING
            if getattr(tx, "day_order", None) is not None:
                try:
                    meta["day_order"] = int(tx.day_order)
                except (TypeError, ValueError):
                    pass
            if getattr(tx, "possible_duplicate", False):
                meta["possible_duplicate"] = True
                dup_of = getattr(tx, "duplicate_of_id", None)
                if dup_of is not None:
                    meta["duplicate_of_id"] = str(dup_of)
                else:
                    meta.pop("duplicate_of_id", None)
            else:
                meta.pop("possible_duplicate", None)
                meta.pop("duplicate_of_id", None)
            self._pending_row_meta[row] = meta
            self._apply_row_sync_tint(row)
        self._flush_local_draft()
        return len(to_save)

    def _row_date_iso(self, row: int) -> str:
        """Normalize the Date cell (or register day) to ``YYYY-MM-DD``."""
        year = self._current_date.year if self._current_date else date.today().year
        it = self._table.item(row, COL_DATE)
        raw = it.text().strip() if it else ""
        parsed = _parse_optional_date(raw, default_year=year) if raw else None
        if parsed is not None:
            return parsed.date().isoformat()
        if self._current_date is not None:
            return self._current_date.isoformat()
        return ""

    def _expense_fingerprint_for_row(self, row: int):
        """Content fingerprint for a grid row (saved or editable)."""
        def txt(col: int) -> str:
            it = self._table.item(row, col)
            return it.text().strip() if it else ""

        tzs_raw = txt(COL_TZS)
        usd_raw = txt(COL_USD)
        return expense_content_fingerprint(
            description=txt(COL_DESC),
            truck_number=txt(COL_TRUCK),
            item=txt(COL_ITEM),
            tzs=_parse_amount_text(tzs_raw) if tzs_raw else 0.0,
            usd=_parse_amount_text(usd_raw) if usd_raw else 0.0,
            row_date_iso=self._row_date_iso(row),
        )

    def _expense_fingerprint_for_tx(self, tx: Transaction):
        """Content fingerprint for a loaded server transaction."""
        try:
            tzs, usd = tx.money_parts()
        except Exception:
            tzs = float(getattr(tx, "amount", 0) or 0)
            usd = float(getattr(tx, "amount_usd", 0) or 0)
        row_date = ""
        raw_date = getattr(tx, "date", None)
        if raw_date is not None:
            try:
                row_date = (
                    raw_date.date().isoformat()
                    if hasattr(raw_date, "date")
                    else str(raw_date)
                )
            except Exception:
                row_date = ""
        return expense_content_fingerprint(
            description=getattr(tx, "description", None) or "",
            truck_number=getattr(tx, "truck_number", None) or "",
            item=getattr(tx, "item", None) or "",
            tzs=tzs,
            usd=usd,
            row_date_iso=row_date,
        )

    def _expense_fingerprint_from_cells(self, values: dict) -> tuple:
        """Fingerprint from a draft cell map (column index → text)."""
        year = self._current_date.year if self._current_date else date.today().year
        raw_date = (values.get(COL_DATE) or "").strip()
        parsed = _parse_optional_date(raw_date, default_year=year) if raw_date else None
        if parsed is not None:
            row_date = parsed.date().isoformat()
        elif self._current_date is not None:
            row_date = self._current_date.isoformat()
        else:
            row_date = ""
        tzs_raw = (values.get(COL_TZS) or "").strip()
        usd_raw = (values.get(COL_USD) or "").strip()
        return expense_content_fingerprint(
            description=(values.get(COL_DESC) or "").strip(),
            truck_number=(values.get(COL_TRUCK) or "").strip(),
            item=(values.get(COL_ITEM) or "").strip(),
            tzs=_parse_amount_text(tzs_raw) if tzs_raw else 0.0,
            usd=_parse_amount_text(usd_raw) if usd_raw else 0.0,
            row_date_iso=row_date,
        )

    def _saved_expense_fingerprint_counts(self) -> dict:
        """Multiset of fingerprints for currently loaded saved rows."""
        counts: dict = {}
        for row in range(self._saved_count):
            tx = self._saved_txs.get(row)
            if tx is not None:
                fp = self._expense_fingerprint_for_tx(tx)
            else:
                fp = self._expense_fingerprint_for_row(row)
            if not fingerprint_is_meaningful(fp):
                continue
            counts[fp] = counts.get(fp, 0) + 1
        return counts

    def _saved_client_row_ids(self) -> set:
        """``client_row_id`` values already present on loaded saved transactions."""
        out: set = set()
        for row in range(self._saved_count):
            tx = self._saved_txs.get(row)
            if tx is None:
                continue
            rid = getattr(tx, "client_row_id", None)
            if rid:
                out.add(str(rid).strip())
        return out

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
        # Fresh id — never reuse the source row's client_row_id.
        self._ensure_row_client_id(insert_at)
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

    def has_unsaved_work(self, *, include_stashed_my: bool = True) -> bool:
        """True when edit-mode dirty rows or typed-but-unsaved new rows exist.

        Unsaved parked rows (local bucket or still on the Parked grid) count as
        unsaved work — Save persists them as ``register_status=parked``.

        While Parked is showing, also inspect the keep-alive My table/session so
        navigation prompts still see leftover My entries. Pass
        ``include_stashed_my=False`` for Parked-surface Save outcomes that must
        ignore the hidden My grid.
        """
        if self._dirty_rows:
            return True
        # Local Parked bucket waiting on My for the next Save → server.
        if self._local_parked and not self._parked_mode and not self._merged_mode:
            return True
        for row in range(self._saved_count, self._table.rowCount()):
            if not self._row_has_data(row):
                continue
            return True
        if (
            include_stashed_my
            and self._parked_mode
            and self._my_session is not None
            and self._my_table is not None
        ):
            if self._my_session.get("dirty_rows"):
                return True
            saved = int(self._my_session.get("saved_count") or 0)
            for row in range(saved, self._my_table.rowCount()):
                # Minimal data check without rebinding maps: Description/amount.
                desc = self._my_table.item(row, COL_DESC)
                tzs = self._my_table.item(row, COL_TZS)
                usd = self._my_table.item(row, COL_USD)
                if (desc and desc.text().strip()) or (
                    tzs and tzs.text().strip()
                ) or (usd and usd.text().strip()):
                    return True
        return False

    def _row_stays_parked(self, row: int) -> bool:
        """Unsaved row marked parked whose truck is still not a valid fleet value."""
        return (
            row >= self._saved_count
            and self._row_marked_parked(row)
            and not self._row_truck_is_valid(row)
        )

    def _notify_day_issues_source_changed(self) -> None:
        """Mark Day Issues dirty / refresh badge.

        During bulk paste/import, defer to one emit via
        ``_flush_day_issues_source_changed`` so the sidebar count stays live
        without scanning on every cell.
        """
        if self._bulk_mutating:
            self._day_issues_notify_pending = True
            return
        self._day_issues_notify_pending = False
        self.day_issues_source_changed.emit()

    def _flush_day_issues_source_changed(self, *, force: bool = False) -> None:
        """Emit one Day Issues source-changed after a blocked-signal bulk edit.

        Paste/clear/fill-down use ``blockSignals`` so ``itemChanged`` never runs;
        callers must flush once afterward (Qt bulk-update best practice).
        """
        if not force and not self._day_issues_notify_pending:
            return
        self._day_issues_notify_pending = False
        if self._bulk_mutating:
            # Still inside a nested bulk — keep pending for the outer flush.
            self._day_issues_notify_pending = True
            return
        self.day_issues_source_changed.emit()

    def snapshot_transactions_for_day_issues(self) -> list:
        """Live Table rows (saved + unsaved) using current Item / Payee / Desc cells.

        Used as the authoritative Day Issues source when this register date is open.
        """
        from dataclasses import replace
        from datetime import datetime as _dt

        from tahmeed.models.transaction import Transaction

        if self._current_date is None:
            return []
        base_dt = _dt(
            self._current_date.year,
            self._current_date.month,
            self._current_date.day,
        )
        out: list = []
        for row in range(self._table.rowCount()):
            if row >= self._saved_count and not self._row_has_data(row):
                continue
            desc = self._cell_text(row, COL_DESC).strip()
            item = self._cell_text(row, COL_ITEM).strip()
            payee = self._cell_text(row, COL_PAYEE).strip()
            if row < self._saved_count:
                base = self._saved_txs.get(row)
                if base is None:
                    continue
                out.append(
                    replace(
                        base,
                        description=desc or (base.description or ""),
                        item=item,
                        category_name=item or (base.category_name or ""),
                        payee=payee,
                    )
                )
                continue
            if not desc:
                continue
            out.append(
                Transaction(
                    date=base_dt,
                    description=desc,
                    truck_number=self._cell_text(row, COL_TRUCK).strip(),
                    amount=0.0,
                    item=item,
                    category_name=item,
                    payee=payee,
                    cashier_id=getattr(self._user, "_id", None),
                    verified=False,
                )
            )
        return out

    def local_draft_transactions_for_day_issues(self, target: date) -> list:
        """Unsaved draft rows for *target* when the Table is not open on that date."""
        from datetime import datetime as _dt

        from tahmeed.models.transaction import Transaction

        draft = load_register_draft(
            self._user._id, target, merged=self._merged_mode
        )
        if draft is None or draft_is_empty(draft):
            return []
        base_dt = _dt(target.year, target.month, target.day)
        out: list = []
        for entry in draft.get("new_rows") or []:
            cells = cells_from_json(entry.get("cells"))
            desc = (cells.get(COL_DESC) or "").strip()
            if not desc:
                continue
            item = (cells.get(COL_ITEM) or "").strip()
            payee = (cells.get(COL_PAYEE) or "").strip()
            out.append(
                Transaction(
                    date=base_dt,
                    description=desc,
                    truck_number=(cells.get(COL_TRUCK) or "").strip(),
                    amount=0.0,
                    item=item,
                    category_name=item,
                    payee=payee,
                    cashier_id=getattr(self._user, "_id", None),
                    verified=False,
                )
            )
        return out

    def day_issues_row_source(self, target: date) -> tuple:
        """Return ``(replace_transactions, extra_transactions)`` for Day Issues.

        When the Table is open on *target*, the live snapshot fully replaces the
        DB load. Otherwise local draft new-rows are passed as extras.
        """
        if self._current_date == target:
            return self.snapshot_transactions_for_day_issues(), None
        extras = self.local_draft_transactions_for_day_issues(target)
        return None, extras or None

    def apply_description_mapping_to_open_rows(
        self,
        target: date,
        descriptions,
        *,
        payee: str,
        item_name: str,
        category_id=None,
        cashier_id=None,
        emit_source_changed: bool = True,
        clear_category: bool = False,
    ) -> int:
        """Stamp Payee + Item onto matching live / draft rows for *target*.

        Updates unsaved Table rows in place (no Save required). Also updates
        saved-row cells on the open register so the UI matches the DB patch.
        When the Table is on another date, patches the local draft file.

        Pass ``emit_source_changed=False`` when the caller (e.g. Day Issues map)
        will refresh Day Issues itself — avoids a duplicate full scan.
        Pass ``clear_category=True`` (Undo) to wipe category_id while writing
        blank Item / Payee.
        """
        from tahmeed.services.description_mapping_service import normalize_description

        keys = {
            normalize_description(d)
            for d in (descriptions or [])
            if normalize_description(d)
        }
        if not keys:
            return 0
        payee_text = " ".join((payee or "").strip().upper().split())
        item_text = (item_name or "").strip()

        if self._current_date == target:
            return self._apply_mapping_to_live_grid(
                keys,
                payee=payee_text,
                item_name=item_text,
                category_id=category_id,
                cashier_id=cashier_id,
                emit_source_changed=emit_source_changed,
                clear_category=clear_category,
            )
        return self._apply_mapping_to_local_draft(
            target,
            keys,
            payee=payee_text,
            item_name=item_text,
            category_id=category_id,
            clear_category=clear_category,
        )

    def _apply_mapping_to_live_grid(
        self,
        keys: set,
        *,
        payee: str,
        item_name: str,
        category_id=None,
        cashier_id=None,
        emit_source_changed: bool = True,
        clear_category: bool = False,
    ) -> int:
        from dataclasses import replace

        from tahmeed.services.description_mapping_service import normalize_description

        updated = 0
        self._commit_open_editor()
        prev_bulk = self._bulk_mutating
        self._bulk_mutating = True
        prev = self._table.blockSignals(True)
        try:
            for row in range(self._table.rowCount()):
                if row >= self._saved_count and not self._row_has_data(row):
                    continue
                desc = self._cell_text(row, COL_DESC).strip()
                key = normalize_description(desc)
                if not key or key not in keys:
                    continue
                if row < self._saved_count:
                    tx = self._saved_txs.get(row)
                    if tx is None:
                        continue
                    if getattr(tx, "verified", False) or getattr(tx, "trashed", False):
                        continue
                    if getattr(tx, "discarded", False):
                        continue
                    if cashier_id is not None:
                        cid = getattr(tx, "cashier_id", None)
                        if cid is None or str(cid) != str(cashier_id):
                            continue
                    self._write_mapping_cells(
                        row, item_name=item_name, payee=payee, saved=True
                    )
                    if clear_category:
                        next_cat_id = None
                    elif category_id is not None:
                        next_cat_id = category_id
                    else:
                        next_cat_id = tx.category_id
                    self._saved_txs[row] = replace(
                        tx,
                        item=item_name,
                        category_name=item_name,
                        category_id=next_cat_id,
                        payee=payee,
                    )
                    updated += 1
                    continue

                if cashier_id is not None:
                    # Unsaved rows belong to the signed-in user.
                    if str(getattr(self._user, "_id", "") or "") != str(cashier_id):
                        continue
                self._write_mapping_cells(
                    row, item_name=item_name, payee=payee, saved=False
                )
                meta = dict(self._pending_row_meta.get(row) or {})
                if clear_category:
                    meta.pop("category_id", None)
                elif category_id is not None:
                    meta["category_id"] = category_id
                self._pending_row_meta[row] = meta
                updated += 1
        finally:
            self._table.blockSignals(prev)
            self._bulk_mutating = prev_bulk
        if updated:
            self._schedule_draft_autosave()
            if emit_source_changed:
                self.day_issues_source_changed.emit()
        return updated

    def _write_mapping_cells(
        self, row: int, *, item_name: str, payee: str, saved: bool
    ) -> None:
        if saved and not self._edit_mode:
            flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            bg: Optional[QBrush] = QBrush(SAVED_BG)
        elif saved and self._edit_mode:
            flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
            bg = QBrush(EDIT_BG)
        else:
            flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
            bg = None

        for col, text in ((COL_ITEM, item_name), (COL_PAYEE, payee)):
            it = self._table.item(row, col)
            if it is None:
                it = QTableWidgetItem(text)
                self._table.setItem(row, col, it)
            else:
                it.setText(text)
            it.setFlags(flags)
            if bg is not None:
                it.setBackground(bg)

    def _apply_mapping_to_local_draft(
        self,
        target: date,
        keys: set,
        *,
        payee: str,
        item_name: str,
        category_id=None,
        clear_category: bool = False,
    ) -> int:
        from tahmeed.services.register_draft_service import apply_mapping_to_draft_payload

        draft = load_register_draft(
            self._user._id, target, merged=self._merged_mode
        )
        if draft is None or draft_is_empty(draft):
            return 0
        updated = apply_mapping_to_draft_payload(
            draft,
            keys,
            payee=payee,
            item_name=item_name,
            category_id=category_id,
            clear_category=clear_category,
            desc_col=COL_DESC,
            item_col=COL_ITEM,
            payee_col=COL_PAYEE,
        )
        if updated:
            try:
                save_register_draft(draft)
            except Exception:
                return 0
        return updated

    # ------------------------------------------------------------------
    # Phase 4 — outbox drain (offline queue → sync on reconnect)
    # ------------------------------------------------------------------

    def _api_is_online(self) -> bool:
        """True when the connectivity probe last succeeded (or probe unknown)."""
        try:
            from tahmeed.services.connectivity_service import connectivity_monitor

            status = connectivity_monitor.status
            if status is None:
                # No probe yet — allow Save (legacy online-first path).
                return True
            return bool(status.online)
        except Exception:
            return True

    def _on_connectivity_changed(self, status) -> None:
        try:
            online = bool(getattr(status, "online", False))
        except Exception:
            return
        if online:
            self._outbox_backoff_attempt = 0
            self._schedule_outbox_drain(delay_ms=400)

    def _schedule_outbox_drain(self, delay_ms: int = 500) -> None:
        if self._merged_mode or self._current_date is None:
            return
        if self._save_in_flight or self._outbox_drain_in_flight or self._load_in_flight:
            return
        if not self._api_is_online():
            return
        if not self._outbox_pending_rows():
            return
        self._outbox_drain_timer.start(max(0, int(delay_ms)))

    def _kick_outbox_drain(self) -> None:
        if self._outbox_drain_in_flight or self._save_in_flight or self._load_in_flight:
            return
        if self._outbox_cancel_requested:
            return
        asyncio.ensure_future(self._drain_register_outbox())

    def cancel_outbox_sync(self) -> bool:
        """Request cancel of the in-flight outbox drain (Esc).

        Already-ACK'd chunks stay saved; remaining rows return to PENDING
        (sky tint) for a later Save / auto-retry. Returns True if a cancel
        was armed or a pending timer was stopped.
        """
        stopped_timer = False
        if self._outbox_drain_timer.isActive():
            self._outbox_drain_timer.stop()
            stopped_timer = True
        if not self._outbox_drain_in_flight and not stopped_timer:
            return False
        self._outbox_cancel_requested = True
        if self._outbox_drain_in_flight:
            self.transient_status.emit("Cancelling sync…")
        elif stopped_timer:
            self._outbox_cancel_requested = False
            self.transient_status.emit("Sync cancelled")
        return True

    async def _await_outbox_idle(
        self, *, timeout_s: float = 180, require_empty: bool = True
    ) -> bool:
        """Wait for outbox drain to settle.

        ``require_empty=True`` (Parked after Save): wait until no PENDING /
        FAILED / IN_FLIGHT rows remain (or timeout).
        ``require_empty=False`` (before ``_load_date``): wait only until the
        current drain flight finishes so the grid wipe cannot race row indices.

        Returns True when the wait condition is met; False on timeout / offline.
        """
        if self._merged_mode or self._current_date is None:
            return True
        if not self._outbox_pending_rows() and not self._outbox_drain_in_flight:
            return True

        nested_busy = (
            self._save_in_flight
            or self._submit_in_flight
            or self._outbox_drain_in_flight
        )
        showed_overlay = False
        if not nested_busy:
            self._show_register_loading("Finishing sync…")
            showed_overlay = True

        # Kick a drain if rows are waiting and nothing is in flight.
        if (
            require_empty
            and self._outbox_pending_rows()
            and not self._outbox_drain_in_flight
            and not self._save_in_flight
            and not self._load_in_flight
            and self._api_is_online()
        ):
            self._outbox_drain_timer.stop()
            asyncio.ensure_future(self._drain_register_outbox())

        deadline = time.monotonic() + max(1.0, float(timeout_s))
        try:
            while time.monotonic() < deadline:
                draining = self._outbox_drain_in_flight
                pending = bool(self._outbox_pending_rows())
                if require_empty:
                    if not draining and not pending:
                        return True
                    if not self._api_is_online() and not draining:
                        return False
                else:
                    if not draining:
                        return True
                await asyncio.sleep(0.05)
            if require_empty:
                return (
                    not self._outbox_drain_in_flight
                    and not self._outbox_pending_rows()
                )
            return not self._outbox_drain_in_flight
        finally:
            if showed_overlay and not self._outbox_drain_in_flight:
                self._hide_register_loading()

    def _outbox_pending_rows(self) -> list[int]:
        """Editable rows explicitly queued for sync (offline / failed / in-flight)."""
        rows: list[int] = []
        if self._merged_mode:
            return rows
        for row in range(self._saved_count, self._table.rowCount()):
            if not self._row_has_data(row):
                continue
            meta = self._pending_row_meta.get(row) or {}
            if not meta.get("sync_status"):
                # Ordinary unsaved typing — user clicks Save; do not auto-drain.
                continue
            status = self._row_sync_status(row)
            if status in (SYNC_PENDING, SYNC_FAILED, SYNC_IN_FLIGHT):
                rows.append(row)
        return rows

    def _row_sync_status(self, row: int) -> str:
        """Live outbox state for a grid row (preserves IN_FLIGHT for tint)."""
        meta = self._pending_row_meta.get(row) or {}
        raw = str(meta.get("sync_status") or SYNC_PENDING).strip().upper()
        if raw == SYNC_IN_FLIGHT:
            return SYNC_IN_FLIGHT
        if raw == SYNC_FAILED:
            return SYNC_FAILED
        return SYNC_PENDING

    def _set_row_sync_status(self, row: int, status: str) -> None:
        if row < self._saved_count:
            return
        self._ensure_row_client_id(row)
        meta = dict(self._pending_row_meta.get(row) or {})
        if status == SYNC_IN_FLIGHT:
            meta["sync_status"] = SYNC_IN_FLIGHT
        elif status == SYNC_FAILED:
            meta["sync_status"] = SYNC_FAILED
        else:
            meta["sync_status"] = SYNC_PENDING
        self._pending_row_meta[row] = meta
        self._apply_row_sync_tint(row)

    def _apply_row_sync_tint(self, row: int) -> None:
        if row < self._saved_count or not self._row_has_data(row):
            return
        status = self._row_sync_status(row)
        if status == SYNC_FAILED:
            bg = QBrush(FAILED_SYNC_BG)
            tip = "Sync failed — will retry when online"
        elif status == SYNC_IN_FLIGHT:
            bg = QBrush(PENDING_SYNC_BG)
            tip = "Syncing…"
        else:
            bg = QBrush(PENDING_SYNC_BG)
            tip = "Queued — will sync when online"
        prev = self._table.blockSignals(True)
        try:
            for col in range(self._table.columnCount()):
                if col in READONLY_COLS:
                    continue
                it = self._table.item(row, col)
                if it is None:
                    continue
                it.setBackground(bg)
                it.setToolTip(tip)
        finally:
            self._table.blockSignals(prev)

    def _enqueue_offline_outbox(self) -> None:
        """Persist unsaved work as PENDING outbox entries (no network)."""
        if not self._parked_mode and self._local_parked:
            self._materialize_local_parked_for_save()
        for row in range(self._saved_count, self._table.rowCount()):
            if not self._row_has_data(row):
                continue
            self._set_row_sync_status(row, SYNC_PENDING)
        if self._parked_mode:
            self._local_parked = self._table_parked_entries()
        self._flush_local_draft()

    async def _drain_register_outbox(self) -> None:
        """Push PENDING/FAILED new rows to the server (single-flight)."""
        if (
            self._outbox_drain_in_flight
            or self._save_in_flight
            or self._submit_in_flight
            or self._load_in_flight
            or self._merged_mode
            or self._current_date is None
        ):
            return
        if not self._api_is_online():
            return
        rows = self._outbox_pending_rows()
        if not rows:
            return

        if self._outbox_cancel_requested:
            self._outbox_cancel_requested = False
            return

        self._outbox_drain_in_flight = True
        self._reload_after_drain = False
        self._outbox_cancel_requested = False
        nested_busy = self._save_in_flight or self._submit_in_flight
        with timed_op(
            "outbox_drain",
            day=str(self._current_date or ""),
            pending=len(rows),
        ) as meta:
            try:
                to_save: list[tuple[int, Transaction]] = []
                for row in rows:
                    try:
                        tx = self._build_transaction_from_row(row)
                    except ValueError:
                        continue
                    if tx is None:
                        continue
                    self._set_row_sync_status(row, SYNC_IN_FLIGHT)
                    to_save.append((row, tx))
                self._flush_local_draft()

                if not to_save:
                    meta["synced"] = 0
                    return

                total = len(to_save)

                def _progress(done: int, tot: int) -> None:
                    # Phase 2/3 — status only (no overlay / toolbar lock).
                    if nested_busy or tot <= 0:
                        return
                    esc = " · Esc to cancel" if tot > 1 else ""
                    if tot <= BULK_CREATE_CHUNK:
                        self.transient_status.emit(
                            f"Syncing {tot} pending "
                            f"entr{'y' if tot == 1 else 'ies'}…{esc}"
                        )
                        return
                    if done <= 0:
                        lo, hi = 1, min(BULK_CREATE_CHUNK, tot)
                    else:
                        hi = min(done, tot)
                        lo = ((hi - 1) // BULK_CREATE_CHUNK) * BULK_CREATE_CHUNK + 1
                    self.transient_status.emit(
                        f"Syncing {lo}–{hi} of {tot}…{esc}"
                    )

                try:
                    outcomes = await bulk_save_transactions(
                        [tx for _r, tx in to_save],
                        on_progress=_progress,
                        should_cancel=lambda: self._outbox_cancel_requested,
                    )
                except Exception as exc:
                    for row, _tx in to_save:
                        self._set_row_sync_status(row, SYNC_FAILED)
                    self._flush_local_draft()
                    self._outbox_backoff_attempt += 1
                    self._schedule_outbox_backoff()
                    meta["ok"] = False
                    meta["error"] = f"{type(exc).__name__}: {exc}"
                    if not nested_busy:
                        self.transient_status.emit(
                            "Sync failed — will retry when online"
                        )
                    return

                cancelled = False
                any_fail = False
                synced = 0
                already_exists = 0
                acked_txs: list = []
                success_idxs: list[int] = []
                for i, ((row, _tx), (status_key, saved_tx, err)) in enumerate(
                    zip(to_save, outcomes)
                ):
                    if status_key in ("created", "already_exists"):
                        synced += 1
                        if status_key == "already_exists":
                            already_exists += 1
                        if saved_tx is not None:
                            acked_txs.append(saved_tx)
                        success_idxs.append(i)
                    elif err in ("cancelled", "not processed"):
                        cancelled = True
                        self._set_row_sync_status(row, SYNC_PENDING)
                    else:
                        any_fail = True
                        self._set_row_sync_status(row, SYNC_FAILED)

                drained_parked_ids: set[str] = set()
                for i in sorted(success_idxs, reverse=True):
                    row, tx = to_save[i]
                    if (getattr(tx, "register_status", "") or "") == "parked":
                        rid = (
                            getattr(tx, "client_row_id", None)
                            or (self._pending_row_meta.get(row) or {}).get(
                                "client_row_id"
                            )
                        )
                        if rid:
                            drained_parked_ids.add(str(rid))
                    self._mark_new_row_saved(row, flush=False)
                if drained_parked_ids:
                    self._drop_local_parked_by_client_ids(drained_parked_ids)

                meta["synced"] = synced
                meta["already_exists"] = already_exists
                meta["failed"] = int(any_fail)
                meta["cancelled"] = int(cancelled)
                meta["total"] = total
                self._flush_local_draft()
                if synced:
                    self.drafts_changed.emit()
                    self.rows_saved.emit(synced)
                    if not any_fail and not cancelled:
                        self._outbox_backoff_attempt = 0

                # Phase 3 — promote ACK'd rows in place; reload only when we
                # cleared cells but lack transaction payloads to rebuild them.
                if acked_txs:
                    self._append_saved_transactions(acked_txs)
                    self._invalidate_day_tx_cache()
                need_reload = bool(
                    synced and len(acked_txs) < synced and self._current_date is not None
                )
                if need_reload:
                    self._reload_after_drain = True
                    self._invalidate_day_tx_cache()

                if cancelled:
                    left = len(self._outbox_pending_rows())
                    if not nested_busy:
                        parts = []
                        if synced:
                            parts.append(f"{synced} saved")
                        parts.append(f"{left} still pending")
                        self.transient_status.emit(
                            "Sync cancelled · " + " · ".join(parts)
                        )
                elif any_fail:
                    self._outbox_backoff_attempt += 1
                    self._schedule_outbox_backoff()
                    if not nested_busy and not need_reload:
                        left = len(self._outbox_pending_rows())
                        self.transient_status.emit(
                            f"Synced {synced} · {left} left to retry"
                        )
                elif synced and not need_reload and not nested_busy and not self._submit_in_flight:
                    self.transient_status.emit(
                        f"Draft saved: {synced} added · "
                        "Open Drafts to submit for Verify"
                    )
            finally:
                self._outbox_drain_in_flight = False
                self._outbox_cancel_requested = False
                reload_day = (
                    self._current_date
                    if self._reload_after_drain and self._current_date is not None
                    else None
                )
                self._reload_after_drain = False
                if reload_day is not None:
                    await self._load_date(reload_day)
                # Rows enqueued while this drain ran (another Save) — kick again.
                # Do not auto-resume immediately after an explicit cancel.
                elif (
                    self._outbox_pending_rows()
                    and not self._load_in_flight
                    and not self._save_in_flight
                    and not self._submit_in_flight
                    and not meta.get("cancelled")
                ):
                    self._schedule_outbox_drain(delay_ms=100)

    def _schedule_outbox_backoff(self) -> None:
        """Exponential backoff with jitter after a failed drain."""
        import random

        attempt = max(1, min(self._outbox_backoff_attempt, 6))
        base_ms = min(60_000, (2 ** attempt) * 1_000)
        jitter = random.randint(0, 500)
        self._schedule_outbox_drain(delay_ms=base_ms + jitter)

    # ------------------------------------------------------------------
    # Local draft autosave (crash / power-loss recovery)
    # ------------------------------------------------------------------

    def _schedule_draft_autosave(self) -> None:
        if (
            self._restoring_draft
            or self._save_in_flight
            or self._bulk_mutating
            or self._current_date is None
        ):
            return
        self._draft_timer.start()

    def _flush_local_draft(self, *, include_dirty: bool = True) -> None:
        """Persist current unsaved grid state to disk (or clear if empty)."""
        if self._restoring_draft or self._current_date is None:
            return
        try:
            self._commit_open_editor()
            payload = self._capture_local_draft(include_dirty=include_dirty)
            save_register_draft(payload)
        except Exception:
            # Local draft must never break typing / save.
            pass

    def _clear_local_draft(self, *, both_modes: bool = False) -> None:
        """Drop typed / dirty recovery rows. The local Parked bucket survives."""
        self._draft_timer.stop()
        if self._current_date is None:
            return
        try:
            if self._parked_mode:
                self._local_parked = self._table_parked_entries()
            parked = list(self._local_parked)
            if both_modes or self._merged_mode:
                clear_register_draft(
                    self._user._id, self._current_date, merged=True
                )
            if both_modes or not self._merged_mode:
                if parked:
                    save_register_draft(
                        build_draft_payload(
                            user_id=self._user._id,
                            username=getattr(self._user, "username", "") or "",
                            register_date=self._current_date,
                            merged=False,
                            edit_mode=False,
                            dirty_saved=[],
                            new_rows=[],
                            parked_rows=parked,
                        )
                    )
                else:
                    clear_register_draft(
                        self._user._id, self._current_date, merged=False
                    )
        except Exception:
            pass

    def _capture_rows_from_bound_table(self, *, include_dirty: bool) -> tuple:
        """Capture dirty_saved / new_rows / edit_mode from the active table maps."""
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
        if not self._merged_mode:
            for row in range(self._saved_count, self._table.rowCount()):
                if not self._row_has_data(row):
                    continue
                rid = self._ensure_row_client_id(row)
                pending = self._pending_row_meta.get(row)
                new_rows.append({
                    "client_row_id": rid,
                    "cells": cells_for_json(self._row_value_map(row)),
                    "pending_meta": serialize_pending_meta(pending),
                })
        edit_mode_flag = self._edit_mode and include_dirty and bool(dirty_saved)
        return dirty_saved, new_rows, edit_mode_flag

    def _capture_local_draft(self, *, include_dirty: bool = True) -> dict:
        # Merged never persists typed "new" rows — entry belongs in My.
        new_rows: list = []
        parked_rows: list = []
        dirty_saved: list = []
        edit_mode_flag = False
        if self._parked_mode:
            # Parked surface is the live bucket; My rows come from the keep-alive
            # My table (preferred) or the on-disk stash (slow path / cold).
            self._local_parked = self._table_parked_entries()
            parked_rows = list(self._local_parked)
            parked_dirty, _parked_new, parked_edit = self._capture_rows_from_bound_table(
                include_dirty=include_dirty
            )
            if self._my_session is not None and self._my_table is not None:
                parked_maps = self._snapshot_grid_session()
                parked_table = self._table
                try:
                    self._table = self._my_table
                    self._restore_grid_session(self._my_session)
                    dirty_saved, new_rows, edit_mode_flag = (
                        self._capture_rows_from_bound_table(include_dirty=include_dirty)
                    )
                    # Keep session in sync if client_row_ids were minted.
                    self._my_session = self._snapshot_grid_session()
                finally:
                    self._table = parked_table
                    self._restore_grid_session(parked_maps)
            else:
                try:
                    on_disk = load_register_draft(
                        self._user._id, self._current_date, merged=False
                    )
                except Exception:
                    on_disk = None
                if on_disk:
                    new_rows = [
                        e for e in (on_disk.get("new_rows") or [])
                        if isinstance(e, dict)
                    ]
                    live_ids = {str(e.get("tx_id")) for e in parked_dirty}
                    view_ids = {str(tx_id) for tx_id in self._saved_ids.values()}
                    stashed_dirty = [
                        e for e in (on_disk.get("dirty_saved") or [])
                        if isinstance(e, dict)
                        and str(e.get("tx_id")) not in live_ids
                        and str(e.get("tx_id")) not in view_ids
                    ]
                    if stashed_dirty:
                        edit_mode_flag = bool(on_disk.get("edit_mode", True))
                    dirty_saved = stashed_dirty
            # Parked edits to server-parked rows append after My dirty stash.
            dirty_saved = list(dirty_saved) + list(parked_dirty)
            if parked_edit and parked_dirty:
                edit_mode_flag = True
        elif not self._merged_mode:
            dirty_saved, new_rows, edit_mode_flag = self._capture_rows_from_bound_table(
                include_dirty=include_dirty
            )
            parked_rows = list(self._local_parked)
        else:
            dirty_saved, new_rows, edit_mode_flag = self._capture_rows_from_bound_table(
                include_dirty=include_dirty
            )

        return build_draft_payload(
            user_id=self._user._id,
            username=getattr(self._user, "username", "") or "",
            register_date=self._current_date,
            merged=self._merged_mode,
            edit_mode=edit_mode_flag,
            dirty_saved=dirty_saved,
            new_rows=new_rows,
            parked_rows=parked_rows,
        )

    def _restore_local_draft(self) -> Optional[tuple]:
        """Apply a saved draft onto the freshly populated grid.

        Returns ``(dirty_count, new_count)`` when anything was restored, else None.
        """
        if self._current_date is None:
            return None
        draft = load_register_draft(
            self._user._id, self._current_date, merged=self._merged_mode
        )
        if draft is None or draft_is_empty(draft):
            return None

        dirty_entries = list(draft.get("dirty_saved") or [])
        new_entries = list(draft.get("new_rows") or [])
        # Legacy drafts kept parked-flagged rows among new_rows (they used to be
        # saved as register_status=parked). Migrate them into the local bucket
        # so My never shows — or saves — incorrect-truck rows.
        migrated_parked = [e for e in new_entries if entry_is_parked(e)]
        if migrated_parked and not self._merged_mode:
            new_entries = [e for e in new_entries if not entry_is_parked(e)]
            known = {entry_client_row_id(e) for e in self._local_parked}
            for entry in migrated_parked:
                if entry_client_row_id(entry) in known:
                    continue
                self._local_parked.append(entry)
            self._emit_parked_count()
        if not dirty_entries and not new_entries:
            if migrated_parked and not self._merged_mode and not self._parked_mode:
                self._flush_local_draft()
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

            # Never restore typed new rows onto Merged / Parked (entry is My-only).
            # Prefer client_row_id match (Phase 1); fall back to content fingerprint
            # (Phase 0) so legacy drafts without ids still cannot inflate totals.
            if new_entries and not self._merged_mode and not self._parked_mode:
                known_ids = self._saved_client_row_ids()
                saved_fps = self._saved_expense_fingerprint_counts()
                to_restore: list = []
                reconcile_id = 0
                reconcile_fp = 0
                for entry in new_entries:
                    rid = entry_client_row_id(entry)
                    if rid and rid in known_ids:
                        reconcile_id += 1
                        continue
                    values = cells_from_json(entry.get("cells"))
                    fp = self._expense_fingerprint_from_cells(values)
                    if claim_fingerprint(saved_fps, fp):
                        reconcile_fp += 1
                        continue
                    to_restore.append((entry, values, rid))
                if reconcile_id or reconcile_fp:
                    log_register_event(
                        "draft_reconcile",
                        skipped_by_id=reconcile_id,
                        skipped_by_fingerprint=reconcile_fp,
                        restored=len(to_restore),
                        day=str(self._current_date or ""),
                    )
                if to_restore:
                    start = self._first_empty_editable_row()
                    needed = start + len(to_restore) - self._table.rowCount()
                    if needed > 0:
                        self._append_editable_rows(needed + 5)
                    for offset, (entry, values, rid) in enumerate(to_restore):
                        row = start + offset
                        if row < self._saved_count:
                            continue
                        truck_cells.extend(self._write_row_values(row, values))
                        meta = hydrate_pending_meta(entry.get("pending_meta")) or {}
                        if rid:
                            meta["client_row_id"] = rid
                        elif not meta.get("client_row_id"):
                            meta["client_row_id"] = new_client_row_id()
                        raw_sync = (
                            meta.get("sync_status") or entry.get("sync_status") or ""
                        )
                        raw_sync = str(raw_sync).strip().upper()
                        if raw_sync in (SYNC_PENDING, SYNC_FAILED, SYNC_IN_FLIGHT):
                            # Crash mid-flight → PENDING so reconnect can retry.
                            meta["sync_status"] = normalize_sync_status(raw_sync)
                        else:
                            meta.pop("sync_status", None)
                        self._pending_row_meta[row] = meta
                        self._activate_row(row)
                        self._sync_row_date(row)
                        if meta.get("sync_status"):
                            self._apply_row_sync_tint(row)
                        new_applied += 1
        finally:
            self._table.blockSignals(prev)
            self._bulk_mutating = False
            self._restoring_draft = False

        self._renumber()
        self._finalize_truck_cells(truck_cells)
        self._update_footer()
        # Rewrite disk draft from the live grid so fingerprint-skipped rows
        # (already on the server) are not re-offered on the next load.
        # Never flush from Parked — that would overwrite the My stash file.
        if new_entries and not self._merged_mode and not self._parked_mode:
            self._flush_local_draft()
        if dirty_applied or new_applied:
            self._flush_day_issues_source_changed(force=True)
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
        sync_left = 0
        for row in range(self._saved_count, self._table.rowCount()):
            meta = self._pending_row_meta.get(row) or {}
            if meta.get("sync_status"):
                sync_left += 1
        if sync_left:
            # Phase 2 — mid-sync leftovers (not necessarily an app crash).
            plural = "" if sync_left == 1 else "s"
            QMessageBox.information(
                self,
                "Sync pending",
                f"Recovered {detail}.\n\n"
                f"{sync_left} row{plural} were still waiting to reach the server "
                "(sky tint). They sync automatically when online, or click "
                "Save to retry.",
            )
            return
        QMessageBox.information(
            self,
            "Draft restored",
            f"Recovered {detail} from before the app closed.\n\n"
            "Click Save to store them on the server.",
        )

    def _prompt_unsaved_work(self, message: str) -> str:
        """Return ``save``, ``discard``, or ``cancel`` for unsaved register work."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Unsaved changes")
        box.setText(message)
        save_btn = box.addButton("Save as Draft", QMessageBox.AcceptRole)
        discard_btn = box.addButton("Discard", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(save_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_btn:
            return "save"
        if clicked == discard_btn:
            return "discard"
        return "cancel"

    def _active_item_editor(self):
        """Return the open cell editor widget, or None if not editing."""
        table = self._table
        if table.state() != QAbstractItemView.EditingState:
            return None
        idx = table.currentIndex()
        if idx.isValid():
            persistent = table.indexWidget(idx)
            if persistent is not None:
                return persistent
        viewport = table.viewport()
        w = QApplication.focusWidget()
        while w is not None:
            parent = w.parentWidget()
            if parent is viewport:
                return w
            if w is table or not table.isAncestorOf(w):
                break
            w = parent
        return None

    def _commit_open_editor(self) -> None:
        """Flush the active cell editor into the model before save/leave checks."""
        table = self._table
        editor = self._active_item_editor()
        if editor is not None:
            table.commitData(editor)
            table.closeEditor(editor, QAbstractItemDelegate.SubmitModelCache)
        elif table.state() == QAbstractItemView.EditingState:
            # Last resort: focus-out commits without naming the wrong widget.
            fw = QApplication.focusWidget()
            if fw is not None and table.isAncestorOf(fw):
                fw.clearFocus()
            table.setFocus(Qt.OtherFocusReason)
        self._begin_closing_row_edit()
        self._flush_pending_row_edits()

    async def confirm_leave(self) -> bool:
        """Ask to save/discard before logout or app exit. False = stay put."""
        self._commit_open_editor()
        if not self.has_unsaved_work():
            self._clear_local_draft()
            return True
        self._flush_local_draft()
        if self._current_date is None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Unsaved changes")
            box.setText(
                "You have unsaved entries but no Reconciled Date.\n\n"
                "Set Reconciled Date to save as draft, or discard before leaving."
            )
            discard_btn = box.addButton("Discard", QMessageBox.DestructiveRole)
            cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
            box.setDefaultButton(cancel_btn)
            box.exec()
            if box.clickedButton() != discard_btn:
                return False
            self._clear_local_draft()
            return True
        choice = self._prompt_unsaved_work(
            "You have unsaved entries in the Daily Register.\n"
            "Save as draft before leaving?"
        )
        if choice == "cancel":
            return False
        if choice == "discard":
            self._clear_local_draft()
            return True
        # Save as draft; if the user cancels mid-save (duplicates / off-date), stay.
        return await self._do_save()

    def _row_marked_parked(self, row: int) -> bool:
        """True when the row is tagged for the Parked truck follow-up tab."""
        meta = self._pending_row_meta.get(row) or {}
        return bool(isinstance(meta, dict) and meta.get("parked"))

    def _row_accepts_empty_truck(self, row: int) -> bool:
        """True when Fix trucks → No truck waived a truck for this row."""
        meta = self._pending_row_meta.get(row) or {}
        return bool(isinstance(meta, dict) and meta.get("accept_empty_truck"))

    async def _do_save(self, *, parked_only: bool = False) -> bool:
        """Persist dirty + new rows. Returns False if cancelled or leftovers remain.

        ``parked_only=True`` (Park all / Park remaining): persist only rows marked
        parked, skip Off-date / duplicate confirms, leave other unsaved rows alone.
        """
        if self._save_in_flight:
            log_register_event("save_ignored", reason="already_in_flight")
            return False
        self._save_in_flight = True
        self._kick_outbox_after_save = False
        # Submit already holds the busy UI lock; avoid flickering it off early.
        nested_under_submit = self._submit_in_flight
        if not nested_under_submit:
            self.save_busy_changed.emit(True)
            self._show_register_loading(
                "Parking for follow-up…" if parked_only else "Saving draft…"
            )
        from tahmeed.ui.async_utils import pause_background_polls

        try:
            with pause_background_polls(self):
                with timed_op(
                    "register_save",
                    day=str(self._current_date or ""),
                    merged=bool(self._merged_mode),
                    parked_only=bool(parked_only),
                ) as meta:
                    ok = await self._do_save_body(meta, parked_only=parked_only)
                    meta["ok"] = bool(ok)
                    return ok
        finally:
            self._save_in_flight = False
            kick = bool(self._kick_outbox_after_save)
            self._kick_outbox_after_save = False
            if not nested_under_submit and not self._submit_in_flight:
                self.save_busy_changed.emit(False)
                self._hide_register_loading()
            # Drain only after Save releases its lock (outbox guards on in-flight).
            if kick:
                self._schedule_outbox_drain(delay_ms=0)

    async def _do_save_body(
        self, meta: dict | None = None, *, parked_only: bool = False
    ) -> bool:
        """Inner save implementation (caller holds `_save_in_flight`)."""
        saved, updated, errors = 0, 0, []
        already_exists = 0
        kept_parked = 0
        parked_saved_ids: set[str] = set()
        deferred_dups: set[int] = set()
        self._commit_open_editor()

        if self._current_date is None:
            QMessageBox.warning(
                self,
                "Reconciled Date required",
                "Set Reconciled Date before saving as draft.",
            )
            return False

        missing_truck_rows = [
            row for row in self._rows_missing_required_truck()
            if not (self._pending_row_meta.get(row) or {}).get("parked")
            and not self._parked_mode
        ]
        if missing_truck_rows and not parked_only:
            detail_lines = []
            for row in missing_truck_rows[:12]:
                item_label = self._cell_text(row, COL_ITEM) or "—"
                detail_lines.append(f"  Row {row + 1}: {item_label}")
            if len(missing_truck_rows) > 12:
                detail_lines.append(
                    f"  …and {len(missing_truck_rows) - 12} more"
                )
            plural = "s" if len(missing_truck_rows) != 1 else ""
            QMessageBox.warning(
                self,
                "Truck Required",
                f"{len(missing_truck_rows)} row{plural} require a truck number "
                f"before saving:\n\n"
                + "\n".join(detail_lines)
                + "\n\nEnter the truck number for each row, then save again.",
                QMessageBox.Ok,
            )
            return False

        # Phase 4 — offline: queue locally; drain when connectivity returns.
        if not self._api_is_online() and (
            self.has_unsaved_work()
            if not parked_only
            else any(
                self._row_marked_parked(r) and self._row_has_data(r)
                for r in range(self._saved_count, self._table.rowCount())
            )
        ):
            if parked_only:
                if not self._parked_mode and self._local_parked:
                    self._materialize_local_parked_for_save()
                for row in range(self._saved_count, self._table.rowCount()):
                    if not self._row_has_data(row) or not self._row_marked_parked(row):
                        continue
                    self._set_row_sync_status(row, SYNC_PENDING)
                if self._parked_mode:
                    self._local_parked = self._table_parked_entries()
                self._flush_local_draft()
            else:
                self._enqueue_offline_outbox()
            if meta is not None:
                meta["offline_queued"] = True
                if parked_only:
                    meta["parked_only"] = True
            log_register_event(
                "outbox_enqueue",
                day=str(self._current_date or ""),
                rows=len(self._outbox_pending_rows()),
                parked_only=bool(parked_only),
            )
            if not self._submit_in_flight and not parked_only:
                self._hide_register_loading()
                QMessageBox.information(
                    self,
                    "Saved locally — will sync",
                    "You appear to be offline.\n\n"
                    "Your entries are kept on this computer and will sync "
                    "automatically when the connection returns.\n\n"
                    "You can keep editing; click Save again anytime.",
                )
            return True

        # ── Pass 1: commit edits to already-saved rows ─────────────────
        # In-place draft/unverified edits → one bulk-patch. Verified rows
        # still go through per-row pending-edit (rarer, different semantics).
        inplace_updates: list[tuple[int, object, dict]] = []
        pending_edit_rows: list[tuple[int, object, dict]] = []
        for row in sorted(self._dirty_rows):
            if parked_only and not self._row_marked_parked(row):
                continue
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
            if orig is not None and getattr(orig, "original_transaction_id", None):
                updates["edited_after_verification"] = True
                inplace_updates.append((row, tx_id, updates))
            elif orig is not None and orig.verified:
                pending_edit_rows.append((row, tx_id, updates))
            elif orig is not None and (getattr(orig, "register_status", "") or "") == "draft":
                inplace_updates.append((row, tx_id, updates))
            elif orig is not None and (getattr(orig, "register_status", "") or "") == "parked":
                # Parked truck follow-up → draft (Fix trucks / typed truck + Save).
                inplace_updates.append((row, tx_id, updates))
            elif orig is not None:
                updates["edited_after_verification"] = True
                if getattr(orig, "rejected", False):
                    updates["rejected"] = False
                    updates["rejection_reason"] = None
                    updates["discarded"] = False
                inplace_updates.append((row, tx_id, updates))
            else:
                inplace_updates.append((row, tx_id, updates))

        if inplace_updates:
            try:
                self._show_register_loading(
                    f"Updating {len(inplace_updates)} saved row"
                    f"{'s' if len(inplace_updates) != 1 else ''}…"
                )
                await bulk_update_transactions(
                    [(tx_id, body) for _row, tx_id, body in inplace_updates]
                )
                for row, _tx_id, _body in inplace_updates:
                    updated += 1
                    self._dirty_rows.discard(row)
            except Exception:
                # Fall back to per-row PATCH so one bad row does not block all.
                for row, tx_id, body in inplace_updates:
                    try:
                        await update_transaction(tx_id, body)
                        updated += 1
                        self._dirty_rows.discard(row)
                    except Exception as row_exc:
                        errors.append(f"Row {row + 1}: {row_exc}")

        for row, tx_id, updates in pending_edit_rows:
            try:
                await insert_pending_edit(tx_id, updates, self._user._id)
                updated += 1
                self._dirty_rows.discard(row)
            except Exception as exc:
                errors.append(f"Row {row + 1}: {exc}")

        # ── Pass 2: insert brand-new rows (INSERT) — My + parked ───
        pending_inserts: list[tuple[int, Transaction]] = []
        queued = 0
        if not self._merged_mode:
            dup_days = int(getattr(self, "_duplicate_check_days", 5) or 5)

            # My Save: bring local Parked bucket onto the grid so inserts cover them.
            if not self._parked_mode and self._local_parked:
                self._materialize_local_parked_for_save()

            # Pre-scan: warn once if any new draft rows carry a date other than the open day.
            # Parked follow-up rows skip this dialog.
            if not parked_only:
                register_label = self._current_date.strftime("%d %b %Y")
                _off_date = 0
                for _s in range(self._saved_count, self._table.rowCount()):
                    if not self._row_has_data(_s) or self._row_stays_parked(_s):
                        continue
                    _it = self._table.item(_s, COL_DATE)
                    _ds = _it.text().strip() if _it else ""
                    _parsed = _parse_optional_date(
                        _ds, default_year=self._current_date.year
                    )
                    if _parsed is not None:
                        _td = _parsed.date()
                    else:
                        _td = self._current_date
                    if _td != self._current_date:
                        _off_date += 1
                if _off_date:
                    _plural = "s" if _off_date != 1 else ""
                    _are = "are" if _off_date != 1 else "is"
                    if QMessageBox.warning(
                        self, "Off-date Entries",
                        f"{_off_date} row{_plural} {_are} not dated {register_label}.\n\n"
                        "They will stay on this register day (visible after Save) but "
                        "will be flagged in the accountant's Verify inbox.\n\n"
                        "Proceed with save?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    ) == QMessageBox.No:
                        return False

            duplicate_items: list[DuplicateReviewItem] = []
            append_order = None
            try:
                append_order = await next_day_order(self._current_date)
            except Exception:
                append_order = None

            for row in range(self._saved_count, self._table.rowCount()):
                if not self._row_has_data(row):
                    continue
                if parked_only and not self._row_marked_parked(row):
                    continue
                try:
                    tx = self._build_transaction_from_row(row)
                except ValueError as exc:
                    errors.append(f"Row {row + 1}: {exc}")
                    continue
                if tx is None:
                    continue

                if append_order is not None:
                    tx.day_order = append_order
                    append_order += 1
                else:
                    tx.day_order = row
                pending_inserts.append((row, tx))

            # Soft-dup scan for draft rows only (parked follow-up skips confirm UI).
            draft_inserts = [
                (row, tx) for row, tx in pending_inserts
                if (getattr(tx, "register_status", "") or "") != "parked"
            ]
            dup_hits: list = [[] for _ in draft_inserts]
            if draft_inserts and not parked_only:
                try:
                    self._show_register_loading(
                        f"Checking duplicates ({len(draft_inserts)})…"
                    )
                    dup_hits = await check_for_duplicates_bulk(
                        [
                            {
                                "truck_number": tx.truck_number or "",
                                "amount": tx.amount,
                                "item": tx.item or "",
                                "description": tx.description or "",
                            }
                            for _row, tx in draft_inserts
                        ],
                        dup_days,
                    )
                except Exception:
                    dup_hits = [[] for _ in draft_inserts]

            for i, (row, tx) in enumerate(draft_inserts):
                dupes = dup_hits[i] if i < len(dup_hits) else []
                if dupes:
                    duplicate_items.append(DuplicateReviewItem(
                        row=row,
                        row_display=row + 1,
                        description=tx.description or "—",
                        truck_number=tx.truck_number or "",
                        item=tx.item or "",
                        amount=tx.amount,
                        amount_label=format_amount_label(tx),
                        existing=dupes[0],
                        date_label=format_new_date(tx.date),
                    ))

            save_anyway: set[int] = set()
            if duplicate_items and not parked_only:
                dlg = DuplicateReviewDialog(
                    duplicate_items,
                    dup_days=dup_days,
                    parent=self,
                    pending_total=len(draft_inserts),
                )
                if dlg.exec() != QDialog.Accepted:
                    self._flush_local_draft()
                    return False
                save_anyway = dlg.save_anyway_rows()
                deferred_dups = dlg.deferred_duplicate_rows()

            duplicate_rows = {item.row for item in duplicate_items}
            match_by_row = {
                item.row: item.existing
                for item in duplicate_items
                if getattr(item.existing, "_id", None) is not None
            }
            to_save: list[tuple[int, Transaction]] = []
            for row, tx in pending_inserts:
                if row in duplicate_rows and row not in save_anyway:
                    continue
                tx.possible_duplicate = row in save_anyway
                if row in save_anyway:
                    matched = match_by_row.get(row)
                    tx.duplicate_of_id = (
                        matched._id if matched is not None else None
                    )
                else:
                    tx.duplicate_of_id = None
                to_save.append((row, tx))
            # Ensure deferred dups are ordinary unsaved rows (no sync_status).
            for row in deferred_dups:
                row_meta = dict(self._pending_row_meta.get(row) or {})
                if "sync_status" in row_meta:
                    row_meta.pop("sync_status", None)
                    self._pending_row_meta[row] = row_meta

            # Parked inserts always sync so they leave the local bucket immediately.
            # Draft inserts on My may use the async outbox (unchanged).
            parked_to_save = [
                (row, tx) for row, tx in to_save
                if (getattr(tx, "register_status", "") or "") == "parked"
            ]
            draft_to_save = [
                (row, tx) for row, tx in to_save
                if (getattr(tx, "register_status", "") or "") != "parked"
            ]

            async def _sync_create_chunked(
                rows_txs: list[tuple[int, Transaction]],
                *,
                label: str,
                one_request: bool = False,
            ) -> None:
                nonlocal saved, already_exists
                total_to_save = len(rows_txs)
                chunk_size = total_to_save if one_request else BULK_CREATE_CHUNK
                for chunk_start in range(0, total_to_save, chunk_size):
                    chunk = rows_txs[chunk_start : chunk_start + chunk_size]
                    lo = chunk_start + 1
                    hi = chunk_start + len(chunk)
                    self._show_register_loading(
                        f"{label}… {lo}–{hi} of {total_to_save}"
                        if total_to_save > chunk_size
                        else f"{label}… ({total_to_save})"
                    )
                    try:
                        txs = [tx for _r, tx in chunk]
                        if one_request:
                            from tahmeed.services.cashier_service import (
                                bulk_save_one_request,
                            )
                            with bulk_save_one_request():
                                outcomes = await bulk_save_transactions(txs)
                        else:
                            outcomes = await bulk_save_transactions(txs)
                    except Exception as exc:
                        for row, _tx in chunk:
                            errors.append(f"Row {row + 1}: {exc}")
                        continue
                    for (row, tx), (status_key, _saved, err) in zip(
                        chunk, outcomes
                    ):
                        if status_key in ("created", "already_exists"):
                            saved += 1
                            if status_key == "already_exists":
                                already_exists += 1
                            rid = (
                                getattr(tx, "client_row_id", None)
                                or self._ensure_row_client_id(row)
                            )
                            if rid and (
                                getattr(tx, "register_status", "") or ""
                            ) == "parked":
                                parked_saved_ids.add(str(rid))
                            self._mark_new_row_saved(row, flush=False)
                        else:
                            errors.append(
                                f"Row {row + 1}: "
                                f"{err or status_key or 'save failed'}"
                            )
                    self._flush_local_draft()

            if parked_to_save:
                await _sync_create_chunked(
                    parked_to_save, label="Saving parked", one_request=True,
                )
                if parked_saved_ids:
                    self._drop_local_parked_by_client_ids(parked_saved_ids)

            use_async_outbox = (
                bool(draft_to_save)
                and not self._submit_in_flight
                and not self._merged_mode
                and not self._parked_mode
            )
            if use_async_outbox:
                queued = self._enqueue_inserts_for_outbox(draft_to_save)
                self._kick_outbox_after_save = True
                if meta is not None:
                    meta["queued"] = queued
                    meta["async_outbox"] = True
                log_register_event(
                    "outbox_enqueue",
                    day=str(self._current_date or ""),
                    rows=queued,
                    online=True,
                )
            elif draft_to_save:
                await _sync_create_chunked(
                    draft_to_save, label="Saving draft"
                )

        if self._merged_mode and (saved or updated):
            try:
                await self._persist_visual_day_order()
            except Exception as exc:
                errors.append(f"Could not save row order: {exc}")

        if meta is not None:
            meta["saved"] = saved
            meta["updated"] = updated
            meta["already_exists"] = already_exists
            meta["queued"] = queued
            meta["errors"] = len(errors)
            meta["deferred_dups"] = len(deferred_dups)
            meta["kept_parked"] = kept_parked
            if parked_only:
                meta["parked_only"] = True

        if saved or updated or queued:
            # Server / outbox changed — My↔Parked must not reuse stale day cache.
            self._invalidate_day_tx_cache()

        if errors:
            QMessageBox.warning(
                self, "Save — partial errors",
                f"{saved} added, {updated} updated.\n\nErrors:\n"
                + "\n".join(errors),
            )
            self._flush_local_draft()
            if saved or updated:
                self.drafts_changed.emit()
                if self._current_date is not None:
                    await self._load_date(self._current_date)
            elif queued:
                # Updates failed but inserts are durable in the outbox — drain.
                self._kick_outbox_after_save = True
            return False

        if saved == 0 and updated == 0 and queued == 0:
            # Cashier deferred possible duplicates — leave them on the table.
            live_deferred = [
                row for row in deferred_dups
                if row >= self._saved_count and self._row_has_data(row)
            ]
            if live_deferred:
                self._flush_local_draft()
                n = len(live_deferred)
                plural = "" if n == 1 else "s"
                self.transient_status.emit(
                    f"Kept {n} possible duplicate{plural} on the table — "
                    "Save when ready"
                )
                if meta is not None:
                    meta["deferred_only"] = True
                return True
            if parked_only:
                # Other unsaved (non-parked) rows may remain — do not warn.
                return True
            if self.has_unsaved_work(include_stashed_my=False):
                # Typed rows that could not become transactions (e.g. no
                # description) — keep local recovery; do not claim success.
                QMessageBox.warning(
                    self,
                    "Nothing saved",
                    "No rows could be saved as draft.\n\n"
                    "Each draft row needs at least a Description.",
                )
                self._flush_local_draft()
                return False
            QMessageBox.information(self, "Nothing to save", "No changes to save.")
            self._clear_local_draft(both_modes=True)
            return True

        # Async outbox path: keep PENDING draft rows on disk; drain promotes them.
        # Also used when parked rows synced immediately and drafts are still queued.
        if queued:
            if parked_only:
                # Leave non-parked dirty/new rows editable on My.
                self._dirty_rows = {
                    r for r in self._dirty_rows if not self._row_marked_parked(r)
                }
                if self._dirty_rows:
                    self.edit_state_changed.emit(True, len(self._dirty_rows))
                else:
                    self._edit_mode = False
                    self.edit_state_changed.emit(False, 0)
                self._emit_active_cheque()
            else:
                self._reset_edit_state()
            if saved or updated:
                self.rows_saved.emit(saved)
                self.drafts_changed.emit()
                self._invalidate_day_tx_cache()
            if not self._submit_in_flight and not parked_only:
                plural = "" if queued == 1 else "s"
                parts = [f"Saved locally · syncing {queued} draft{plural}"]
                if parked_saved_ids:
                    parts.insert(
                        0, f"{len(parked_saved_ids)} parked saved"
                    )
                if updated:
                    parts.append(f"{updated} updated")
                if deferred_dups:
                    n_def = len([
                        r for r in deferred_dups
                        if r >= self._saved_count and self._row_has_data(r)
                    ])
                    if n_def:
                        parts.append(
                            f"{n_def} duplicate{'s' if n_def != 1 else ''} kept"
                        )
                self.transient_status.emit(" · ".join(parts) + "…")
            return True

        if parked_only:
            # Do not wipe remaining non-parked unsaved work / local draft.
            self._dirty_rows = {
                r for r in self._dirty_rows if not self._row_marked_parked(r)
            }
            if self._dirty_rows:
                self.edit_state_changed.emit(True, len(self._dirty_rows))
            else:
                self._edit_mode = False
                self.edit_state_changed.emit(False, 0)
            self._emit_active_cheque()
            self._flush_local_draft()
            if saved or updated:
                self.rows_saved.emit(saved)
                self.drafts_changed.emit()
            return True

        self._clear_local_draft(both_modes=True)
        self._reset_edit_state()
        self.rows_saved.emit(saved)
        self.drafts_changed.emit()
        # Updates-only: cells already match — skip full reload.
        # Sync insert path (Submit): reload so Verify sees server state.
        # Parked: always reload so saved parked / fixed trucks refresh the views.
        if self._current_date is not None and (saved or self._parked_mode):
            await self._load_date(self._current_date)
        if not self._submit_in_flight:
            parts = []
            if parked_saved_ids:
                parts.append(f"{len(parked_saved_ids)} parked")
            draft_n = max(0, saved - len(parked_saved_ids))
            if draft_n:
                parts.append(f"{draft_n} draft")
            if updated:
                parts.append(f"{updated} updated")
            summary = ", ".join(parts) if parts else "saved"
            if self._parked_mode:
                self.transient_status.emit(
                    f"Parked saved: {summary} · Fixed trucks return to My entries"
                )
            else:
                self.transient_status.emit(
                    f"Saved: {summary} · Open Drafts to submit for Verify"
                )
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
        self._refresh_truck_required_highlights()

    async def _load_categories(self) -> None:
        """Ensure the Item column lists expense items and suppliers."""
        await self._load_register_catalog()

    async def _load_register_catalog(self, *, refresh: bool = False) -> None:
        """One catalog snapshot for items, payees, fleet, and cashier settings."""
        from tahmeed.services.catalog_service import (
            categories_from_snapshot,
            fetch_catalog_snapshot,
            setting_from_snapshot,
        )
        from tahmeed.services.category_service import sort_payment_targets
        from tahmeed.services.export_restriction_service import normalize_export_surfaces
        from tahmeed.services.truck_service import get_fleet_kinds, get_fleet_numbers

        try:
            snapshot = await fetch_catalog_snapshot(refresh=refresh)
        except Exception:
            return
        self._catalog_applied = True
        cats = sort_payment_targets(categories_from_snapshot(snapshot))
        if cats:
            self.update_categories(cats)
            self._revalidate_visible_item_cells()
        self._apply_catalog_settings(snapshot, setting_from_snapshot, normalize_export_surfaces)
        try:
            self._fleet_numbers = await get_fleet_numbers()
            self._fleet_kinds = await get_fleet_kinds()
        except Exception:
            self._fleet_numbers = set()
            self._fleet_kinds = {}
        names = [
            str(document.get("name") or "").strip().upper()
            for document in snapshot.get("payees") or []
            if isinstance(document, dict) and str(document.get("name") or "").strip()
        ]
        self.update_payees(names)

    def _apply_catalog_settings(self, snapshot, setting_from_snapshot, normalize_export_surfaces) -> None:
        self._restrict_items = bool(setting_from_snapshot(snapshot, "restrict_items", False))
        self._restrict_payees = bool(setting_from_snapshot(snapshot, "restrict_payees", False))
        self._defer_item_to_verify = bool(
            setting_from_snapshot(snapshot, "defer_item_to_verify", False)
        )
        auto_fill = setting_from_snapshot(snapshot, "auto_fill_from_description_maps", True)
        self._auto_fill_from_maps = True if auto_fill is None else bool(auto_fill)
        try:
            self._duplicate_check_days = int(
                setting_from_snapshot(snapshot, "duplicate_check_days", 5) or 5
            )
        except (TypeError, ValueError):
            self._duplicate_check_days = 5
        self._restrict_trucks = True
        surfaces = setting_from_snapshot(snapshot, "export_restrict_surfaces", None)
        self._export_restrict_surfaces = set(normalize_export_surfaces(surfaces))
        raw = snapshot.get("allowed_truck_labels")
        if not isinstance(raw, list) or not raw:
            raw = setting_from_snapshot(snapshot, "allowed_truck_labels", None)
        if isinstance(raw, list) and raw:
            self._allowed_truck_labels = merge_allowed_labels(raw, DEFAULT_PLACE_LABELS)
        else:
            self._allowed_truck_labels = set(DEFAULT_PLACE_LABELS)
        current = setting_from_snapshot(snapshot, "restrict_trucks", True)
        if current is not True:
            asyncio.ensure_future(self._force_restrict_trucks())

    async def _force_restrict_trucks(self) -> None:
        try:
            await set_setting("restrict_trucks", True)
        except Exception:
            self._restrict_trucks = True

    async def _load_cashier_settings(self) -> None:
        await self._load_register_catalog()

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

    def _apply_mapping_fields_to_row(
        self, row: int, cat_name: str, payee: str
    ) -> bool:
        """Fill blank Item/Payee on *row* from mapping fields. Returns True if changed."""
        item_it = self._table.item(row, COL_ITEM)
        payee_it = self._table.item(row, COL_PAYEE)
        need_item = not (item_it and item_it.text().strip())
        need_payee = not (payee_it and payee_it.text().strip())
        if not need_item and not need_payee:
            return False
        cat_name = (cat_name or "").strip()
        payee = (payee or "").strip()
        if not cat_name and not payee:
            return False

        prev = self._table.blockSignals(True)
        filled_item = None
        changed = False
        try:
            if need_item and cat_name:
                if item_it is None:
                    item_it = QTableWidgetItem(cat_name)
                    item_it.setFlags(
                        Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
                    )
                    self._table.setItem(row, COL_ITEM, item_it)
                else:
                    item_it.setText(cat_name)
                filled_item = item_it
                changed = True
            if need_payee and payee:
                payee_text = payee.strip().upper()
                if payee_it is None:
                    payee_it = QTableWidgetItem(payee_text)
                    payee_it.setFlags(
                        Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
                    )
                    self._table.setItem(row, COL_PAYEE, payee_it)
                else:
                    payee_it.setText(payee_text)
                changed = True
        finally:
            self._table.blockSignals(prev)
        if filled_item is not None:
            self._validate_item_cell(row, filled_item)
            self._update_truck_required_highlight(row)
        return changed

    async def _auto_fill_from_mapping(
        self, row: int, description: str = "", *, seq: int | None = None
    ) -> None:
        """Pre-fill Item/Payee from an exact Description Map (blank cells only).

        No map → leave blank (history suggestions do not invent Item/Payee).
        Lookup uses the warm Description Maps cache — silent, no register horse.
        """
        try:
            if not getattr(self, "_auto_fill_from_maps", True):
                return
            if seq is not None and seq != self._auto_fill_seq:
                return
            # Re-read after Tab/uppercase settle so lookup uses the committed text.
            live = self._cell_text(row, COL_DESC).strip() or (description or "").strip()
            if not live:
                return
            item_it = self._table.item(row, COL_ITEM)
            payee_it = self._table.item(row, COL_PAYEE)
            need_item = not (item_it and item_it.text().strip())
            need_payee = not (payee_it and payee_it.text().strip())
            if not need_item and not need_payee:
                return

            from tahmeed.services.description_mapping_service import (
                resolve_mapping_for_description,
            )

            try:
                mapping = await resolve_mapping_for_description(live)
            except Exception:
                return
            if seq is not None and seq != self._auto_fill_seq:
                return
            if mapping is None:
                return
            cat_name = (getattr(mapping, "category_name", None) or "").strip()
            payee = (getattr(mapping, "payee", None) or "").strip()
            if self._apply_mapping_fields_to_row(row, cat_name, payee):
                self._notify_day_issues_source_changed()
        finally:
            if self._auto_fill_row == row and (
                seq is None or seq == self._auto_fill_seq
            ):
                self._auto_fill_row = None
            self._schedule_finalize_row_edit()

    async def _auto_fill_rows_from_mappings(self, rows: list) -> None:
        """Batch exact-map auto-fill after paste — warm cache or one keyed lookup."""
        try:
            if not getattr(self, "_auto_fill_from_maps", True):
                return
            from tahmeed.services.description_mapping_service import (
                get_mappings_for_descriptions,
                normalize_description,
            )

            targets: list[tuple[int, str]] = []
            for row in rows:
                if row < 0 or row >= self._table.rowCount():
                    continue
                if row < self._saved_count and not self._edit_mode:
                    continue
                desc = self._cell_text(row, COL_DESC).strip()
                if not desc:
                    continue
                item_blank = not self._cell_text(row, COL_ITEM).strip()
                payee_blank = not self._cell_text(row, COL_PAYEE).strip()
                if not item_blank and not payee_blank:
                    continue
                targets.append((row, desc))
            if not targets:
                return

            try:
                mappings = await get_mappings_for_descriptions(
                    [desc for _r, desc in targets]
                )
            except Exception:
                return

            changed = False
            for row, desc in targets:
                mapping = mappings.get(normalize_description(desc))
                if mapping is None:
                    continue
                cat_name = (getattr(mapping, "category_name", None) or "").strip()
                payee = (getattr(mapping, "payee", None) or "").strip()
                if self._apply_mapping_fields_to_row(row, cat_name, payee):
                    changed = True
                    if row < self._saved_count and self._edit_mode:
                        self._mark_dirty(row)
            if changed:
                self._schedule_draft_autosave()
        finally:
            self._flush_day_issues_source_changed(force=True)

    async def _load_fleet_numbers(self) -> None:
        from tahmeed.services.catalog_service import catalog_is_stale

        await self._load_register_catalog(refresh=catalog_is_stale())

    async def _load_description_cache(self) -> None:
        """Warm system-wide description history for Excel-style autocomplete."""
        from tahmeed.services.cashier_service import ensure_description_cache
        try:
            await ensure_description_cache()
        except Exception:
            pass

    async def _load_description_mapping_cache(self) -> None:
        """Warm Description Maps for silent Item/Payee auto-fill on type/paste."""
        from tahmeed.services.description_mapping_service import (
            ensure_description_mapping_cache,
        )
        try:
            await ensure_description_mapping_cache()
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
        """Mark an unknown Item cell (restrict on) until resolved via prompt."""
        item.setForeground(QBrush(NEG_COLOR))
        item.setToolTip(
            f'"{text}" is not in the Item list. Cancel to clear, or Add Item.'
        )

    def _flag_unknown_payee(self, item: QTableWidgetItem, text: str) -> None:
        item.setForeground(QBrush(NEG_COLOR))
        item.setToolTip(
            f'"{text}" is not in the Payee list. Cancel to clear, or Add Payee.'
        )

    def _clear_catalog_flag(self, row: int, item: QTableWidgetItem) -> None:
        tip = item.toolTip() or ""
        if "is not in the Item list" not in tip and "is not in the Payee list" not in tip:
            # Legacy flag wording from older builds.
            if "is not a known item" not in tip and "is not a known payee" not in tip:
                return
        item.setToolTip("")
        item.setForeground(QBrush())
        if item.background().color() == DUP_BG:
            item.setBackground(self._item_row_background(row))

    def _clear_item_flag(self, row: int, item: QTableWidgetItem) -> None:
        self._clear_catalog_flag(row, item)

    def _set_catalog_cell_text(self, row: int, col: int, text: str) -> None:
        it = self._table.item(row, col)
        if it is None:
            it = QTableWidgetItem(text)
            self._table.blockSignals(True)
            self._table.setItem(row, col, it)
            self._table.blockSignals(False)
        else:
            self._table.blockSignals(True)
            it.setText(text)
            self._table.blockSignals(False)
        self._clear_catalog_flag(row, it)

    def _clear_catalog_cell(self, row: int, col: int) -> None:
        self._set_catalog_cell_text(row, col, "")
        if col == COL_ITEM:
            self._update_truck_required_highlight(row)

    def _validate_item_cell(self, row: int, item: QTableWidgetItem) -> None:
        text = item.text().strip()
        if not text:
            self._clear_item_flag(row, item)
            self._update_truck_required_highlight(row)
            return
        cat = self._cat_by_name.get(text.lower())
        if cat is not None:
            canonical = cat.name.upper()
            if item.text() != canonical:
                self._table.blockSignals(True)
                item.setText(canonical)
                self._table.blockSignals(False)
            self._clear_item_flag(row, item)
            self._update_truck_required_highlight(row)
            return
        if not self._restrict_items:
            self._clear_item_flag(row, item)
            self._update_truck_required_highlight(row)
            return
        # Do not flag every cell when the catalog failed to load — that paints
        # known names red and makes Restrict look broken.
        if not self._categories:
            return
        self._flag_unknown_item(item, text)
        self._update_truck_required_highlight(row)
        if self._bulk_mutating:
            return
        QTimer.singleShot(
            0, lambda r=row, t=text: self._offer_add_unknown_item(r, t)
        )

    def _validate_payee_cell(self, row: int, item: QTableWidgetItem) -> None:
        text = item.text().strip()
        if not text:
            self._clear_catalog_flag(row, item)
            return
        known = next(
            (p for p in self._payee_names if p.upper() == text.upper()),
            None,
        )
        if known is not None:
            canonical = known.upper()
            if item.text() != canonical:
                self._table.blockSignals(True)
                item.setText(canonical)
                self._table.blockSignals(False)
            self._clear_catalog_flag(row, item)
            return
        if not self._restrict_payees:
            self._clear_catalog_flag(row, item)
            return
        if not self._payee_names:
            return
        self._flag_unknown_payee(item, text)
        if self._bulk_mutating:
            return
        QTimer.singleShot(
            0, lambda r=row, t=text: self._offer_add_unknown_payee(r, t)
        )

    def _offer_add_unknown_item(self, row: int, text: str) -> None:
        key = (row, COL_ITEM, text)
        if key in self._not_in_list_prompting:
            return
        it = self._table.item(row, COL_ITEM)
        if it is None or it.text().strip() != text:
            return
        if self._cat_by_name.get(text.lower()) is not None:
            return
        if not self._restrict_items or self._bulk_mutating:
            return
        self._not_in_list_prompting.add(key)
        try:
            from tahmeed.ui.dialogs.not_in_list_dialog import (
                ask_not_in_list,
                run_add_item_dialog,
            )

            if not ask_not_in_list(self, entity="Item", name=text):
                self._clear_catalog_cell(row, COL_ITEM)
                return
            data = run_add_item_dialog(self, prefill_name=text)
            if not data:
                self._clear_catalog_cell(row, COL_ITEM)
                return
            asyncio.ensure_future(self._finish_add_item_from_prompt(row, data))
        finally:
            self._not_in_list_prompting.discard(key)

    def _offer_add_unknown_payee(self, row: int, text: str) -> None:
        key = (row, COL_PAYEE, text)
        if key in self._not_in_list_prompting:
            return
        it = self._table.item(row, COL_PAYEE)
        if it is None or it.text().strip() != text:
            return
        if any(p.upper() == text.upper() for p in self._payee_names):
            return
        if not self._restrict_payees or self._bulk_mutating:
            return
        self._not_in_list_prompting.add(key)
        try:
            from tahmeed.ui.dialogs.not_in_list_dialog import (
                ask_not_in_list,
                run_add_payee_dialog,
            )

            if not ask_not_in_list(self, entity="Payee", name=text):
                self._clear_catalog_cell(row, COL_PAYEE)
                return
            created = run_add_payee_dialog(self, prefill=text)
            if not created:
                self._clear_catalog_cell(row, COL_PAYEE)
                return
            name, currency = created
            asyncio.ensure_future(
                self._finish_add_payee_from_prompt(row, name, currency=currency)
            )
        finally:
            self._not_in_list_prompting.discard(key)

    async def _finish_add_item_from_prompt(self, row: int, data: dict) -> None:
        from tahmeed.services.mapping_assignment_service import create_item_for_mapping

        try:
            cat = await create_item_for_mapping(data.get("name") or "", data)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create item:\n{exc}")
            self._clear_catalog_cell(row, COL_ITEM)
            return
        # Refresh local catalog so completer + save validation see the new item.
        existing = self._cat_by_name.get(cat.name.lower())
        if existing is None:
            self._categories.append(cat)
        else:
            idx = next(
                (i for i, c in enumerate(self._categories) if c.name.lower() == cat.name.lower()),
                -1,
            )
            if idx >= 0:
                self._categories[idx] = cat
        self._cat_by_name[cat.name.lower()] = cat
        self._set_catalog_cell_text(row, COL_ITEM, cat.name.upper())
        self._update_truck_required_highlight(row)
        self._notify_day_issues_source_changed()

    async def _finish_add_payee_from_prompt(
        self, row: int, name: str, *, currency: str = "TZS",
    ) -> None:
        from tahmeed.services.payee_service import add_payee

        try:
            await add_payee(name, currency=currency or "TZS")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create payee:\n{exc}")
            self._clear_catalog_cell(row, COL_PAYEE)
            return
        normalized = " ".join(name.upper().split())
        if not any(p.upper() == normalized for p in self._payee_names):
            self._payee_names.append(normalized)
            self._payee_names.sort()
        self._set_catalog_cell_text(row, COL_PAYEE, normalized)
        self._notify_day_issues_source_changed()
    # ------------------------------------------------------------------
    # Truck-column validation (format + restrict to fleet registry)
    # ------------------------------------------------------------------

    def _cell_text(self, row: int, col: int) -> str:
        it = self._table.item(row, col)
        return it.text().strip() if it else ""

    def _item_requires_truck(self, item_name: str) -> bool:
        if not item_name:
            return False
        cat = self._cat_by_name.get(item_name.lower())
        if cat is None:
            return False
        return bool(getattr(cat, "requires_truck", True))

    def _row_missing_required_truck(self, row: int) -> bool:
        if not self._cell_text(row, COL_DESC):
            return False
        if self._row_accepts_empty_truck(row):
            return False
        item_name = self._cell_text(row, COL_ITEM)
        if not self._item_requires_truck(item_name):
            return False
        return not self._cell_text(row, COL_TRUCK)

    def _rows_missing_required_truck(self) -> list[int]:
        missing: list[int] = []
        seen: set[int] = set()
        for row in self._dirty_rows:
            if row in seen:
                continue
            if self._row_missing_required_truck(row):
                missing.append(row)
                seen.add(row)
        for row in range(self._saved_count, self._table.rowCount()):
            if row in seen or not self._row_has_data(row):
                continue
            if self._row_missing_required_truck(row):
                missing.append(row)
                seen.add(row)
        return sorted(missing)

    _TRUCK_REQUIRED_TIP = "Truck number is required for this item."

    def _truck_cell_background(self, row: int) -> QBrush:
        if row >= self._saved_count:
            return QBrush(NEW_BG)
        if row in self._dirty_rows:
            return QBrush(DIRTY_BG)
        if self._edit_mode:
            return QBrush(EDIT_BG)
        tx = self._saved_txs.get(row)
        if tx is not None and (getattr(tx, "register_status", "") or "submitted") == "draft":
            return QBrush(DRAFT_BG)
        return QBrush(SAVED_BG)

    def _ensure_truck_cell(self, row: int) -> QTableWidgetItem:
        it = self._table.item(row, COL_TRUCK)
        if it is not None:
            return it
        it = QTableWidgetItem("")
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if row >= self._saved_count or self._edit_mode:
            flags |= Qt.ItemIsEditable
        it.setFlags(flags)
        it.setBackground(self._truck_cell_background(row))
        self._table.setItem(row, COL_TRUCK, it)
        return it

    def _update_truck_required_highlight(self, row: int) -> None:
        if row < 0 or row >= self._table.rowCount():
            return
        if row < self._saved_count and not self._edit_mode and not self._cell_text(row, COL_DESC):
            return
        truck_it = self._table.item(row, COL_TRUCK)
        if self._row_missing_required_truck(row):
            truck_it = self._ensure_truck_cell(row)
            prev = self._table.blockSignals(True)
            truck_it.setBackground(QBrush(TRUCK_REQUIRED_BG))
            truck_it.setToolTip(self._TRUCK_REQUIRED_TIP)
            self._table.blockSignals(prev)
            return
        if truck_it is None:
            return
        if truck_it.background().color() != TRUCK_REQUIRED_BG:
            return
        prev = self._table.blockSignals(True)
        if (truck_it.toolTip() or "") == self._TRUCK_REQUIRED_TIP:
            truck_it.setToolTip("")
        truck_it.setBackground(self._truck_cell_background(row))
        self._table.blockSignals(prev)

    def _refresh_truck_required_highlights(self) -> None:
        prev = self._table.blockSignals(True)
        try:
            for row in range(self._table.rowCount()):
                if not self._cell_text(row, COL_DESC):
                    continue
                self._update_truck_required_highlight(row)
        finally:
            self._table.blockSignals(prev)

    def _can_add_fleet(self) -> bool:
        return getattr(self._user, "role", "") in ("admin", "accountant")

    def _truck_issue_row_details(self, row: int) -> dict:
        """Snapshot visible grid cells for the truck correction Details tab."""
        details: dict[str, str] = {}
        for col, header in enumerate(HEADERS):
            if col == COL_SNO:
                continue
            it = self._table.item(row, col)
            text = (it.text().strip() if it else "")
            if text:
                details[header] = text
        return details

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
        self._update_truck_required_highlight(row)

    def _truck_key(self, text: str) -> str:
        return " ".join((text or "").upper().split())

    def _resolve_truck_text(
        self, raw: str, row: Optional[int] = None
    ) -> tuple[str, Optional[str]]:
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
            # Bikes/cars (and any freeform fleet plate) skip truck format —
            # registry membership wins (parity with import_truck_check).
            matched = try_match_fleet(raw, self._fleet_numbers)
            if matched is not None:
                return "ok", matched
            allowed = self._truck_allow_anyway.get(row) if row is not None else None
            if allowed and self._truck_key(raw) == self._truck_key(allowed):
                return "ok", allowed
            return "invalid_format", norm.value or raw.strip().upper()
        matched = try_match_fleet(norm.value, self._fleet_numbers)
        if matched is None:
            allowed = self._truck_allow_anyway.get(row) if row is not None else None
            if allowed and self._truck_key(raw) == self._truck_key(allowed):
                return "ok", allowed
            return "not_in_registry", norm.value
        return "ok", matched

    def _finalize_truck_cells(self, cells: list) -> None:
        """Normalize / validate truck cells; queue one combined correction dialog.

        ``cells`` is a list of (row, raw_text) for truck values just written.
        """
        if not cells:
            return

        for row, raw in cells:
            status, value = self._resolve_truck_text(raw, row=row)
            if status == "empty":
                self._set_truck_cell(row, "")
                self._pending_truck_issues.pop(row, None)
            elif status == "ok":
                self._set_truck_cell(row, value)
                self._pending_truck_issues.pop(row, None)
            elif status == "invalid_format":
                self._set_truck_cell(row, value)
                self._pending_truck_issues[row] = TruckIssue(
                    row=row,
                    original=raw,
                    kind="invalid_format",
                    row_details=self._truck_issue_row_details(row),
                )
            else:
                self._set_truck_cell(row, value)
                self._pending_truck_issues[row] = TruckIssue(
                    row=row,
                    original=raw,
                    kind="not_in_registry",
                    row_details=self._truck_issue_row_details(row),
                )

        for row, _raw in cells:
            self._update_truck_required_highlight(row)
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
            status, value = self._resolve_truck_text(current, row=issue.row)
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

        # Parked surface: never offer Park again — Accept/Allow promote to My.
        fixing_parked = bool(self._parked_mode)
        dlg = TruckCorrectionDialog(
            live,
            self._fleet_numbers,
            can_add=self._can_add_fleet(),
            allowed_labels=self._allowed_truck_labels,
            on_resolved=self._on_truck_issue_resolved_live,
            fleet_kinds=getattr(self, "_fleet_kinds", None) or {},
            heading="Fix parked trucks" if fixing_parked else "",
            intro=(
                "These rows are already parked. Correct, Allow, or choose No truck — "
                "accepted rows move to My entries right away."
                if fixing_parked
                else ""
            ),
            allow_park=not fixing_parked,
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
        if fixing_parked:
            # Accept/Allow already wrote cells; promote off Parked — never re-park.
            promoted = self._promote_accepted_parked_rows_to_my()
            if not promoted:
                self._local_parked = self._table_parked_entries()
                self._emit_parked_count()
                self._update_footer()
                self._flush_day_issues_source_changed(force=True)
                self._schedule_draft_autosave()
            return
        parked_any = any(getattr(i, "parked", False) for i in dlg.issues)
        if result != QDialog.Accepted:
            resolved_rows = {i.row for i in dlg.issues}
            for issue in live:
                if issue.row not in resolved_rows:
                    self._set_truck_cell(issue.row, "")
        if parked_any:
            self._after_truck_dialog_parked()

    def _after_truck_dialog_parked(self) -> None:
        """Park all / Park remaining / Park row outcome.

        Unsaved rows (typed or imported) move into the local Parked bucket
        without touching the server — correct rows stay on My ready for Save.
        Only rows that are *already saved* on the server still need a server
        update to carry ``register_status=parked`` (legacy follow-up path).

        Never re-park from the Parked surface — promote accepted trucks instead.
        """
        if self._parked_mode:
            self._promote_accepted_parked_rows_to_my()
            return
        self._park_rows_locally()
        saved_parked = [
            row for row, meta in self._pending_row_meta.items()
            if isinstance(meta, dict)
            and meta.get("parked")
            and row in self._saved_ids
            and (
                getattr(self._saved_txs.get(row), "register_status", "") or ""
            ) != "parked"
        ]
        if saved_parked:
            asyncio.ensure_future(self._persist_parked_after_dialog())

    def open_parked_truck_correction(self) -> None:
        """Open truck correction for rows currently on the Parked surface.

        Park actions are hidden — these rows are already parked. Cashiers can
        still type truck fixes directly in the table instead of using the dialog.
        """
        if not self._parked_mode:
            self.set_view_mode("parked")
        self._commit_open_editor()
        issues: list[TruckIssue] = []
        for row in range(self._table.rowCount()):
            if row >= self._saved_count and not self._row_has_data(row):
                continue
            it = self._table.item(row, COL_TRUCK)
            raw = (it.text().strip() if it else "")
            if not raw:
                issues.append(
                    TruckIssue(
                        row=row,
                        original="",
                        kind="not_in_registry",
                        row_details=self._truck_issue_row_details(row),
                    )
                )
                continue
            if self._row_truck_is_valid(row):
                continue
            status, value = self._resolve_truck_text(raw, row=row)
            if status == "ok":
                continue
            kind = (
                "invalid_format" if status == "invalid_format" else "not_in_registry"
            )
            issues.append(
                TruckIssue(
                    row=row,
                    original=value or raw,
                    kind=kind,
                    row_details=self._truck_issue_row_details(row),
                )
            )
        if not issues:
            QMessageBox.information(
                self,
                "No truck fixes needed",
                "Every parked row already has a valid fleet truck.\n\n"
                "Type a truck in the table, or Clear parked to discard rows.",
            )
            return
        dlg = TruckCorrectionDialog(
            issues,
            self._fleet_numbers,
            can_add=self._can_add_fleet(),
            allowed_labels=self._allowed_truck_labels,
            on_resolved=self._on_truck_issue_resolved_live,
            fleet_kinds=getattr(self, "_fleet_kinds", None) or {},
            heading="Fix parked trucks",
            intro=(
                "These rows are already parked. Correct, Allow, or choose No truck — "
                "accepted rows move to My entries right away."
            ),
            allow_park=False,
            parent=self,
        )
        self._open_truck_dialog = dlg
        result = dlg.exec()
        self._open_truck_dialog = None
        pending_adds = list(getattr(dlg, "pending_registry_adds", None) or [])
        if pending_adds:
            asyncio.ensure_future(self._persist_truck_registry_adds(pending_adds))
        if getattr(dlg, "new_labels", None):
            asyncio.ensure_future(self._remember_truck_labels(dlg.new_labels))
        asyncio.ensure_future(self._load_fleet_numbers())
        # Cancel must not wipe unresolved parked trucks; live Allow/Apply already
        # wrote accepted values — promote those off Parked onto My immediately.
        _ = result
        promoted = self._promote_accepted_parked_rows_to_my()
        if not promoted:
            self._local_parked = self._table_parked_entries()
            self._emit_parked_count()
            self._update_footer()
            self._flush_day_issues_source_changed(force=True)
            self._schedule_draft_autosave()

    def clear_parked_bucket(self) -> None:
        """Clear Parked: move every parked row to Cashier Trash (not My).

        Local parked rows are saved as ``register_status=parked`` then
        soft-trashed so Restore / Delete permanently work. Legacy server
        parked rows are soft-trashed in place (status stays ``parked``).
        """
        from tahmeed.ui.dialogs.confirm_dialog import ask_confirm

        if not self._parked_mode:
            self.set_view_mode("parked")
        self._commit_open_editor()
        if self._parked_mode:
            self._local_parked = self._table_parked_entries()
        local_n = len(self._local_parked)
        server_ids = [
            self._saved_ids[row]
            for row in sorted(self._saved_ids)
            if (
                getattr(self._saved_txs.get(row), "register_status", "") or ""
            ) == "parked"
        ]
        server_n = len(server_ids)
        if local_n <= 0 and server_n <= 0:
            self.transient_status.emit("Nothing parked to clear")
            return

        total = local_n + server_n
        if not ask_confirm(
            self,
            title="Clear parked?",
            heading="Move Parked rows to Trash?",
            body=(
                f"{total} parked row{'s' if total != 1 else ''} will leave "
                "this tab and go to Trash.\n\n"
                "• Restore later (back to Parked) or Delete permanently\n"
                "• Does not move rows to My entries"
            ),
            note="Use Fix trucks → Allow to keep accepted trucks on My instead.",
            accept_label="Move to Trash",
            cancel_label="Keep",
            destructive=True,
        ):
            return

        asyncio.ensure_future(
            self._clear_parked_to_trash(list(server_ids))
        )

    async def _clear_parked_to_trash(self, server_ids: list) -> None:
        """Persist any local parked rows, soft-trash all, empty the Parked tab."""
        from tahmeed.services.cashier_service import bulk_save_transactions

        if not self._parked_mode:
            self.set_view_mode("parked")
        self._commit_open_editor()

        local_txs: list = []
        for row in range(self._saved_count, self._table.rowCount()):
            if not self._row_has_data(row):
                continue
            try:
                tx = self._build_transaction_from_row(row)
            except ValueError:
                tx = None
            if tx is None:
                continue
            # Always trash as parked so Restore lands back on Parked.
            tx.register_status = "parked"
            if not tx.client_row_id:
                tx.client_row_id = self._ensure_row_client_id(row)
            local_txs.append(tx)

        total = len(server_ids) + len(local_txs)
        self._show_register_loading(
            f"Moving {total or len(server_ids)} parked "
            f"row{'s' if (total or len(server_ids)) != 1 else ''} to Trash…"
        )
        trash_ids: list = [tid for tid in server_ids if tid]
        try:
            if local_txs:
                outcomes = await bulk_save_transactions(local_txs)
                for status, saved, _err in outcomes:
                    if (
                        status in ("created", "already_exists")
                        and saved is not None
                        and getattr(saved, "_id", None)
                    ):
                        trash_ids.append(saved._id)

            trashed = 0
            errors: list[str] = []
            for tid in trash_ids:
                try:
                    result = await request_or_delete_transaction(
                        tid, self._user._id
                    )
                except Exception as exc:
                    errors.append(str(exc))
                    continue
                if result in ("trashed", "already_trashed"):
                    trashed += 1
                elif result not in ("not_found",):
                    errors.append(f"{tid}: {result}")

            # Drop local bucket + remove parked (and just-trashed) from day cache.
            self._local_parked = []
            trash_id_set = {str(tid) for tid in trash_ids}
            if self._day_tx_cache is not None:
                self._day_tx_cache = [
                    tx
                    for tx in self._day_tx_cache
                    if (getattr(tx, "register_status", "") or "") != "parked"
                    and str(getattr(tx, "_id", "")) not in trash_id_set
                ]
            if self._parked_mode:
                self._apply_txs_to_view(
                    list(self._day_tx_cache or []),
                    upload_id="",
                    reload_local_parked=False,
                )
            self._flush_local_draft()
            self._emit_parked_count()
            self._flush_day_issues_source_changed(force=True)
            self.trash_changed.emit()

            if errors and not trashed:
                QMessageBox.critical(
                    self,
                    "Clear parked",
                    "Could not move parked rows to Trash:\n"
                    + "\n".join(errors[:5]),
                )
                return
            msg = (
                f"Moved {trashed} parked "
                f"row{'s' if trashed != 1 else ''} to Trash"
            )
            if errors:
                msg += f" ({len(errors)} failed)"
            self.transient_status.emit(msg)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Clear parked",
                f"Could not move parked rows to Trash:\n{exc}",
            )
        finally:
            self._hide_register_loading()

    def _release_saved_parked_rows(self, pairs: list) -> None:
        """Remove a saved parked set from the Parked surface. The day stays loaded."""
        wanted = {str(tx_id) for tx_id, _body in pairs if tx_id}
        rows = sorted(
            row for row, tx in self._saved_ids.items() if str(tx) in wanted
        )
        entries: list = []
        for row in rows:
            rid = self._ensure_row_client_id(row)
            meta = dict(self._pending_row_meta.get(row) or {})
            meta.pop("parked", None)
            meta.pop("sync_status", None)
            meta["client_row_id"] = rid
            entry = {
                "client_row_id": rid,
                "cells": cells_for_json(self._row_value_map(row)),
                "pending_meta": serialize_pending_meta(meta),
                "tx_id": self._saved_ids.get(row),
            }
            allow = self._truck_allow_anyway.get(row)
            if allow:
                entry["allow_anyway"] = allow
            entries.append(entry)
        if entries:
            self._append_entries_onto_my_surface(entries)
        if rows:
            prev = self._table.blockSignals(True)
            self._bulk_mutating = True
            try:
                for row in reversed(rows):
                    self._shift_row_maps_on_remove(row)
                    self._table.removeRow(row)
            finally:
                self._bulk_mutating = False
                self._table.blockSignals(prev)
            self._renumber()
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._emit_undo_redo_state()
        if self._parked_mode:
            self._local_parked = self._table_parked_entries()
            self._flush_local_draft()
            self._emit_parked_count()
            self._update_footer()
        if self._flip_to_my_keep_alive():
            return
        self._ensure_my_surface_active()
        self._view_mode = "my"
        self._merged_mode = False
        self._parked_mode = False
        self.mode_changed.emit("my")

    async def _unpark_saved_rows(
        self,
        patches: list,
        *,
        loading_message: str | None = None,
        status_message: str | None = None,
        error_title: str = "Clear parked",
    ) -> None:
        """Return saved parked rows to draft on My, persisting truck fixes.

        ``patches`` is a list of ``tx_id`` or ``(tx_id, updates)``. Updates should
        include the corrected ``truck_number`` when Fix trucks → Correct ran;
        ``register_status`` is always forced to ``draft``.
        """
        from tahmeed.services.cashier_service import bulk_update_transactions

        pairs: list[tuple] = []
        for item in patches or []:
            if isinstance(item, tuple) and len(item) == 2:
                tx_id, updates = item
                body = dict(updates or {})
            else:
                tx_id, body = item, {}
            if not tx_id:
                continue
            body["register_status"] = "draft"
            # Parked → draft is not an after-verify edit.
            body.pop("edited_after_verification", None)
            pairs.append((tx_id, body))
        if not pairs or self._current_date is None:
            return
        n = len(pairs)
        self._show_register_loading(
            loading_message
            or f"Clearing {n} parked row{'s' if n != 1 else ''}…"
        )
        try:
            updated = await bulk_update_transactions(pairs)
            self._release_saved_parked_rows(pairs)
            count = updated or n
            self.transient_status.emit(
                status_message
                or (
                    f"Returned {count} parked "
                    f"row{'s' if count != 1 else ''} to My entries"
                )
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                error_title,
                f"Could not clear saved parked rows:\n{exc}",
            )
        finally:
            self._hide_register_loading()

    async def _persist_parked_after_dialog(self) -> None:
        """Legacy: persist parked flags on already-saved rows, open Parked.

        Never used for unsaved rows any more — those are parked locally by
        ``_park_rows_locally`` with no server write.
        """
        parked_rows = [
            row for row, meta in self._pending_row_meta.items()
            if isinstance(meta, dict) and meta.get("parked")
        ]
        for row in parked_rows:
            if row in self._saved_ids and row not in self._dirty_rows:
                self._dirty_rows.add(row)
        if parked_rows and not self._edit_mode and any(
            r in self._saved_ids for r in parked_rows
        ):
            self._enter_edit_mode()
        ok = await self._do_save(parked_only=True)
        if ok:
            # Save may have queued the outbox; settle sync before switching
            # views so Parked load cannot race drain / Draft-restore leftovers.
            await self._await_outbox_idle(timeout_s=180, require_empty=True)
            self.transient_status.emit("Parked for truck follow-up")
            # Parked never restores My draft; policy is set again inside set_view_mode.
            self._draft_restore_policy = "off"
            if self._view_mode != "parked":
                self.set_view_mode("parked")
            elif self._current_date is not None:
                await self._load_date(self._current_date)

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
        if getattr(issue, "accept_empty", False):
            self._truck_allow_anyway.pop(issue.row, None)
            self._set_truck_cell(issue.row, "")
            self._pending_truck_issues.pop(issue.row, None)
            meta = dict(self._pending_row_meta.get(issue.row) or {})
            meta["accept_empty_truck"] = True
            # Saved parked rows leave Parked immediately, same as Allow.
            # Local rows keep their parked flag until promote copies them to My.
            if issue.row in self._saved_ids:
                meta.pop("parked", None)
                self._dirty_rows.add(issue.row)
            elif not self._parked_mode:
                meta.pop("parked", None)
            self._pending_row_meta[issue.row] = meta
            return
        if getattr(issue, "parked", False):
            # Re-park is meaningless on the Parked surface — keep the truck text only.
            if self._parked_mode:
                truck = (issue.corrected or issue.original or "").strip()
                self._set_truck_cell(issue.row, truck)
                self._pending_truck_issues.pop(issue.row, None)
                return
            truck = (issue.corrected or issue.original or "").strip()
            self._set_truck_cell(issue.row, truck)
            meta = dict(self._pending_row_meta.get(issue.row) or {})
            meta["parked"] = True
            self._pending_row_meta[issue.row] = meta
            self._truck_allow_anyway.pop(issue.row, None)
            self._pending_truck_issues.pop(issue.row, None)
            if issue.row in self._saved_ids:
                self._dirty_rows.add(issue.row)
            return
        if issue.skip or not issue.corrected:
            self._truck_allow_anyway.pop(issue.row, None)
            self._set_truck_cell(issue.row, "")
            return
        if getattr(issue, "is_place_label", False):
            self._truck_allow_anyway.pop(issue.row, None)
            self._allowed_truck_labels.add(normalize_place_label(issue.corrected))
        elif getattr(issue, "allow_anyway", False):
            self._truck_allow_anyway[issue.row] = issue.corrected
        else:
            self._truck_allow_anyway.pop(issue.row, None)
            self._fleet_numbers.add(issue.corrected)
        self._set_truck_cell(issue.row, issue.corrected)
        self._pending_truck_issues.pop(issue.row, None)
        # Allow / Apply must never leave a parked mark on My — otherwise a later
        # Park-remaining sweep (or mixed dialog) would move allowed rows away.
        if not self._parked_mode:
            meta = dict(self._pending_row_meta.get(issue.row) or {})
            if meta.pop("parked", None) is not None:
                self._pending_row_meta[issue.row] = meta
        # Saved parked leftovers: mark dirty so promote / Save can leave Parked.
        # Local parked keep their flag until ``_promote_accepted_parked_rows_to_my``.
        elif issue.row in self._saved_ids:
            meta = dict(self._pending_row_meta.get(issue.row) or {})
            meta.pop("parked", None)
            self._pending_row_meta[issue.row] = meta
            self._dirty_rows.add(issue.row)

    def _validate_truck_cell(self, row: int, item: QTableWidgetItem) -> None:
        raw = item.text().strip()
        if not raw:
            self._truck_allow_anyway.pop(row, None)
            self._update_truck_required_highlight(row)
            return
        allowed = self._truck_allow_anyway.get(row)
        if allowed and self._truck_key(raw) != self._truck_key(allowed):
            self._truck_allow_anyway.pop(row, None)
        status, value = self._resolve_truck_text(raw, row=row)
        if status == "ok":
            if item.text() != value:
                prev = self._table.blockSignals(True)
                item.setText(value)
                self._table.blockSignals(prev)
            self._pending_truck_issues.pop(row, None)
            self._update_truck_required_highlight(row)
            return
        if status == "empty":
            self._update_truck_required_highlight(row)
            return
        kind = "invalid_format" if status == "invalid_format" else "not_in_registry"
        if item.text() != value:
            prev = self._table.blockSignals(True)
            item.setText(value)
            self._table.blockSignals(prev)
        self._pending_truck_issues[row] = TruckIssue(
            row=row,
            original=value or raw,
            kind=kind,
            row_details=self._truck_issue_row_details(row),
        )
        self._schedule_truck_correction()

    def _reject_truck(self, row: int, number: str) -> None:
        # Kept for compatibility; correction dialog handles messaging.
        it = self._table.item(row, COL_TRUCK)
        if it is None or it.text().strip().upper() != number.upper():
            return
        self._pending_truck_issues[row] = TruckIssue(
            row=row,
            original=number,
            kind="not_in_registry",
            row_details=self._truck_issue_row_details(row),
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
