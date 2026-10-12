"""Accountant — Trash of soft-deleted Master expenses (restore or purge)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List

import qtawesome as qta
from bson import ObjectId
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QLineEdit, QMessageBox,
)

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.ui.accountant.separate_expenses import (
    _ROW_H, _finish_table_row, _stripe_bg, _table_cell_font, _table_style,
)
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading

_BG = "#F4F6F8"
_WHITE = "#FFFFFF"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_RED = "#DC2626"
_RED_L = "#FEF2F2"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"

# Day Issues–style leading columns: checkbox, then serial #.
_COL_CHK = 0
_COL_NUM = 1
_COL_DATE = 2
_COL_ITEM = 3
_COL_DESC = 4
_COL_TRUCK = 5
_COL_AMOUNT = 6
_COL_CURR = 7
_COL_TRASHED = 8

_HEADERS = [
    "", "#", "Date", "Item", "Description", "Truck", "Amount", "Currency", "Trashed",
]
_COL_DEFAULTS = [36, 40, 100, 120, 220, 110, 90, 80, 110]
_NCOLS = len(_HEADERS)


def _lbl(text="", size=13, weight=400, color=_T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI';background:transparent;"
    )
    return w


def _btn(text: str, icon: str = "", *, primary=False, danger=False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(32)
    if icon:
        btn.setIcon(qta.icon(icon, color="#FFF" if primary else (_RED if danger else _T1)))
        btn.setIconSize(QSize(14, 14))
    if primary:
        btn.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
            "font-size:12px;font-weight:600;font-family:'Segoe UI';"
            "padding:0 14px;}"
            "QPushButton:hover{background:#005EA3;}"
            "QPushButton:disabled{background:#93C5FD;}"
        )
    elif danger:
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_RED};border:1px solid #FECACA;"
            "border-radius:5px;font-size:12px;font-weight:600;"
            "font-family:'Segoe UI';padding:0 14px;}"
            f"QPushButton:hover{{background:{_RED_L};}}"
            "QPushButton:disabled{color:#FCA5A5;border-color:#FEE2E2;}"
        )
    else:
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
            "border-radius:5px;font-size:12px;font-weight:500;"
            "font-family:'Segoe UI';padding:0 14px;}"
            f"QPushButton:hover{{background:{_BG};}}"
            "QPushButton:disabled{color:#D1D5DB;}"
        )
    return btn


def _fmt_date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    return "—"


def _fmt_money(amount: float) -> str:
    try:
        return f"{float(amount):,.0f}"
    except (TypeError, ValueError):
        return "0"


def _cell(
    text: str,
    align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
    *,
    bg: str = "",
) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    item.setFont(_table_cell_font())
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    if bg:
        item.setBackground(QBrush(QColor(bg)))
    item.setForeground(QBrush(QColor(_T1)))
    return item


class TrashWidget(QWidget):
    """Soft-deleted expenses: restore to Master or permanently purge."""

    def __init__(self, user: User, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._transactions: List[Transaction] = []
        self._busy = False
        self._build()
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._reload())

    def refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._reload())

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(_lbl("Trash", size=18, weight=700))
        self._subtitle = _lbl(
            "Soft-deleted expenses. Restore to Master or delete permanently.",
            size=12, color=_T2,
        )
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box, 1)
        root.addLayout(header)

        # One toolbar: compact Table-sized search + Restore / Delete / Refresh.
        tools = QFrame()
        tools.setStyleSheet(
            f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};border-radius:6px;}}"
        )
        tools_hl = QHBoxLayout(tools)
        tools_hl.setContentsMargins(10, 8, 10, 8)
        tools_hl.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search description, item, or truck…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(32)
        self._search.setFixedWidth(220)
        self._search.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:#F9FAFB;color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 10px;}"
            f"QLineEdit:focus{{border-color:{_BLUE};background:{_WHITE};}}"
        )
        self._search.returnPressed.connect(self.refresh)
        tools_hl.addWidget(self._search, 0)

        search_btn = _btn("Search", "mdi.magnify")
        search_btn.clicked.connect(self.refresh)
        tools_hl.addWidget(search_btn)

        self._restore_btn = _btn("Restore Selected", "mdi.restore", primary=True)
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._on_bulk_restore)
        tools_hl.addWidget(self._restore_btn)

        self._purge_btn = _btn("Delete Permanently", "mdi.delete-forever", danger=True)
        self._purge_btn.setEnabled(False)
        self._purge_btn.clicked.connect(self._on_bulk_purge)
        tools_hl.addWidget(self._purge_btn)

        tools_hl.addStretch(1)

        refresh_btn = _btn("Refresh", "mdi.refresh")
        refresh_btn.clicked.connect(self.refresh)
        tools_hl.addWidget(refresh_btn)
        root.addWidget(tools)

        self._table = QTableWidget(0, _NCOLS)
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(True)
        # Phase 3 — Separate Expenses / Master compact density.
        self._table.setStyleSheet(_table_style())
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(_COL_DESC, QHeaderView.Stretch)
        for i, width in enumerate(_COL_DEFAULTS):
            self._table.setColumnWidth(i, width)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemSelectionChanged.connect(self._sync_action_buttons)
        root.addWidget(self._table, 1)

        self._status = _lbl("", size=12, color=_T2)
        root.addWidget(self._status)

    def _selected_ids(self) -> List[ObjectId]:
        ids: List[ObjectId] = []
        for r in range(self._table.rowCount()):
            chk = self._table.item(r, _COL_CHK)
            if chk is not None and chk.checkState() == Qt.Checked:
                if r < len(self._transactions) and self._transactions[r]._id:
                    ids.append(self._transactions[r]._id)
        if ids:
            return ids
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        for r in rows:
            if r < len(self._transactions) and self._transactions[r]._id:
                ids.append(self._transactions[r]._id)
        return ids

    def _sync_action_buttons(self) -> None:
        enabled = bool(self._selected_ids()) and not self._busy
        self._restore_btn.setEnabled(enabled)
        self._purge_btn.setEnabled(enabled)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item is not None and item.column() == _COL_CHK:
            self._sync_action_buttons()

    async def _reload(self) -> None:
        from tahmeed.services.accountant_service import (
            get_trashed_transactions, count_trashed_transactions,
        )
        if self._busy:
            return
        self._busy = True
        show_widget_loading(self, "Loading trash…")
        self._status.setText("Loading…")
        try:
            search = self._search.text().strip()
            txs, total = await asyncio.gather(
                get_trashed_transactions(search=search, limit=500),
                count_trashed_transactions(search=search),
            )
            self._transactions = txs
            self._populate(txs)
            self._subtitle.setText(
                f"{total} soft-deleted expense{'s' if total != 1 else ''}. "
                "Restore to Master or delete permanently."
            )
            self._status.setText(f"Showing {len(txs)} of {total}")
        except Exception as exc:
            self._status.setText(f"Failed to load trash: {exc}")
        finally:
            self._busy = False
            hide_widget_loading(self)
            self._sync_action_buttons()

    def _populate(self, txs: List[Transaction]) -> None:
        t = self._table
        t.blockSignals(True)
        t.clearSpans()
        t.setRowCount(0)
        if not txs:
            t.setRowCount(1)
            empty = QTableWidgetItem("Trash is empty.")
            empty.setTextAlignment(Qt.AlignCenter)
            empty.setForeground(QColor(_TM))
            empty.setFont(_table_cell_font())
            empty.setFlags(Qt.ItemIsEnabled)
            t.setItem(0, 0, empty)
            t.setSpan(0, 0, 1, _NCOLS)
            t.setRowHeight(0, _ROW_H)
            t.blockSignals(False)
            return

        t.setRowCount(len(txs))
        for r, tx in enumerate(txs):
            # Phase 4 — plain Items-style zebra (white / slate), no peach tint.
            row_bg = _stripe_bg(r)

            # Phase 2 — native checkable cell (Day Issues pattern).
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
            )
            check.setCheckState(Qt.Unchecked)
            check.setBackground(QBrush(QColor(row_bg)))
            t.setItem(r, _COL_CHK, check)

            t.setItem(
                r, _COL_NUM,
                _cell(str(r + 1), Qt.AlignCenter | Qt.AlignVCenter, bg=row_bg),
            )
            t.setItem(r, _COL_DATE, _cell(_fmt_date(tx.date), bg=row_bg))
            t.setItem(
                r, _COL_ITEM,
                _cell(tx.item or tx.category_name or "—", bg=row_bg),
            )
            t.setItem(r, _COL_DESC, _cell(tx.description or "—", bg=row_bg))
            t.setItem(r, _COL_TRUCK, _cell(tx.truck_number or "—", bg=row_bg))
            t.setItem(
                r, _COL_AMOUNT,
                _cell(
                    _fmt_money(tx.amount),
                    Qt.AlignRight | Qt.AlignVCenter,
                    bg=row_bg,
                ),
            )
            t.setItem(
                r, _COL_CURR,
                _cell(tx.currency or "TZS", Qt.AlignCenter | Qt.AlignVCenter, bg=row_bg),
            )
            t.setItem(r, _COL_TRASHED, _cell(_fmt_date(tx.trashed_at), bg=row_bg))
            _finish_table_row(t, r, row_bg)
        t.blockSignals(False)

    def _on_bulk_restore(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        if QMessageBox.question(
            self, "Restore",
            f"Restore {len(ids)} expense(s) to Master Expenses and the day table?",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_restore(ids))

    def _on_bulk_purge(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        if QMessageBox.question(
            self, "Delete Permanently",
            f"Permanently delete {len(ids)} expense(s) from Trash?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_purge(ids))

    async def _do_restore(self, ids: List[ObjectId]) -> None:
        from tahmeed.services.accountant_service import bulk_restore_from_trash
        self._busy = True
        try:
            n = await bulk_restore_from_trash(ids)
            QMessageBox.information(
                self, "Restored",
                f"Restored {n} expense(s) to Master Expenses.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Restore failed:\n{exc}")
        finally:
            self._busy = False
            await self._reload()

    async def _do_purge(self, ids: List[ObjectId]) -> None:
        from tahmeed.services.accountant_service import bulk_permanently_delete_trashed
        self._busy = True
        try:
            n = await bulk_permanently_delete_trashed(
                ids, actor_id=getattr(self._user, "_id", None),
            )
            QMessageBox.information(
                self, "Deleted",
                f"Permanently deleted {n} expense(s).",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Delete failed:\n{exc}")
        finally:
            self._busy = False
            await self._reload()
