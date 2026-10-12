"""Cashier — Trash of soft-deleted expenses (request restore via accountant)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List

import qtawesome as qta
from bson import ObjectId
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QLineEdit, QMessageBox, QCheckBox,
)

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading

_BG = "#F4F6F8"
_WHITE = "#FFFFFF"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"
_HDR_BG = "#F3F4F6"
_AMBER = "#B45309"

_COL_CHK = 0
_HEADERS = [
    "", "Date", "Item", "Description", "Truck", "Amount", "Currency",
    "Trashed", "Status",
]
_NCOLS = len(_HEADERS)


def _lbl(text="", size=13, weight=400, color=_T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI',sans-serif;background:transparent;"
    )
    return w


def _btn(text: str, icon: str = "", *, primary=False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(32)
    if icon:
        btn.setIcon(qta.icon(icon, color="#FFF" if primary else _T1))
        btn.setIconSize(QSize(14, 14))
    if primary:
        btn.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:4px;"
            "font-size:12px;font-weight:600;font-family:'Segoe UI',sans-serif;"
            "padding:0 14px;}"
            "QPushButton:hover{background:#005EA3;}"
            "QPushButton:disabled{background:#93C5FD;}"
        )
    else:
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
            "border-radius:4px;font-size:12px;font-weight:500;"
            "font-family:'Segoe UI',sans-serif;padding:0 14px;}"
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


class CashierTrashWidget(QWidget):
    """Cashier trash: request restore (accountant must approve)."""

    def __init__(self, user: User, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._transactions: List[Transaction] = []
        self._busy = False
        self._build()
        asyncio.ensure_future(self._reload())

    def refresh(self) -> None:
        asyncio.ensure_future(self._reload())

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
            "Deleted expenses confirmed by the accountant. "
            "Request restore to send them back for verification.",
            size=12, color=_T2,
        )
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box, 1)

        self._restore_btn = _btn("Request Restore", "mdi.restore", primary=True)
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._on_bulk_restore)
        header.addWidget(self._restore_btn)

        self._cancel_btn = _btn("Cancel Request", "mdi.close")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_bulk_cancel)
        header.addWidget(self._cancel_btn)

        refresh_btn = _btn("Refresh", "mdi.refresh")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
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
        self._search.setFixedHeight(30)
        self._search.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:4px;padding:0 8px;"
            f"font-size:12px;font-family:'Segoe UI',sans-serif;color:{_T1};"
            f"background:{_WHITE};}}"
        )
        self._search.returnPressed.connect(self.refresh)
        tools_hl.addWidget(self._search, 1)

        search_btn = _btn("Search", "mdi.magnify")
        search_btn.clicked.connect(self.refresh)
        tools_hl.addWidget(search_btn)
        root.addWidget(tools)

        self._table = QTableWidget(0, _NCOLS)
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(
            f"QTableWidget{{background:{_WHITE};border:1px solid {_BORDER};"
            f"border-radius:6px;gridline-color:transparent;color:{_T1};"
            "font-family:'Segoe UI',sans-serif;font-size:10pt;}"
            f"QHeaderView::section{{background:{_HDR_BG};color:{_T2};border:none;"
            f"border-bottom:1px solid {_BORDER};padding:6px 8px;font-weight:600;}}"
            f"QTableWidget::item{{padding:4px 8px;color:{_T1};}}"
            f"QTableWidget::item:selected{{background:#DBEAFE;color:{_T1};}}"
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.setColumnWidth(_COL_CHK, 36)
        self._table.setColumnWidth(1, 100)
        self._table.setColumnWidth(2, 120)
        self._table.setColumnWidth(4, 110)
        self._table.setColumnWidth(5, 90)
        self._table.setColumnWidth(6, 80)
        self._table.setColumnWidth(7, 110)
        self._table.setColumnWidth(8, 130)
        self._table.itemSelectionChanged.connect(self._sync_action_buttons)
        root.addWidget(self._table, 1)

        self._status = _lbl("", size=12, color=_T2)
        root.addWidget(self._status)

    def _selected_ids(self, *, pending_only: bool | None = None) -> List[ObjectId]:
        ids: List[ObjectId] = []
        for r in range(self._table.rowCount()):
            chk = self._table.cellWidget(r, _COL_CHK)
            if not (isinstance(chk, QCheckBox) and chk.isChecked()):
                continue
            if r >= len(self._transactions) or not self._transactions[r]._id:
                continue
            tx = self._transactions[r]
            if pending_only is True and not tx.restore_requested:
                continue
            if pending_only is False and tx.restore_requested:
                continue
            ids.append(tx._id)
        if ids:
            return ids
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        for r in rows:
            if r >= len(self._transactions) or not self._transactions[r]._id:
                continue
            tx = self._transactions[r]
            if pending_only is True and not tx.restore_requested:
                continue
            if pending_only is False and tx.restore_requested:
                continue
            ids.append(tx._id)
        return ids

    def _sync_action_buttons(self) -> None:
        can_request = bool(self._selected_ids(pending_only=False)) and not self._busy
        can_cancel = bool(self._selected_ids(pending_only=True)) and not self._busy
        self._restore_btn.setEnabled(can_request)
        self._cancel_btn.setEnabled(can_cancel)

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
            self._subtitle.setText(
                f"{total} deleted expense{'s' if total != 1 else ''}. "
                "Request restore to send them to the accountant for approval."
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
        t.setRowCount(0)
        if not txs:
            t.setRowCount(1)
            empty = QTableWidgetItem("Trash is empty.")
            empty.setTextAlignment(Qt.AlignCenter)
            empty.setForeground(QColor(_TM))
            empty.setFlags(Qt.ItemIsEnabled)
            t.setItem(0, 0, empty)
            t.setSpan(0, 0, 1, _NCOLS)
            t.blockSignals(False)
            return

        t.setRowCount(len(txs))
        tint = QBrush(QColor("#FFF7ED"))
        for r, tx in enumerate(txs):
            chk = QCheckBox()
            chk.setStyleSheet("margin-left:8px;")
            chk.stateChanged.connect(lambda *_: self._sync_action_buttons())
            t.setCellWidget(r, _COL_CHK, chk)

            status = "Restore pending" if tx.restore_requested else "In trash"
            values = [
                "",
                _fmt_date(tx.date),
                tx.item or tx.category_name or "—",
                tx.description or "—",
                tx.truck_number or "—",
                _fmt_money(tx.amount),
                tx.currency or "TZS",
                _fmt_date(tx.trashed_at),
                status,
            ]
            for c, text in enumerate(values):
                if c == _COL_CHK:
                    continue
                item = QTableWidgetItem(text)
                item.setBackground(tint)
                item.setForeground(
                    QBrush(QColor(_AMBER if c == 8 and tx.restore_requested else _T1))
                )
                if c == 5:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(r, c, item)
            t.setRowHeight(r, 32)
        t.blockSignals(False)

    def _on_bulk_restore(self) -> None:
        ids = self._selected_ids(pending_only=False)
        if not ids:
            return
        if QMessageBox.question(
            self, "Request Restore",
            f"Send {len(ids)} expense(s) to the accountant for restore approval?\n"
            "They stay in Trash until approved.",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_restore(ids))

    def _on_bulk_cancel(self) -> None:
        ids = self._selected_ids(pending_only=True)
        if not ids:
            return
        if QMessageBox.question(
            self, "Cancel Restore Request",
            f"Cancel restore request for {len(ids)} expense(s)?\n"
            "They will remain in Trash.",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_cancel(ids))

    async def _do_restore(self, ids: List[ObjectId]) -> None:
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
