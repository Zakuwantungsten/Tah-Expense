"""CashierCategoryView — Read-only table of a cashier's own entries for one category."""

from __future__ import annotations

import asyncio
from typing import List, Optional

import qtawesome as qta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QColor, QBrush

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.services.cashier_service import get_transactions_by_category

# ── Design tokens (match accountant palette) ──────────────────────────────────────
_WHITE   = "#FFFFFF"
_BG      = "#F4F6F8"
_BORDER  = "#E5E7EB"
_BLUE    = "#0077C5"
_STRIPE  = "#E8F4FD"
_GREEN   = "#16A34A"
_GREEN_L = "#DCFCE7"
_AMBER   = "#D97706"
_AMBER_L = "#FEF3C7"
_RED     = "#DC2626"
_RED_L   = "#FEE2E2"
_T1      = "#111827"
_T2      = "#6B7280"
_HDR_BG  = "#F1F5F9"

_COLS = [
    ("S/NO",        52,  "center"),
    ("DATE",        100, "left"),
    ("ITEM",        110, "left"),
    ("DESCRIPTION", 260, "left"),
    ("TRUCK NO.",   95,  "left"),
    ("MEMO",        140, "left"),
    ("NOTES",       56,  "center"),
    ("TZS",         120, "right"),
    ("RECEIPT",     110, "center"),
    ("OWNERSHIP",   95,  "left"),
    ("APR BY",      90,  "left"),
]

_RECEIPT_MAP = {
    "received": ("Received",   _GREEN, _GREEN_L),
    "pending":  ("Pending",    _AMBER, _AMBER_L),
    "missing":  ("No Receipt", _RED,   _RED_L),
}

_TABLE_SS = (
    f"QTableWidget {{"
    f"  background: {_WHITE}; gridline-color: {_BORDER};"
    f"  font-size: 12px; font-family: 'Segoe UI', sans-serif;"
    f"  color: {_T1}; border: none;"
    f"  selection-background-color: #DBEAFE; selection-color: {_T1};"
    f"}}"
    f"QTableWidget::item {{ padding: 2px 6px; border: none; }}"
    f"QHeaderView::section {{"
    f"  background: {_HDR_BG}; color: {_T2};"
    f"  font-size: 11px; font-weight: 600; font-family: 'Segoe UI', sans-serif;"
    f"  border: none; border-bottom: 2px solid {_BORDER};"
    f"  border-right: 1px solid {_BORDER}; padding: 0 6px; min-height: 30px;"
    f"}}"
    f"QHeaderView::section:hover {{ background: #E2E8F0; }}"
    f"QScrollBar:horizontal {{ background: {_BG}; height: 8px; margin: 0; }}"
    f"QScrollBar::handle:horizontal {{ background: #D1D5DB; border-radius: 4px; min-width: 24px; }}"
    f"QScrollBar:vertical {{ background: {_BG}; width: 8px; margin: 0; }}"
    f"QScrollBar::handle:vertical {{ background: #D1D5DB; border-radius: 4px; min-height: 24px; }}"
    f"QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}"
)


