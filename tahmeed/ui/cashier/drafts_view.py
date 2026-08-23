"""Draft inbox — saved-but-not-submitted register entries across days."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import List, Optional

from bson import ObjectId
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QSplitter,
)

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.services.cashier_service import (
    count_draft_transactions,
    discard_draft_transactions,
    get_draft_day_summaries,
    get_draft_transactions,
    submit_day_for_verify,
)
from tahmeed.services.accountant_service import get_cashier_names
from tahmeed.ui.widgets.column_persistence import (
    apply_pending_column_autofit,
    bind_column_width_persistence,
)

_DAYS_COL_DEFAULTS = [110, 72, 120, 180]
_ROWS_COL_DEFAULTS = [280, 100, 90, 110, 100, 52]
_DAYS_COL_KEY = "drafts_days_table"
_ROWS_COL_KEY = "drafts_rows_table"

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_GOLD = "#B18E5E"
_T1 = "#111827"
_T2 = "#6B7280"
_AMBER = "#B45309"
_HDR_BG = "#FFFBEB"


def _lbl(text: str = "", *, size: int = 13, weight: int = 400, color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI',sans-serif;background:transparent;"
    )
    return w


def _tool_btn(text: str, *, primary: bool = False, gold: bool = False, danger: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(30)
    if primary:
        style = (
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:4px;"
            "font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
            "QPushButton:hover{background:#005EA3;}"
            "QPushButton:disabled{background:#93C5FD;}"
        )
    elif gold:
        style = (
            f"QPushButton{{background:{_GOLD};color:#FFF;border:none;border-radius:4px;"
            "font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
            "QPushButton:hover{background:#9A784C;}"
            "QPushButton:disabled{background:#D6C4A8;}"
        )
    elif danger:
        style = (
            "QPushButton{background:transparent;color:#DC2626;border:1px solid #FECACA;"
            "border-radius:4px;font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}"
            "QPushButton:hover{background:#FEF2F2;}"
            "QPushButton:disabled{color:#FCA5A5;border-color:#FEE2E2;}"
        )
    else:
        style = (
            f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
            "border-radius:4px;font-size:12px;font-weight:500;font-family:'Segoe UI';padding:0 14px;}"
            f"QPushButton:hover{{background:{_BG};}}"
            "QPushButton:disabled{color:#D1D5DB;}"
        )
    btn.setStyleSheet(style)
    return btn


def _cell(text: object, *, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(align)
    return item


class DraftsView(QWidget):
    """List draft days and rows; submit or discard without opening Verify."""

    open_register_date = Signal(object)  # date
    drafts_changed = Signal()

    def __init__(self, user: User, *, show_all_cashiers: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._show_all = bool(show_all_cashiers)
        self._days: List[dict] = []
        self._rows: List[Transaction] = []
        self._selected_day: Optional[date] = None
        self._cashier_names: dict = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(f"background:{_WHITE};border-bottom:1px solid {_BORDER};")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 14, 20, 14)
        hl.setSpacing(12)

        title_vl = QVBoxLayout()
        title_vl.setSpacing(2)
        title_vl.addWidget(_lbl("Drafts", size=18, weight=700, color="#1B2B4B"))
        scope = "all cashiers" if self._show_all else "your entries"
        title_vl.addWidget(_lbl(
            f"Saved but not submitted to Verify ({scope})",
            size=11, color=_T2,
        ))
        hl.addLayout(title_vl, 1)

        self._btn_refresh = _tool_btn("Refresh")
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_open = _tool_btn("Open in Register")
        self._btn_open.clicked.connect(self._on_open_register)
        self._btn_submit = _tool_btn("Submit day", gold=True)
        self._btn_submit.clicked.connect(self._on_submit_day)
        self._btn_discard = _tool_btn("Discard selected", danger=True)
        self._btn_discard.clicked.connect(self._on_discard)
        for b in (self._btn_refresh, self._btn_open, self._btn_submit, self._btn_discard):
            hl.addWidget(b)
        root.addWidget(header)

        split = QSplitter(Qt.Horizontal)
        split.setStyleSheet(f"background:{_BG};")

        left = QFrame()
        left.setStyleSheet(f"background:{_WHITE};")
        left_vl = QVBoxLayout(left)
        left_vl.setContentsMargins(12, 12, 12, 12)
        left_vl.setSpacing(8)
        left_vl.addWidget(_lbl("Days with drafts", size=12, weight=600, color=_T2))

        self._days_table = QTableWidget(0, 4)
        self._days_table.setHorizontalHeaderLabels(["Date", "Entries", "Total TZS", "Cashiers"])
        self._days_table.verticalHeader().setVisible(False)
        self._days_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._days_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._days_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        days_hdr = self._days_table.horizontalHeader()
        days_hdr.setStretchLastSection(False)
        days_hdr.setSectionsMovable(False)
        for col in range(self._days_table.columnCount()):
            days_hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._days_table,
            _DAYS_COL_KEY,
            _DAYS_COL_DEFAULTS,
            auto_fit_if_unset=True,
        )
        self._days_table.itemSelectionChanged.connect(self._on_day_selected)
        left_vl.addWidget(self._days_table, 1)

        right = QFrame()
        right.setStyleSheet(f"background:{_WHITE};")
        right_vl = QVBoxLayout(right)
        right_vl.setContentsMargins(12, 12, 12, 12)
        right_vl.setSpacing(8)
        self._detail_title = _lbl("Select a day", size=12, weight=600, color=_T2)
        right_vl.addWidget(self._detail_title)

        self._rows_table = QTableWidget(0, 6)
        self._rows_table.setHorizontalHeaderLabels([
            "Description", "Item", "Truck", "Amount", "Cashier", "Dup?",
        ])
        self._rows_table.verticalHeader().setVisible(False)
        self._rows_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._rows_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._rows_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rows_hdr = self._rows_table.horizontalHeader()
        rows_hdr.setStretchLastSection(False)
        rows_hdr.setSectionsMovable(False)
        for col in range(self._rows_table.columnCount()):
            rows_hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._rows_table,
            _ROWS_COL_KEY,
            _ROWS_COL_DEFAULTS,
            auto_fit_if_unset=True,
        )
        right_vl.addWidget(self._rows_table, 1)

        hint = _lbl(
            "Orange rows on the Daily Register are drafts. Submit sends the whole day to Verify.",
            size=11, color=_AMBER,
        )
        hint.setWordWrap(True)
        right_vl.addWidget(hint)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        root.addWidget(split, 1)

        self.setStyleSheet(f"background:{_BG};")

    def refresh(self) -> None:
        asyncio.ensure_future(self._load_days())

    async def _load_days(self) -> None:
        try:
            cashier_id = None if self._show_all else self._user._id
            self._days = await get_draft_day_summaries(
                cashier_id,
                all_cashiers=self._show_all,
            )
            ids = []
            for d in self._days:
                ids.extend(d.get("cashier_ids") or [])
            self._cashier_names = await get_cashier_names(ids) if ids else {}
        except Exception as exc:
            QMessageBox.critical(self, "Drafts", f"Could not load drafts:\n{exc}")
            return
        self._populate_days()
        if self._days:
            self._days_table.selectRow(0)
        else:
            self._selected_day = None
            self._rows = []
            self._populate_rows()

    def _populate_days(self) -> None:
        self._days_table.setRowCount(len(self._days))
        for i, d in enumerate(self._days):
            day: date = d["date"]
            n = int(d.get("entries_count") or 0)
            tzs = float(d.get("total_tzs") or 0.0)
            cids = d.get("cashier_ids") or []
            if self._show_all:
                names = sorted({
                    self._cashier_names.get(cid, str(cid)[:6])
                    for cid in cids if cid
                })
                cashiers = ", ".join(names) if names else "—"
            else:
                cashiers = "You"
            self._days_table.setItem(i, 0, _cell(day.strftime("%d %b %Y")))
            self._days_table.setItem(i, 1, _cell(str(n), align=Qt.AlignCenter))
            self._days_table.setItem(i, 2, _cell(f"TZS {tzs:,.0f}" if tzs else "—"))
            self._days_table.setItem(i, 3, _cell(cashiers))
            self._days_table.item(i, 0).setData(Qt.UserRole, day)

        apply_pending_column_autofit(self._days_table)

    def _on_day_selected(self) -> None:
        items = self._days_table.selectedItems()
        if not items:
            return
        day = items[0].data(Qt.UserRole)
        if not isinstance(day, date):
            return
        self._selected_day = day
        asyncio.ensure_future(self._load_rows(day))

    async def _load_rows(self, day: date) -> None:
        try:
            merged = self._show_all
            cashier_id = None if merged else self._user._id
            self._rows = await get_draft_transactions(
                day, cashier_id, merged=merged,
            )
            ids = [tx.cashier_id for tx in self._rows if tx.cashier_id]
            if ids:
                extra = await get_cashier_names(ids)
                self._cashier_names.update(extra)
        except Exception as exc:
            QMessageBox.critical(self, "Drafts", f"Could not load entries:\n{exc}")
            return
        self._populate_rows()

    def _populate_rows(self) -> None:
        if self._selected_day:
            self._detail_title.setText(
                f"{self._selected_day.strftime('%d %b %Y')} — "
                f"{len(self._rows)} draft entr{'y' if len(self._rows) == 1 else 'ies'}"
            )
        else:
            self._detail_title.setText("Select a day")

        self._rows_table.setRowCount(len(self._rows))
        for i, tx in enumerate(self._rows):
            tzs, usd = tx.money_parts()
            if tzs and usd:
                amt = f"TZS {tzs:,.0f} / USD {usd:,.2f}"
            elif tzs:
                amt = f"TZS {tzs:,.0f}"
            elif usd:
                amt = f"USD {usd:,.2f}"
            else:
                amt = "—"
            cashier = self._cashier_names.get(tx.cashier_id, "—") if tx.cashier_id else "—"
            dup = "Yes" if getattr(tx, "possible_duplicate", False) else ""
            self._rows_table.setItem(i, 0, _cell(tx.description or "—"))
            self._rows_table.setItem(i, 1, _cell(tx.item or "—"))
            self._rows_table.setItem(i, 2, _cell(tx.truck_number or "—"))
            self._rows_table.setItem(i, 3, _cell(amt))
            self._rows_table.setItem(i, 4, _cell(cashier))
            self._rows_table.setItem(i, 5, _cell(dup, align=Qt.AlignCenter))
            for col in range(6):
                self._rows_table.item(i, col).setData(Qt.UserRole, tx._id)
                self._rows_table.item(i, col).setBackground(QBrush(QColor(_HDR_BG)))

        apply_pending_column_autofit(self._rows_table)

    def _selected_row_ids(self) -> List[ObjectId]:
        rows = sorted({idx.row() for idx in self._rows_table.selectedIndexes()})
        ids: List[ObjectId] = []
        for row in rows:
            if row < 0 or row >= len(self._rows):
                continue
            oid = self._rows[row]._id
            if oid is not None:
                ids.append(oid)
        return ids

    def _on_open_register(self) -> None:
        if self._selected_day is None:
            QMessageBox.information(self, "Drafts", "Select a day first.")
            return
        self.open_register_date.emit(self._selected_day)

    def _on_submit_day(self) -> None:
        if self._selected_day is None:
            QMessageBox.information(self, "Drafts", "Select a day first.")
            return
        label = self._selected_day.strftime("%d %b %Y")
        scope = "the whole day's transactions (all cashiers)" if self._show_all else "your draft entries for that day"
        resp = QMessageBox.question(
            self, "Submit for Verify",
            f"Submit all draft entries for {label} to the Verify inbox?\n\n"
            f"This sends {scope}.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if resp != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_submit_day(self._selected_day))

    async def _do_submit_day(self, day: date) -> None:
        try:
            n = await submit_day_for_verify(day)
            QMessageBox.information(
                self, "Submitted",
                f"{n:,} entr{'y' if n == 1 else 'ies'} sent to Verify for "
                f"{day.strftime('%d %b %Y')}.",
            )
            self.drafts_changed.emit()
            await self._load_days()
        except Exception as exc:
            QMessageBox.critical(self, "Submit Failed", str(exc))

    def _on_discard(self) -> None:
        ids = self._selected_row_ids()
        if not ids:
            QMessageBox.information(self, "Discard", "Select one or more draft rows to discard.")
            return
        n = len(ids)
        resp = QMessageBox.warning(
            self, "Discard drafts",
            f"Permanently delete {n} draft entr{'y' if n == 1 else 'ies'}?\n\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_discard(ids))

    async def _do_discard(self, ids: List[ObjectId]) -> None:
        try:
            role = getattr(self._user, "role", "") or ""
            allow_any = self._show_all and role in ("admin", "accountant")
            n = await discard_draft_transactions(
                ids,
                self._user._id,
                allow_any_cashier=allow_any,
            )
            if n == 0:
                QMessageBox.warning(
                    self, "Discard",
                    "No rows were discarded. You may only discard your own drafts.",
                )
                return
            self.drafts_changed.emit()
            if self._selected_day:
                await self._load_rows(self._selected_day)
            await self._load_days()
        except Exception as exc:
            QMessageBox.critical(self, "Discard Failed", str(exc))


async def fetch_draft_badge_count(user: User, *, all_cashiers: bool = False) -> int:
    """Sidebar badge helper."""
    cashier_id = None if all_cashiers else user._id
    return await count_draft_transactions(cashier_id, all_cashiers=all_cashiers)
