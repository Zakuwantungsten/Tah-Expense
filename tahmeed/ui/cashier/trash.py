"""Cashier — Trash of soft-deleted expenses (Accountant Trash layout parity).

- Draft / submitted (unverified) soft-trash: Restore or Delete permanently.
- Verified-path trash (accountant-confirmed): Request Restore / Cancel Request.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Optional

import qtawesome as qta
from bson import ObjectId
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QBrush, QColor
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
from tahmeed.ui.async_utils import schedule_coro
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
_AMBER = "#B45309"

_COL_CHK = 0
_COL_NUM = 1
_COL_DATE = 2
_COL_ITEM = 3
_COL_DESC = 4
_COL_TRUCK = 5
_COL_AMOUNT = 6
_COL_CURR = 7
_COL_TRASHED = 8
_COL_STATUS = 9

_HEADERS = [
    "", "#", "Date", "Item", "Description", "Truck", "Amount", "Currency",
    "Trashed", "Status",
]
_COL_DEFAULTS = [36, 40, 100, 120, 220, 110, 90, 80, 110, 130]
_NCOLS = len(_HEADERS)


def _lbl(text="", size=13, weight=400, color=_T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI',sans-serif;background:transparent;"
    )
    return w


def _btn(text: str, icon: str = "", *, primary=False, danger=False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(32)
    if icon:
        btn.setIcon(
            qta.icon(
                icon,
                color="#FFF" if primary else (_RED if danger else _T1),
            )
        )
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
    color: str = "",
) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    item.setFont(_table_cell_font())
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    if bg:
        item.setBackground(QBrush(QColor(bg)))
    item.setForeground(QBrush(QColor(color or _T1)))
    return item


def _is_own_soft_trash(tx: Transaction) -> bool:
    return not bool(getattr(tx, "verified", False))


class CashierTrashWidget(QWidget):
    """Cashier trash — Accountant Trash layout with role-correct actions."""

    count_changed = Signal(int)

    def __init__(self, user: User, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._transactions: List[Transaction] = []
        self._busy = False
        self._build()
        schedule_coro(self._reload())

    def refresh(self) -> None:
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
            "Own soft-deleted drafts/submitted: restore or purge. "
            "Approved deletions: request restore via accountant.",
            size=12, color=_T2,
        )
        self._subtitle.setWordWrap(True)
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box, 1)
        root.addLayout(header)

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

        self._restore_own_btn = _btn("Restore", "mdi.restore", primary=True)
        self._restore_own_btn.setEnabled(False)
        self._restore_own_btn.setToolTip(
            "Restore selected draft / not-yet-approved expenses from Trash"
        )
        self._restore_own_btn.clicked.connect(self._on_restore_own)
        tools_hl.addWidget(self._restore_own_btn)

        self._purge_own_btn = _btn("Delete Permanently", "mdi.delete-forever", danger=True)
        self._purge_own_btn.setEnabled(False)
        self._purge_own_btn.setToolTip(
            "Permanently delete selected draft / not-yet-approved Trash rows"
        )
        self._purge_own_btn.clicked.connect(self._on_purge_own)
        tools_hl.addWidget(self._purge_own_btn)

        self._request_btn = _btn("Request Restore", "mdi.account-arrow-left")
        self._request_btn.setEnabled(False)
        self._request_btn.setToolTip(
            "Ask the accountant to restore approved expenses from Trash"
        )
        self._request_btn.clicked.connect(self._on_bulk_request_restore)
        tools_hl.addWidget(self._request_btn)

        self._cancel_btn = _btn("Cancel Request", "mdi.close")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_bulk_cancel)
        tools_hl.addWidget(self._cancel_btn)

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

    def _selected_txs(self) -> List[Transaction]:
        txs: List[Transaction] = []
        checked_rows: List[int] = []
        for r in range(self._table.rowCount()):
            chk = self._table.item(r, _COL_CHK)
            if chk is not None and chk.checkState() == Qt.Checked:
                checked_rows.append(r)
        rows = checked_rows or sorted(
            {idx.row() for idx in self._table.selectedIndexes()}
        )
        for r in rows:
            if 0 <= r < len(self._transactions) and self._transactions[r]._id:
                txs.append(self._transactions[r])
        return txs

    def _selected_ids(
        self,
        *,
        own_soft: Optional[bool] = None,
        pending_only: Optional[bool] = None,
        verified_only: Optional[bool] = None,
    ) -> List[ObjectId]:
        ids: List[ObjectId] = []
        for tx in self._selected_txs():
            if own_soft is True and not _is_own_soft_trash(tx):
                continue
            if own_soft is False and _is_own_soft_trash(tx):
                continue
            if verified_only is True and _is_own_soft_trash(tx):
                continue
            if pending_only is True and not tx.restore_requested:
                continue
            if pending_only is False and tx.restore_requested:
                continue
            ids.append(tx._id)
        return ids

    def _sync_action_buttons(self) -> None:
        if self._busy:
            self._restore_own_btn.setEnabled(False)
            self._purge_own_btn.setEnabled(False)
            self._request_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)
            return
        self._restore_own_btn.setEnabled(bool(self._selected_ids(own_soft=True)))
        self._purge_own_btn.setEnabled(bool(self._selected_ids(own_soft=True)))
        self._request_btn.setEnabled(
            bool(self._selected_ids(verified_only=True, pending_only=False))
        )
        self._cancel_btn.setEnabled(
            bool(self._selected_ids(verified_only=True, pending_only=True))
        )

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item is not None and item.column() == _COL_CHK:
            self._sync_action_buttons()

    async def _reload(self) -> None:
        from tahmeed.services.cashier_service import (
            get_cashier_trash, count_cashier_trash,
        )
        if self._busy:
            return
        self._busy = True
        show_widget_loading(self, "Loading trash…")
        self._status.setText("Loading…")
        try:
            search = self._search.text().strip()
            txs, total = await asyncio.gather(
                get_cashier_trash(search=search, limit=500),
                count_cashier_trash(),
            )
            self._transactions = txs
            self._populate(txs)
            own_n = sum(1 for tx in txs if _is_own_soft_trash(tx))
            verified_n = len(txs) - own_n
            self._subtitle.setText(
                f"{total} in Trash"
                f"  ·  {own_n} own (restore / purge)"
                f"  ·  {verified_n} approved (request restore)."
            )
            self._status.setText(f"Showing {len(txs)} of {total}")
            self.count_changed.emit(int(total))
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
            row_bg = _stripe_bg(r)
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
            )
            check.setCheckState(Qt.Unchecked)
            check.setBackground(QBrush(QColor(row_bg)))
            t.setItem(r, _COL_CHK, check)

            own = _is_own_soft_trash(tx)
            if tx.restore_requested:
                status, status_color = "Restore pending", _AMBER
            elif own:
                status, status_color = "Own trash", _BLUE
            else:
                status, status_color = "In trash", _T1

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
            t.setItem(r, _COL_CURR, _cell(tx.currency or "TZS", bg=row_bg))
            t.setItem(r, _COL_TRASHED, _cell(_fmt_date(tx.trashed_at), bg=row_bg))
            t.setItem(
                r, _COL_STATUS,
                _cell(status, bg=row_bg, color=status_color),
            )
            _finish_table_row(t, r, row_bg)
        t.blockSignals(False)

    def _on_restore_own(self) -> None:
        ids = self._selected_ids(own_soft=True)
        if not ids:
            return
        if QMessageBox.question(
            self, "Restore",
            f"Restore {len(ids)} expense(s) from Trash back to the register?",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        schedule_coro(self._do_restore_own(ids))

    def _on_purge_own(self) -> None:
        ids = self._selected_ids(own_soft=True)
        if not ids:
            return
        if QMessageBox.question(
            self, "Delete Permanently",
            f"Permanently delete {len(ids)} expense(s) from Trash?\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        schedule_coro(self._do_purge_own(ids))

    def _on_bulk_request_restore(self) -> None:
        ids = self._selected_ids(verified_only=True, pending_only=False)
        if not ids:
            return
        if QMessageBox.question(
            self, "Request Restore",
            f"Send {len(ids)} approved expense(s) to the accountant for restore?\n"
            "They stay in Trash until approved.",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        schedule_coro(self._do_request_restore(ids))

    def _on_bulk_cancel(self) -> None:
        ids = self._selected_ids(verified_only=True, pending_only=True)
        if not ids:
            return
        if QMessageBox.question(
            self, "Cancel Restore Request",
            f"Cancel restore request for {len(ids)} expense(s)?\n"
            "They will remain in Trash.",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        schedule_coro(self._do_cancel(ids))

    async def _do_restore_own(self, ids: List[ObjectId]) -> None:
        from tahmeed.services.cashier_service import bulk_restore_own_trash
        self._busy = True
        try:
            n = await bulk_restore_own_trash(ids)
            QMessageBox.information(
                self, "Restored",
                f"Restored {n} expense(s) from Trash.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Restore failed:\n{exc}")
        finally:
            self._busy = False
            await self._reload()

    async def _do_purge_own(self, ids: List[ObjectId]) -> None:
        from tahmeed.services.cashier_service import bulk_purge_own_trash
        self._busy = True
        try:
            n = await bulk_purge_own_trash(ids)
            QMessageBox.information(
                self, "Deleted",
                f"Permanently deleted {n} expense(s).",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Delete failed:\n{exc}")
        finally:
            self._busy = False
            await self._reload()

    async def _do_request_restore(self, ids: List[ObjectId]) -> None:
        from tahmeed.services.cashier_service import bulk_request_restore_from_trash
        self._busy = True
        try:
            n = await bulk_request_restore_from_trash(ids)
            QMessageBox.information(
                self, "Restore Requested",
                f"Sent {n} expense(s) to the accountant Verify → Restore tab.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Restore request failed:\n{exc}")
        finally:
            self._busy = False
            await self._reload()

    async def _do_cancel(self, ids: List[ObjectId]) -> None:
        from tahmeed.services.cashier_service import cancel_restore_request
        self._busy = True
        try:
            ok = 0
            for oid in ids:
                if await cancel_restore_request(oid):
                    ok += 1
            QMessageBox.information(
                self, "Cancelled",
                f"Cancelled {ok} restore request(s).",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Cancel failed:\n{exc}")
        finally:
            self._busy = False
            await self._reload()
