"""Cashier — Rejected / Discarded entries panel.

Inline table editing (same columns & editors as DailyRegister) with unlock,
clipboard, bulk Resubmit / Discard, and a Discarded sub-tab for restore or
permanent delete. Scoped to the logged-in cashier.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, date
from typing import Dict, List, Optional, Set

import qtawesome as qta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QApplication, QMenu, QMessageBox,
)
from PySide6.QtCore import Qt, QObject, QEvent, QSize
from PySide6.QtGui import QColor, QBrush, QKeyEvent

from tahmeed.models.transaction import Transaction, pack_money
from tahmeed.models.user import User
from tahmeed.ui.cashier.register_delegates import (
    COL_SNO, COL_DATE, COL_ITEM, COL_DESC, COL_TRUCK, COL_MEMO,
    COL_REF, COL_TZS, COL_USD, COL_RECEIPT, COL_OWN, COL_APR, COL_PAYEE, COL_CHEQUE,
    HEADERS, CHECK_COLS, READONLY_COLS, _UPPER_SKIP_COLS, _COL_PREFERRED,
    _ref_float_text, _parse_optional_date, _norm_receipt_text, _parse_amount_text,
    _parse_optional_amount_text,
    _upper_text, _VALID_RCPT,
    SAVED_BG, EDIT_BG, DIRTY_BG, NEG_COLOR,
    _ExcelCellDelegate, _DescriptionDelegate, _TruckDelegate, _DateDelegate,
    _RefFloatDelegate, _ReceiptDelegate, _ItemDelegate, _TZSDelegate,
)
from tahmeed.services.truck_format import (
    normalize_truck_number, try_match_fleet, normalize_place_label,
    is_allowed_place_label, DEFAULT_PLACE_LABELS, merge_allowed_labels,
)
from tahmeed.services.truck_service import get_fleet_numbers
from tahmeed.services.settings_service import get_setting
from tahmeed.services.category_service import get_payment_target_categories
from tahmeed.services.subtable_service import get_subtables
from tahmeed.ui.cashier.excel_row_header import ExcelRowHeaderView, ROW_HEADER_QSS, sync_row_header_labels

# ── Design tokens ────────────────────────────────────────────────────────────
_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_RED = "#DC2626"
_RED_L = "#FEF2F2"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"
_HDR_BG = "#FFF0F0"
_HDR_FG = "#991B1B"

COL_REASON = len(HEADERS)  # trailing read-only rejection reason
REJECTED_HEADERS = list(HEADERS) + ["Reason"]
_EDITABLE_DATA_COLS = set(range(len(HEADERS))) - READONLY_COLS
_ALL_READONLY = READONLY_COLS | {COL_REASON}

_RED_ROW = QColor(_RED_L)


def _lbl(text="", size=13, weight=400, color=_T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI',sans-serif;background:transparent;"
    )
    return w


def _tool_btn(text: str, *, primary=False, danger=False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(30)
    if primary:
        btn.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:4px;"
            "font-size:12px;font-weight:600;font-family:'Segoe UI',sans-serif;"
            "padding:0 14px;}}"
            "QPushButton:hover{background:#005EA3;}"
            "QPushButton:disabled{background:#93C5FD;}"
        )
    elif danger:
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_RED};border:1px solid #FECACA;"
            "border-radius:4px;font-size:12px;font-weight:600;"
            "font-family:'Segoe UI',sans-serif;padding:0 14px;}}"
            f"QPushButton:hover{{background:{_RED_L};}}"
            "QPushButton:disabled{color:#FCA5A5;border-color:#FEE2E2;}"
        )
    else:
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
            "border-radius:4px;font-size:12px;font-weight:500;"
            "font-family:'Segoe UI',sans-serif;padding:0 14px;}}"
            f"QPushButton:hover{{background:{_BG};}}"
            "QPushButton:disabled{color:#D1D5DB;}"
        )
    return btn


class _TableKeyFilter(QObject):
    def __init__(self, handler):
        super().__init__()
        self._handler = handler

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.KeyPress:
            self._handler(event)
            return True
        return False


class RejectedView(QWidget):
    """Rejected + Discarded sub-tabs with unlockable inline editing."""

    def __init__(self, user: User, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._mode = "rejected"  # "rejected" | "discarded"
        self._transactions: List[Transaction] = []
        self._row_ids: Dict[int, object] = {}
        self._row_txs: Dict[int, Transaction] = {}
        self._unlocked: Set[int] = set()
        self._dirty_rows: Set[int] = set()
        self._bulk_mutating = False

        self._categories: list = []
        self._cat_by_name: dict = {}
        self._locked_subitems: dict = {}
        self._fleet_numbers: set = set()
        self._allowed_truck_labels: set = set(DEFAULT_PLACE_LABELS)
        self._people_names: list = []
        self._restrict_items = False
        self._defer_item_to_verify = False

        self._build()
        asyncio.ensure_future(self._load_lookups())

    # ── Layout ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        title_bar = QFrame()
        title_bar.setFixedHeight(52)
        title_bar.setStyleSheet(
            f"QFrame{{background:{_WHITE};border-bottom:1px solid {_BORDER};}}"
        )
        tbl = QHBoxLayout(title_bar)
        tbl.setContentsMargins(20, 0, 20, 0)
        tbl.setSpacing(10)
        try:
            ico_lbl = QLabel()
            ico_lbl.setFixedSize(22, 22)
            ico_lbl.setPixmap(
                qta.icon("mdi.alert-circle-outline", color=_RED).pixmap(22, 22)
            )
            ico_lbl.setStyleSheet("background:transparent;")
            tbl.addWidget(ico_lbl)
        except Exception:
            pass
        tbl.addWidget(_lbl("Rejected Entries", 16, 700))
        self._count_badge = QLabel("0")
        self._count_badge.setAlignment(Qt.AlignCenter)
        self._count_badge.setMinimumWidth(36)
        self._count_badge.setStyleSheet(
            f"color:#FFFFFF;background:{_RED};border-radius:10px;"
            "padding:2px 8px;font-size:10px;font-weight:600;"
            "font-family:'Segoe UI',sans-serif;"
        )
        tbl.addWidget(self._count_badge)
        tbl.addStretch()
        refresh_btn = QPushButton()
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;border-radius:4px;}"
            f"QPushButton:hover{{background:{_BG};}}"
        )
        try:
            refresh_btn.setIcon(qta.icon("mdi.refresh", color=_T2))
            refresh_btn.setIconSize(QSize(18, 18))
        except Exception:
            refresh_btn.setText("↻")
        refresh_btn.clicked.connect(self.refresh)
        tbl.addWidget(refresh_btn)
        root.addWidget(title_bar)

        # Sub-tabs (left) + actions (right)
        tabs = QFrame()
        tabs.setFixedHeight(44)
        tabs.setStyleSheet(
            f"QFrame{{background:{_WHITE};border-bottom:1px solid {_BORDER};}}"
        )
        tl = QHBoxLayout(tabs)
        tl.setContentsMargins(20, 0, 20, 0)
        tl.setSpacing(8)
        self._tab_rejected = QPushButton("Rejected (0)")
        self._tab_discarded = QPushButton("Discarded (0)")
        for btn, mode in ((self._tab_rejected, "rejected"), (self._tab_discarded, "discarded")):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.clicked.connect(lambda _, m=mode: self._set_mode(m))
            tl.addWidget(btn)
        tl.addStretch()

        # Rejected actions (right)
        self._rejected_actions = QWidget()
        self._rejected_actions.setStyleSheet("background:transparent;")
        ra = QHBoxLayout(self._rejected_actions)
        ra.setContentsMargins(0, 0, 0, 0)
        ra.setSpacing(8)
        self._btn_unlock = _tool_btn("Unlock")
        self._btn_unlock.clicked.connect(self._on_unlock)
        self._btn_resubmit = _tool_btn("Resubmit", primary=True)
        self._btn_resubmit.clicked.connect(self._on_resubmit)
        self._btn_discard = _tool_btn("Discard", danger=True)
        self._btn_discard.clicked.connect(self._on_discard)
        for b in (self._btn_unlock, self._btn_resubmit, self._btn_discard):
            ra.addWidget(b)
        tl.addWidget(self._rejected_actions)

        # Discarded actions (right) — same position as rejected actions
        self._discarded_actions = QWidget()
        self._discarded_actions.setStyleSheet("background:transparent;")
        da = QHBoxLayout(self._discarded_actions)
        da.setContentsMargins(0, 0, 0, 0)
        da.setSpacing(8)
        self._btn_restore = _tool_btn("Restore", primary=True)
        self._btn_restore.clicked.connect(self._on_restore)
        self._btn_delete = _tool_btn("Delete permanently", danger=True)
        self._btn_delete.clicked.connect(self._on_delete)
        for b in (self._btn_restore, self._btn_delete):
            da.addWidget(b)
        tl.addWidget(self._discarded_actions)

        root.addWidget(tabs)
        self._style_tabs()
        self._sync_toolbar()

        # Table card
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(20, 12, 20, 16)
        cl.setSpacing(0)
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};border-radius:6px;}}"
        )
        card_vl = QVBoxLayout(card)
        card_vl.setContentsMargins(0, 0, 0, 0)
        card_vl.setSpacing(0)
        self._table = self._build_table()
        card_vl.addWidget(self._table)
        cl.addWidget(card, 1)
        root.addWidget(content, 1)

    def _build_table(self) -> QTableWidget:
        t = QTableWidget(0, len(REJECTED_HEADERS))
        t.setHorizontalHeaderLabels(REJECTED_HEADERS)
        t.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        t.setSelectionBehavior(QAbstractItemView.SelectItems)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.setAlternatingRowColors(False)
        t.setVerticalHeader(ExcelRowHeaderView(t, owner=self))
        t.setShowGrid(True)
        t.setTabKeyNavigation(False)
        t.setStyleSheet(
            f"QTableWidget{{background:{_WHITE};gridline-color:{_BORDER};"
            "border:none;font-family:Calibri;font-size:11pt;"
            "selection-background-color:#cde0f5;selection-color:#1B2B4B;}}"
            f"QTableWidget::item{{padding:2px 3px;color:{_T1};}}"
            f"QHeaderView::section:horizontal{{background:{_HDR_BG};color:{_HDR_FG};"
            "font-size:11px;font-weight:700;font-family:'Segoe UI',sans-serif;"
            f"border:none;border-right:1px solid {_BORDER};"
            f"border-bottom:2px solid {_RED};padding:5px 4px;}}"
            f"QTableCornerButton::section{{background:#F2F2F2;border:none;"
            f"border-right:1px solid #D4D4D4;border-bottom:2px solid {_RED};}}"
            + ROW_HEADER_QSS
            + "QLineEdit{color:#111827;background:#ffffff;font-family:Calibri;font-size:11pt;}"
            "QScrollBar:vertical{width:8px;background:transparent;}"
            "QScrollBar::handle:vertical{background:#D1D5DB;border-radius:4px;}"
        )
        hdr = t.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setStretchLastSection(False)
        for col in range(len(REJECTED_HEADERS)):
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_SNO, QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_DESC, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_REASON, QHeaderView.Stretch)
        for col, width in _COL_PREFERRED.items():
            t.setColumnWidth(col, width)
        t.setColumnWidth(COL_REASON, 180)

        t.setItemDelegate(_ExcelCellDelegate(t))
        t.setItemDelegateForColumn(
            COL_ITEM,
            _ItemDelegate(lambda: [c.name for c in self._categories], t),
        )
        t.setItemDelegateForColumn(
            COL_DESC,
            _DescriptionDelegate(
                cat_getter=lambda name: self._cat_by_name.get(name.lower()),
                subs_getter=lambda name: self._locked_subitems.get(name.lower(), []),
                parent=t,
            ),
        )
        t.setItemDelegateForColumn(
            COL_TRUCK,
            _TruckDelegate(lambda: sorted(self._fleet_numbers), t),
        )
        date_del = _DateDelegate(lambda: date.today(), t)
        t.setItemDelegateForColumn(COL_DATE, date_del)
        t.setItemDelegateForColumn(COL_REF, _RefFloatDelegate(t))
        t.setItemDelegateForColumn(COL_TZS, _TZSDelegate(t))
        t.setItemDelegateForColumn(COL_USD, _TZSDelegate(t))
        t.setItemDelegateForColumn(COL_RECEIPT, _ReceiptDelegate(t))
        people_del = _ItemDelegate(lambda: list(self._people_names), t)
        t.setItemDelegateForColumn(COL_OWN, people_del)
        t.setItemDelegateForColumn(COL_APR, people_del)

        t.itemChanged.connect(self._on_item_changed)
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.customContextMenuRequested.connect(self._show_context_menu)

        t._grid_owner = self  # used by cell delegates for Tab/Enter navigation
        self._key_filter = _TableKeyFilter(self._table_key_press)
        t.installEventFilter(self._key_filter)
        vh = t.verticalHeader()
        if vh is not None:
            vh.installEventFilter(self._key_filter)
        return t

    # ── Mode / chrome ────────────────────────────────────────────────────────

    def _style_tabs(self) -> None:
        for btn, active in (
            (self._tab_rejected, self._mode == "rejected"),
            (self._tab_discarded, self._mode == "discarded"),
        ):
            if active:
                btn.setStyleSheet(
                    f"QPushButton{{background:transparent;color:{_RED};border:none;"
                    f"border-bottom:2px solid {_RED};border-radius:0;"
                    "font-size:12px;font-weight:700;font-family:'Segoe UI',sans-serif;"
                    "padding:6px 12px;}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:transparent;color:{_T2};border:none;"
                    "border-bottom:2px solid transparent;border-radius:0;"
                    "font-size:12px;font-weight:500;font-family:'Segoe UI',sans-serif;"
                    "padding:6px 12px;}}"
                    f"QPushButton:hover{{color:{_T1};}}"
                )

    def _sync_toolbar(self) -> None:
        rejected = self._mode == "rejected"
        self._rejected_actions.setVisible(rejected)
        self._discarded_actions.setVisible(not rejected)

    def _set_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self._unlocked.clear()
        self._dirty_rows.clear()
        self._style_tabs()
        self._sync_toolbar()
        self.refresh()

    # ── Lookups / data ───────────────────────────────────────────────────────

    async def _load_lookups(self) -> None:
        try:
            cats = await get_payment_target_categories()
            self._categories = cats
            self._cat_by_name = {c.name.lower(): c for c in cats}
        except Exception:
            pass
        try:
            self._restrict_items = bool(await get_setting("restrict_items"))
        except Exception:
            self._restrict_items = False
        try:
            self._defer_item_to_verify = bool(await get_setting("defer_item_to_verify"))
        except Exception:
            self._defer_item_to_verify = False
        try:
            fleet = await get_fleet_numbers()
            self._fleet_numbers = {str(n).strip().upper() for n in fleet if n}
        except Exception:
            pass
        try:
            from tahmeed.services.people_service import get_people_names
            names = await get_people_names()
            self._people_names = [
                str(n).strip().upper() for n in (names or []) if str(n).strip()
            ]
        except Exception:
            self._people_names = []
        try:
            labels = await get_setting("allowed_truck_labels")
            if labels:
                self._allowed_truck_labels = merge_allowed_labels(labels)
        except Exception:
            pass
        try:
            from tahmeed.services.category_service import item_key
            cache: dict = {}
            for c in self._categories:
                if getattr(c, "lock_description", False):
                    try:
                        subs = await get_subtables(item_key(c.name))
                        cache[c.name.lower()] = [s.name for s in subs]
                    except Exception:
                        cache[c.name.lower()] = []
            self._locked_subitems = cache
        except Exception:
            self._locked_subitems = {}

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services.cashier_service import (
            get_rejected_transactions_for_cashier,
            get_discarded_transactions_for_cashier,
        )
        try:
            rejected = await get_rejected_transactions_for_cashier(self._user._id)
            discarded = await get_discarded_transactions_for_cashier(self._user._id)
            self._tab_rejected.setText(f"Rejected ({len(rejected)})")
            self._tab_discarded.setText(f"Discarded ({len(discarded)})")
            txs = rejected if self._mode == "rejected" else discarded
            self._transactions = txs
            self._unlocked.clear()
            self._dirty_rows.clear()
            self._fill_table(txs)
            self._count_badge.setText(str(len(txs)))
        except Exception as exc:
            self._show_empty(f"Failed to load: {exc}")

    def _fill_table(self, txs: List[Transaction]) -> None:
        t = self._table
        t.blockSignals(True)
        t.setRowCount(0)
        self._row_ids.clear()
        self._row_txs.clear()

        if not txs:
            t.blockSignals(False)
            sync_row_header_labels(t)
            msg = (
                "No rejected entries."
                if self._mode == "rejected"
                else "No discarded entries."
            )
            self._show_empty(msg)
            return

        for i, tx in enumerate(txs):
            r = t.rowCount()
            t.insertRow(r)
            self._row_ids[r] = tx._id
            self._row_txs[r] = tx
            self._fill_row(r, tx, i + 1)

        t.blockSignals(False)
        sync_row_header_labels(t)

    def _fill_row(self, row: int, tx: Transaction, sn: int) -> None:
        bg = QBrush(_RED_ROW if self._mode == "rejected" else SAVED_BG)
        ro = Qt.ItemIsEnabled | Qt.ItemIsSelectable

        def cell(text: str, align=Qt.AlignVCenter | Qt.AlignLeft) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            it.setFlags(ro)
            it.setBackground(bg)
            it.setTextAlignment(align)
            return it

        self._table.setItem(row, COL_SNO, cell(str(sn), Qt.AlignCenter))
        date_str = tx.date.strftime("%d/%m/%Y") if tx.date else ""
        self._table.setItem(row, COL_DATE, cell(date_str))
        self._table.setItem(row, COL_ITEM, cell(tx.item or ""))
        self._table.setItem(row, COL_DESC, cell(tx.description or ""))
        self._table.setItem(row, COL_TRUCK, cell(tx.truck_number or ""))
        self._table.setItem(row, COL_MEMO, cell(tx.memo or ""))
        self._table.setItem(row, COL_REF, cell(_ref_float_text(tx)))

        tzs_amt, usd_amt = tx.money_parts()
        tzs_txt = f"{tzs_amt:,.2f}" if tzs_amt else ""
        usd_txt = f"{usd_amt:,.2f}" if usd_amt else ""
        tzs_it = cell(tzs_txt, Qt.AlignRight | Qt.AlignVCenter)
        if tzs_amt < 0:
            tzs_it.setForeground(NEG_COLOR)
        self._table.setItem(row, COL_TZS, tzs_it)
        usd_it = cell(usd_txt, Qt.AlignRight | Qt.AlignVCenter)
        if usd_amt < 0:
            usd_it.setForeground(NEG_COLOR)
        self._table.setItem(row, COL_USD, usd_it)

        self._table.setItem(row, COL_RECEIPT, cell(tx.receipt_status or "pending"))
        self._table.setItem(row, COL_OWN, cell(tx.ownership or ""))
        self._table.setItem(row, COL_APR, cell(tx.approver or ""))
        self._table.setItem(row, COL_PAYEE, cell(getattr(tx, "payee", "") or ""))
        self._table.setItem(row, COL_CHEQUE, cell(getattr(tx, "cheque", "") or ""))

        reason = tx.rejection_reason or "—"
        reason_it = cell(reason)
        reason_it.setForeground(QColor(_RED))
        reason_it.setToolTip(reason)
        self._table.setItem(row, COL_REASON, reason_it)

    def _show_empty(self, msg: str) -> None:
        t = self._table
        t.blockSignals(True)
        t.setRowCount(1)
        self._row_ids.clear()
        self._row_txs.clear()
        for c in range(len(REJECTED_HEADERS)):
            t.setItem(0, c, QTableWidgetItem(""))
        item = QTableWidgetItem(msg)
        item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        item.setForeground(QColor(_TM))
        item.setFlags(Qt.ItemIsEnabled)
        t.setItem(0, 0, item)
        t.setSpan(0, 0, 1, len(REJECTED_HEADERS))
        t.blockSignals(False)
        sync_row_header_labels(t)

    # ── Selection helpers ────────────────────────────────────────────────────

    def _selected_rows(self) -> List[int]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        return [r for r in rows if r in self._row_ids]

    def _selected_ids(self) -> List:
        return [self._row_ids[r] for r in self._selected_rows()]

    # ── Unlock / dirty ───────────────────────────────────────────────────────

    def _on_unlock(self) -> None:
        if self._mode != "rejected":
            return
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Unlock", "Select one or more rows to unlock.")
            return
        editable = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
        self._table.blockSignals(True)
        for row in rows:
            self._unlocked.add(row)
            for col in range(len(REJECTED_HEADERS)):
                it = self._table.item(row, col)
                if it is None:
                    continue
                if col in _ALL_READONLY:
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                else:
                    it.setFlags(editable)
                if row not in self._dirty_rows:
                    it.setBackground(QBrush(EDIT_BG))
        self._table.blockSignals(False)

    def _mark_dirty(self, row: int) -> None:
        if row not in self._unlocked:
            return
        if row in self._dirty_rows:
            return
        self._dirty_rows.add(row)
        self._table.blockSignals(True)
        for col in range(len(REJECTED_HEADERS)):
            it = self._table.item(row, col)
            if it is not None:
                it.setBackground(QBrush(DIRTY_BG))
        self._table.blockSignals(False)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._bulk_mutating or item is None:
            return
        row, col = item.row(), item.column()
        if row not in self._unlocked or col in _ALL_READONLY:
            return
        if col not in _UPPER_SKIP_COLS and col not in (COL_TZS, COL_USD):
            text = item.text()
            uppered = _upper_text(col, text)
            if uppered != text:
                self._table.blockSignals(True)
                item.setText(uppered)
                self._table.blockSignals(False)
        if col in (COL_TZS, COL_USD):
            raw = item.text().strip()
            if raw:
                amt = _parse_amount_text(raw)
                formatted = f"{amt:,.2f}"
                if formatted != raw:
                    self._table.blockSignals(True)
                    item.setText(formatted)
                    if amt < 0:
                        item.setForeground(NEG_COLOR)
                    self._table.blockSignals(False)
        self._mark_dirty(row)

    def _row_is_editable(self, row: int) -> bool:
        return self._mode == "rejected" and row in self._unlocked

    # ── Keyboard / clipboard ─────────────────────────────────────────────────

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
            if key == Qt.Key_A:
                self._table.selectAll()
                return
            if key == Qt.Key_D:
                self._fill_down()
                return

        if key == Qt.Key_F2:
            it = self._table.currentItem()
            if it and self._row_is_editable(it.row()):
                self._table.editItem(it)
            return

        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self._clear_selected()
            return

        if key == Qt.Key_Tab:
            self._tab_forward()
            return

        if key == Qt.Key_Backtab:
            self._step(0, -1, skip=_ALL_READONLY)
            return

        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._step(+1, 0)
            return

        QTableWidget.keyPressEvent(self._table, event)

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
                row_cells.append(it.text() if it is not None else "")
            lines.append("\t".join(row_cells))
        QApplication.clipboard().setText("\n".join(lines))

    def _cut(self) -> None:
        self._copy()
        self._clear_selected()

    def _clear_selected(self) -> None:
        self._table.blockSignals(True)
        for item in self._table.selectedItems():
            row, col = item.row(), item.column()
            if not self._row_is_editable(row) or col in _ALL_READONLY:
                continue
            item.setText("")
            self._mark_dirty(row)
        self._table.blockSignals(False)

    def _paste(self) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return
        lines = text.splitlines()
        sel = self._table.selectedIndexes()
        if not sel:
            return
        start_row = min(i.row() for i in sel)
        start_col = min(i.column() for i in sel)
        sel_rows = sorted({i.row() for i in sel})
        sel_cols = sorted({i.column() for i in sel})

        self._bulk_mutating = True
        self._table.blockSignals(True)
        try:
            if len(lines) == 1 and "\t" not in lines[0] and (
                len(sel_rows) > 1 or len(sel_cols) > 1
            ):
                cell_value = lines[0].strip()
                for row in sel_rows:
                    if not self._row_is_editable(row):
                        continue
                    for col in sel_cols:
                        self._set_pasted_cell(row, col, cell_value)
                return

            for r, line in enumerate(lines):
                for c, cell in enumerate(line.split("\t")):
                    row = start_row + r
                    col = start_col + c
                    if row not in self._row_ids or col >= len(REJECTED_HEADERS):
                        continue
                    if not self._row_is_editable(row):
                        continue
                    self._set_pasted_cell(row, col, cell)
        finally:
            self._table.blockSignals(False)
            self._bulk_mutating = False
            for row in sel_rows:
                if self._row_is_editable(row):
                    self._mark_dirty(row)

    def _set_pasted_cell(self, row: int, col: int, raw: str) -> None:
        if col in _ALL_READONLY:
            return
        value = (raw or "").strip()
        if col == COL_RECEIPT:
            value = _norm_receipt_text(value)
        elif col in (COL_TZS, COL_USD):
            if value:
                amt = _parse_amount_text(value)
                value = f"{amt:,.2f}"
            else:
                value = ""
        elif col == COL_TRUCK:
            value = value.upper()
        else:
            value = _upper_text(col, value)
        it = self._table.item(row, col) or QTableWidgetItem()
        it.setText(value)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        if col in (COL_TZS, COL_USD):
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if value and _parse_amount_text(value) < 0:
                it.setForeground(NEG_COLOR)
        self._table.setItem(row, col, it)

    def _fill_down(self) -> None:
        items = self._table.selectedItems()
        if not items:
            return
        rows = sorted(set(it.row() for it in items))
        cols = sorted(set(it.column() for it in items))
        if len(rows) < 2:
            return
        source_row = rows[0]
        cell_map = {(it.row(), it.column()): it for it in items}
        self._bulk_mutating = True
        self._table.blockSignals(True)
        try:
            for col in cols:
                if col in _ALL_READONLY:
                    continue
                src = cell_map.get((source_row, col))
                if src is None:
                    continue
                for row in rows[1:]:
                    if not self._row_is_editable(row):
                        continue
                    self._set_pasted_cell(row, col, src.text())
                    self._dirty_rows.add(row)
        finally:
            self._table.blockSignals(False)
            self._bulk_mutating = False
            for row in rows[1:]:
                if self._row_is_editable(row):
                    self._mark_dirty(row)

    # ── Context menu ─────────────────────────────────────────────────────────

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        self._populate_context_menu(menu)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _show_row_header_context_menu(self, global_pos) -> None:
        menu = QMenu(self)
        self._populate_context_menu(menu)
        menu.exec(global_pos)

    def _populate_context_menu(self, menu: QMenu) -> None:
        menu.addAction("Copy", self._copy)
        menu.addAction("Cut", self._cut)
        menu.addAction("Paste", self._paste)
        menu.addSeparator()
        if self._mode == "rejected":
            menu.addAction("Unlock for edit", self._on_unlock)
            menu.addAction("Resubmit selected", self._on_resubmit)
            menu.addAction("Discard selected", self._on_discard)
        else:
            menu.addAction("Restore selected", self._on_restore)
            menu.addAction("Delete permanently", self._on_delete)

    # ── Navigation hooks for delegates ───────────────────────────────────────

    def _tab_forward(self) -> None:
        row, col = self._table.currentRow(), self._table.currentColumn()
        skip = _ALL_READONLY
        next_col = col + 1
        while next_col < self._table.columnCount() and next_col in skip:
            next_col += 1
        if next_col >= self._table.columnCount():
            next_row = min(row + 1, self._table.rowCount() - 1)
            first_col = 0
            while first_col < self._table.columnCount() and first_col in skip:
                first_col += 1
            self._table.setCurrentCell(next_row, first_col)
        else:
            self._table.setCurrentCell(row, next_col)
        self._table.setFocus()
        idx = self._table.currentIndex()
        if self._row_is_editable(idx.row()):
            self._table.edit(idx)

    def _step(self, dr: int, dc: int, skip: set = None) -> None:
        row, col = self._table.currentRow(), self._table.currentColumn()
        new_col, new_row = col + dc, row + dr
        skip = skip or _ALL_READONLY
        if skip and dc != 0:
            while 0 <= new_col < self._table.columnCount():
                if new_col not in skip:
                    break
                new_col += dc
        new_row = max(0, min(new_row, self._table.rowCount() - 1))
        new_col = max(0, min(new_col, self._table.columnCount() - 1))
        self._table.setCurrentCell(new_row, new_col)

    # ── Build updates from row ───────────────────────────────────────────────

    def _txt(self, row: int, col: int) -> str:
        it = self._table.item(row, col)
        return it.text().strip() if it else ""

    def _updates_from_row(self, row: int) -> dict:
        """Build field updates from current cell values (may be empty dict)."""
        description = self._txt(row, COL_DESC)
        item_name = self._txt(row, COL_ITEM)
        date_str = self._txt(row, COL_DATE)
        tx_date = _parse_optional_date(date_str)
        if tx_date is None and row in self._row_txs:
            tx_date = self._row_txs[row].date

        amount, amount_usd, currency = pack_money(
            _parse_optional_amount_text(self._txt(row, COL_TZS)),
            _parse_optional_amount_text(self._txt(row, COL_USD)),
        )
        rcpt = _norm_receipt_text(self._txt(row, COL_RECEIPT))
        if rcpt not in _VALID_RCPT:
            rcpt = "pending"

        if not item_name and not self._defer_item_to_verify:
            raise ValueError(
                "Item is required. Enter an item or ask the accountant to enable description-only entries."
            )

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

        truck_raw = self._txt(row, COL_TRUCK)
        truck_number = ""
        if truck_raw:
            if is_allowed_place_label(truck_raw, self._allowed_truck_labels):
                truck_number = normalize_place_label(truck_raw)
            else:
                matched = try_match_fleet(truck_raw, self._fleet_numbers)
                if matched is not None:
                    truck_number = matched
                else:
                    norm = normalize_truck_number(
                        truck_raw, allowed_labels=self._allowed_truck_labels
                    )
                    if norm.status in ("ok", "normalized", "place_label"):
                        truck_number = norm.value
                    elif self._fleet_numbers:
                        # Soft: keep typed value if fleet list empty/unavailable
                        truck_number = (norm.value or truck_raw).upper()
                    else:
                        truck_number = truck_raw.upper()

        ref = self._txt(row, COL_REF).upper()

        updates = {
            "description": description,
            "item": item_name,
            "truck_number": truck_number,
            "amount": amount,
            "currency": currency,
            "amount_usd": amount_usd,
            "memo": self._txt(row, COL_MEMO),
            "receipt_status": rcpt,
            "ref_float": ref,
            "notes_flag": ref == "REFUND TO FLOAT",
            "ownership": self._txt(row, COL_OWN),
            "approver": self._txt(row, COL_APR),
            "payee": self._txt(row, COL_PAYEE),
            "cheque": self._txt(row, COL_CHEQUE),
        }
        if tx_date is not None:
            updates["date"] = tx_date
            updates["month"] = tx_date.strftime("%b %y")
            updates["year"] = tx_date.year
        if cat is not None:
            updates["category_name"] = cat.name
            updates["category_id"] = getattr(cat, "_id", None)
        return updates

    # ── Bulk actions ─────────────────────────────────────────────────────────

    def _on_resubmit(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Resubmit", "Select one or more rows to resubmit.")
            return
        asyncio.ensure_future(self._do_resubmit(rows))

    async def _do_resubmit(self, rows: List[int]) -> None:
        from tahmeed.services.cashier_service import resubmit_rejected_transactions
        updates_by_id = {}
        try:
            for row in rows:
                tx_id = self._row_ids.get(row)
                if not tx_id:
                    continue
                # Always send current grid values (works even if not unlocked).
                updates_by_id[tx_id] = self._updates_from_row(row)
        except ValueError as exc:
            QMessageBox.warning(self, "Resubmit", str(exc))
            return
        try:
            n = await resubmit_rejected_transactions(self._user._id, updates_by_id)
            await self._load()
            if n:
                QMessageBox.information(
                    self, "Resubmit",
                    f"Resubmitted {n} entr{'y' if n == 1 else 'ies'} to the accountant.",
                )
        except Exception as exc:
            QMessageBox.warning(self, "Resubmit failed", str(exc))

    def _on_discard(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Discard", "Select one or more rows to discard.")
            return
        resp = QMessageBox.question(
            self, "Discard",
            f"Discard {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}?\n"
            "They move to the Discarded tab where you can restore or delete them.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_discard(rows))

    async def _do_discard(self, rows: List[int]) -> None:
        from tahmeed.services.cashier_service import discard_transactions
        ids = [self._row_ids[r] for r in rows if r in self._row_ids]
        try:
            await discard_transactions(ids, self._user._id)
            await self._load()
        except Exception as exc:
            QMessageBox.warning(self, "Discard failed", str(exc))

    def _on_restore(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Restore", "Select one or more rows to restore.")
            return
        asyncio.ensure_future(self._do_restore(rows))

    async def _do_restore(self, rows: List[int]) -> None:
        from tahmeed.services.cashier_service import restore_discarded_transactions
        ids = [self._row_ids[r] for r in rows if r in self._row_ids]
        try:
            await restore_discarded_transactions(ids, self._user._id)
            await self._load()
        except Exception as exc:
            QMessageBox.warning(self, "Restore failed", str(exc))

    def _on_delete(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(
                self, "Delete", "Select one or more discarded rows to delete permanently."
            )
            return
        resp = QMessageBox.warning(
            self, "Delete permanently",
            f"Permanently delete {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_delete(rows))

    async def _do_delete(self, rows: List[int]) -> None:
        from tahmeed.services.cashier_service import delete_discarded_transactions
        ids = [self._row_ids[r] for r in rows if r in self._row_ids]
        try:
            await delete_discarded_transactions(ids, self._user._id)
            await self._load()
        except Exception as exc:
            QMessageBox.warning(self, "Delete failed", str(exc))