class CashierCategoryView(QWidget):
    """Read-only view of the cashier's own submitted entries for a single category."""

    def __init__(
        self,
        user: User,
        category_key: str,
        category_name: str,
        icon_name: str = "mdi.tag-outline",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._name = category_name
        self._icon_name = icon_name
        self._rows: List[Transaction] = []
        self._build()

    def _build(self) -> None:
        self.setObjectName("cashierCatView")
        self.setStyleSheet(f"QWidget#cashierCatView {{ background: {_BG}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Page header ───────────────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("catViewHeader")
        header.setFixedHeight(60)
        header.setStyleSheet(
            f"QFrame#catViewHeader {{"
            f"  background: {_WHITE}; border-bottom: 1px solid {_BORDER};"
            f"}}"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 0, 20, 0)
        hl.setSpacing(12)

        icon_box = QLabel()
        icon_box.setFixedSize(36, 36)
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setStyleSheet(f"background: {_BLUE}; border-radius: 8px;")
        try:
            icon_box.setPixmap(
                qta.icon(self._icon_name, color="#FFFFFF").pixmap(QSize(20, 20))
            )
        except Exception:
            pass
        hl.addWidget(icon_box)

        text_col = QWidget()
        text_col.setStyleSheet("background: transparent;")
        tcl = QVBoxLayout(text_col)
        tcl.setContentsMargins(0, 0, 0, 0)
        tcl.setSpacing(1)
        title = QLabel(self._name)
        title.setStyleSheet(
            f"color: {_T1}; font-size: 16px; font-weight: 700;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        subtitle = QLabel("Your submitted entries · read-only")
        subtitle.setStyleSheet(
            f"color: {_T2}; font-size: 11px;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        tcl.addWidget(title)
        tcl.addWidget(subtitle)
        hl.addWidget(text_col)
        hl.addStretch()

        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(
            f"color: {_T2}; font-size: 12px;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        hl.addWidget(self._count_lbl)

        root.addWidget(header)

        # ── Table ─────────────────────────────────────────────────────────────
        self._table = QTableWidget(0, len(_COLS))
        self._table.setStyleSheet(_TABLE_SS)
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLS])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(True)
        self._table.setSortingEnabled(True)
        hdr = self._table.horizontalHeader()
        for i, (_, w, _align) in enumerate(_COLS):
            self._table.setColumnWidth(i, w)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.verticalHeader().setDefaultSectionSize(34)
        root.addWidget(self._table, 1)

        # ── Footer ────────────────────────────────────────────────────────────
        footer = QFrame()
        footer.setObjectName("catViewFooter")
        footer.setFixedHeight(36)
        footer.setStyleSheet(
            f"QFrame#catViewFooter {{"
            f"  background: {_WHITE}; border-top: 1px solid {_BORDER};"
            f"}}"
        )
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(20, 0, 20, 0)
        fl.setSpacing(0)
        self._footer_lbl = QLabel("")
        self._footer_lbl.setStyleSheet(
            f"color: {_T2}; font-size: 11px;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        fl.addWidget(self._footer_lbl)
        fl.addStretch()
        root.addWidget(footer)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        try:
            self._rows = await get_transactions_by_category(
                cashier_id=self._user._id,
                category_name=self._name,
            )
        except Exception:
            self._rows = []
        self._populate()

    def _populate(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._rows))
        total = 0.0
        for r, tx in enumerate(self._rows):
            self._table.setRowHeight(r, 34)
            bg = QColor(_STRIPE) if r % 2 == 0 else QColor(_WHITE)

            def _cell(text: str, align=Qt.AlignLeft | Qt.AlignVCenter, mono=False):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                it.setTextAlignment(align)
                it.setBackground(QBrush(bg))
                if mono:
                    it.setFont(QFont("Consolas", 11))
                return it

            self._table.setItem(r, 0, _cell(str(r + 1), Qt.AlignCenter | Qt.AlignVCenter))
            date_str = tx.date.strftime("%d %b %Y") if tx.date else ""
            self._table.setItem(r, 1, _cell(date_str))
            self._table.setItem(r, 2, _cell(tx.item or ""))
            self._table.setItem(r, 3, _cell(tx.description or ""))
            self._table.setItem(r, 4, _cell(tx.truck_number or ""))
            self._table.setItem(r, 5, _cell(tx.memo or ""))

            notes_it = QTableWidgetItem("✓" if tx.notes_flag else "")
            notes_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            notes_it.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            notes_it.setBackground(QBrush(bg))
            self._table.setItem(r, 6, notes_it)

            amount = tx.amount or 0.0
            total += amount
            amt_it = _cell(f"{amount:,.0f}", Qt.AlignRight | Qt.AlignVCenter, mono=True)
            amt_it.setForeground(QBrush(QColor(_RED if amount < 0 else _T1)))
            self._table.setItem(r, 7, amt_it)

            rs = (tx.receipt_status or "pending").lower()
            label, fg, _ = _RECEIPT_MAP.get(rs, ("Pending", _AMBER, _AMBER_L))
            rcpt_it = QTableWidgetItem(label)
            rcpt_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            rcpt_it.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            rcpt_it.setBackground(QBrush(bg))
            rcpt_it.setForeground(QBrush(QColor(fg)))
            self._table.setItem(r, 8, rcpt_it)

            self._table.setItem(r, 9, _cell(tx.ownership or ""))
            self._table.setItem(r, 10, _cell(tx.approver or ""))

        self._table.setSortingEnabled(True)
        count = len(self._rows)
        self._count_lbl.setText(f"{count} entr{'ies' if count != 1 else 'y'}")
        self._footer_lbl.setText(f"{count} entries  ·  TZS {total:,.0f}")
