"""Cashier — Rejected Entries panel.

Shows all entries that have been rejected by the accountant.  The cashier can
see the rejection reason and click "Edit & Resubmit" to correct the entry and
send it back to the accountant's New inbox tab.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, date
from typing import List, Optional

import qtawesome as qta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPushButton, QDialog, QFormLayout, QLineEdit, QComboBox,
    QDialogButtonBox, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QFont

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User

# ── Design tokens ────────────────────────────────────────────────────────────
_WHITE  = "#FFFFFF"
_BG     = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE   = "#0077C5"
_RED    = "#DC2626"
_RED_L  = "#FEF2F2"
_T1     = "#111827"
_T2     = "#6B7280"
_TM     = "#9CA3AF"
_HDR_BG = "#FFF0F0"
_HDR_FG = "#991B1B"

_HEADERS = ["S/N", "DATE", "ITEM", "DESCRIPTION", "TRUCK", "AMOUNT", "REJECTION REASON", ""]
_COL_SN    = 0
_COL_DATE  = 1
_COL_ITEM  = 2
_COL_DESC  = 3
_COL_TRUCK = 4
_COL_AMT   = 5
_COL_RSN   = 6
_COL_ACT   = 7
_NCOLS     = 8


def _cell(text, align=Qt.AlignLeft | Qt.AlignVCenter, color="") -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    if color:
        item.setForeground(QColor(color))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _fmt_date(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%d %b %y")
    if isinstance(dt, date):
        return dt.strftime("%d %b %y")
    return str(dt) if dt else "—"


def _lbl(text="", size=13, weight=400, color=_T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI',sans-serif;background:transparent;"
    )
    return w


# ── Edit & Resubmit dialog ───────────────────────────────────────────────────

class _EditRejectedDialog(QDialog):
    """Compact dialog that lets the cashier correct a rejected entry and
    resubmit it.  On accept the caller applies the returned updates dict."""

    def __init__(self, tx: Transaction, parent=None) -> None:
        super().__init__(parent)
        self._tx = tx
        self.setWindowTitle("Edit & Resubmit")
        self.setMinimumWidth(420)
        self.setStyleSheet(
            f"QDialog{{background:{_WHITE};}}"
            f"QLabel{{color:{_T1};font-size:13px;font-family:'Segoe UI',sans-serif;}}"
            f"QLineEdit,QComboBox{{background:{_WHITE};border:1px solid {_BORDER};"
            "border-radius:4px;padding:4px 8px;font-size:13px;"
            "font-family:'Segoe UI',sans-serif;}"
            f"QLineEdit:focus,QComboBox:focus{{border-color:{_BLUE};}}"
        )
        self._build()

    def _build(self) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 16, 20, 16)
        vl.setSpacing(12)

        # Rejection reason banner
        rsn = self._tx.rejection_reason or "No reason provided"
        banner = QFrame()
        banner.setStyleSheet(
            f"QFrame{{background:{_RED_L};border:1px solid #FECACA;border-radius:6px;}}"
        )
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(10, 8, 10, 8)
        try:
            ico = QLabel()
            ico.setFixedSize(18, 18)
            ico.setPixmap(qta.icon("mdi.alert-circle-outline", color=_RED).pixmap(18, 18))
            ico.setStyleSheet("background:transparent;")
            bl.addWidget(ico)
        except Exception:
            pass
        lbl = QLabel(f"<b>Rejected:</b> {rsn}")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"color:{_RED};font-size:12px;font-family:'Segoe UI',sans-serif;"
            "background:transparent;"
        )
        bl.addWidget(lbl, 1)
        vl.addWidget(banner)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)

        tx = self._tx
        self._f_desc = QLineEdit(tx.description or "")
        self._f_truck = QLineEdit(tx.truck_number or "")
        self._f_item = QLineEdit(tx.item or "")
        self._f_amount = QLineEdit(str(tx.amount) if tx.amount else "")
        self._f_memo = QLineEdit(tx.memo or "")
        self._f_receipt = QComboBox()
        for opt in ("pending", "received", "missing"):
            self._f_receipt.addItem(opt.capitalize(), opt)
        idx = self._f_receipt.findData(tx.receipt_status or "pending")
        if idx >= 0:
            self._f_receipt.setCurrentIndex(idx)

        form.addRow("Description:", self._f_desc)
        form.addRow("Truck:", self._f_truck)
        form.addRow("Item:", self._f_item)
        form.addRow("Amount:", self._f_amount)
        form.addRow("Memo:", self._f_memo)
        form.addRow("Receipt:", self._f_receipt)
        vl.addLayout(form)

        btns = QDialogButtonBox()
        submit = btns.addButton("Resubmit", QDialogButtonBox.AcceptRole)
        submit.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:4px;"
            "font-size:13px;font-weight:600;font-family:'Segoe UI',sans-serif;"
            "padding:6px 18px;}}"
            "QPushButton:hover{background:#005EA3;}"
        )
        cancel = btns.addButton("Cancel", QDialogButtonBox.RejectRole)
        cancel.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_T2};border:1px solid {_BORDER};"
            "border-radius:4px;font-size:13px;padding:6px 14px;}}"
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        vl.addWidget(btns, 0, Qt.AlignRight)

    def get_updates(self) -> Optional[dict]:
        try:
            amount = float(self._f_amount.text().replace(",", "").strip())
        except ValueError:
            return None
        return {
            "description":   self._f_desc.text().strip(),
            "truck_number":  self._f_truck.text().strip(),
            "item":          self._f_item.text().strip(),
            "amount":        amount,
            "memo":          self._f_memo.text().strip(),
            "receipt_status": self._f_receipt.currentData(),
            "rejected":      False,
            "rejection_reason": None,
        }


# ── Main view ────────────────────────────────────────────────────────────────

class RejectedView(QWidget):
    """Panel listing all entries rejected by the accountant for this cashier."""

    def __init__(self, user: User, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._transactions: List[Transaction] = []
        self._build()

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

        # Info banner
        info = QFrame()
        info.setFixedHeight(38)
        info.setStyleSheet(
            f"QFrame{{background:{_RED_L};border-bottom:1px solid #FECACA;}}"
        )
        il = QHBoxLayout(info)
        il.setContentsMargins(20, 0, 20, 0)
        banner_lbl = _lbl(
            "These entries were rejected by the accountant. "
            "Click 'Edit & Resubmit' to correct and send back for review.",
            11, 400, _RED,
        )
        banner_lbl.setWordWrap(True)
        il.addWidget(banner_lbl)
        root.addWidget(info)

        # Table card
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(20, 16, 20, 16)
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
        t = QTableWidget(0, _NCOLS)
        t.setHorizontalHeaderLabels(_HEADERS)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setAlternatingRowColors(False)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(38)
        t.setShowGrid(True)
        t.setStyleSheet(
            f"QTableWidget{{background:{_WHITE};gridline-color:{_BORDER};"
            "border:none;font-size:12px;font-family:'Segoe UI',sans-serif;}}"
            f"QTableWidget::item{{padding:0 6px;color:{_T1};}}"
            f"QTableWidget::item:selected{{background:#FEE2E2;color:#7F1D1D;}}"
            f"QHeaderView::section{{background:{_HDR_BG};color:{_HDR_FG};"
            "font-size:11px;font-weight:700;font-family:'Segoe UI',sans-serif;"
            f"border:none;border-right:1px solid {_BORDER};"
            f"border-bottom:2px solid {_RED};padding:0 6px;height:32px;}}"
            "QScrollBar:vertical{width:8px;background:transparent;}"
            "QScrollBar::handle:vertical{background:#D1D5DB;border-radius:4px;}"
        )
        hdr = t.horizontalHeader()
        hdr.setHighlightSections(False)
        col_widths = [44, 80, 90, 0, 90, 120, 0, 130]
        for c, w in enumerate(col_widths):
            if w == 0:
                hdr.setSectionResizeMode(c, QHeaderView.Stretch)
            else:
                hdr.setSectionResizeMode(c, QHeaderView.Fixed)
                t.setColumnWidth(c, w)
        return t

    # ── Data loading ─────────────────────────────────────────────────────────

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services.cashier_service import get_rejected_transactions_for_cashier
        try:
            txs = await get_rejected_transactions_for_cashier(self._user._id)
            self._transactions = txs
            self._fill_table(txs)
        except Exception as exc:
            self._show_empty(f"Failed to load: {exc}")

    # ── Table rendering ──────────────────────────────────────────────────────

    def _fill_table(self, txs: List[Transaction]) -> None:
        t = self._table
        t.blockSignals(True)
        t.setRowCount(0)
        self._count_badge.setText(str(len(txs)))

        if not txs:
            t.blockSignals(False)
            self._show_empty(
                "No rejected entries. All your submissions are in good standing."
            )
            return

        for i, tx in enumerate(txs):
            r = t.rowCount()
            t.insertRow(r)

            t.setItem(r, _COL_SN,    _cell(str(i + 1), Qt.AlignCenter | Qt.AlignVCenter, _T2))
            t.setItem(r, _COL_DATE,  _cell(_fmt_date(tx.date), color=_T2))
            t.setItem(r, _COL_ITEM,  _cell(tx.item or "—"))
            t.setItem(r, _COL_DESC,  _cell(tx.description or "—"))
            t.setItem(r, _COL_TRUCK, _cell(tx.truck_number or "—", color=_T2))

            currency = tx.currency or "TZS"
            amt = QTableWidgetItem(f"{currency} {tx.amount:,.0f}")
            amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            amt.setFont(QFont("Cascadia Code", 11))
            amt.setFlags(amt.flags() & ~Qt.ItemIsEditable)
            t.setItem(r, _COL_AMT, amt)

            reason = tx.rejection_reason or "—"
            reason_item = _cell(reason, color=_RED)
            reason_item.setToolTip(reason)
            t.setItem(r, _COL_RSN, reason_item)

            # Edit & Resubmit button
            btn = QPushButton("Edit & Resubmit")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setStyleSheet(
                f"QPushButton{{background:{_BLUE};color:#FFF;border:none;"
                "border-radius:4px;font-size:11px;font-weight:600;"
                "font-family:'Segoe UI',sans-serif;padding:0 10px;}}"
                "QPushButton:hover{background:#005EA3;}"
            )
            btn.clicked.connect(lambda _, tx=tx: self._on_edit(tx))
            t.setCellWidget(r, _COL_ACT, btn)

            # Red tint on all cell items
            for c in range(_NCOLS - 1):
                cell = t.item(r, c)
                if cell:
                    cell.setBackground(QColor(_RED_L))

        t.blockSignals(False)

    def _show_empty(self, msg: str) -> None:
        t = self._table
        t.blockSignals(True)
        t.setRowCount(1)
        for c in range(_NCOLS):
            t.setItem(0, c, QTableWidgetItem(""))
        item = QTableWidgetItem(msg)
        item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        item.setForeground(QColor(_TM))
        item.setFlags(Qt.ItemIsEnabled)
        t.setItem(0, 0, item)
        t.setSpan(0, 0, 1, _NCOLS)
        t.blockSignals(False)

    # ── Actions ──────────────────────────────────────────────────────────────

    def _on_edit(self, tx: Transaction) -> None:
        dlg = _EditRejectedDialog(tx, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        updates = dlg.get_updates()
        if updates is None:
            return
        asyncio.ensure_future(self._do_resubmit(tx, updates))

    async def _do_resubmit(self, tx: Transaction, updates: dict) -> None:
        from datetime import datetime as _dt
        from tahmeed.services.cashier_service import update_transaction
        updates["last_edited_at"] = _dt.utcnow()
        updates["last_edited_by"] = self._user._id
        try:
            await update_transaction(tx._id, updates)
            await self._load()
        except Exception as exc:
            print(f"[RejectedView] resubmit failed: {exc}")
