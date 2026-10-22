"""Draft inbox — saved-but-not-submitted register entries across days."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import List, Optional, Sequence

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
    discard_draft_day,
    discard_draft_transactions,
    get_draft_day_summaries,
    get_draft_transactions,
)
from tahmeed.ui.cashier.draft_submit import submit_draft_day_with_gates
from tahmeed.services.accountant_service import get_cashier_names
from tahmeed.ui.accountant.separate_expenses import (
    _finish_table_row, _stripe_bg, _table_cell_font, _table_style, _ROW_H,
)
from tahmeed.ui.widgets.column_persistence import (
    apply_pending_column_autofit,
    bind_column_width_persistence,
)
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading

# Date | Saved | Parked | Total | Cashiers — Cashiers stretch
_DAYS_COL_DEFAULTS = [110, 64, 64, 120, 160]
_DAYS_COL_SAVED = 1
_DAYS_COL_PARKED = 2
_DAYS_COL_CASHIERS = 4
# Description | Item | Truck | Amount | Cashier | Dup? — Description stretches
_ROWS_COL_DEFAULTS = [220, 110, 100, 140, 100, 52]
_ROWS_COL_DESC = 0
_ROWS_COL_TRUCK = 2
_ROWS_COL_AMOUNT = 3
_DAYS_COL_KEY = "drafts_days_table_v3"
_ROWS_COL_KEY = "drafts_rows_table_v3"

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_GOLD = "#B18E5E"
_T1 = "#111827"
_T2 = "#6B7280"
_AMBER = "#B45309"


def should_discard_whole_day(
    *,
    selected_row_ids: Sequence[ObjectId],
    day_row_ids: Sequence[ObjectId],
    day_selected: bool = True,
) -> bool:
    """True when the day should be discarded as a whole.

    - Day selected and detail rows not loaded yet → whole day (API by date).
    - No row selection, or every loaded day row selected → whole day.
    - A proper multi-row subset stays a partial discard (caller handles
      single-row ambiguity separately — Qt often auto-selects one row).
    """
    if not day_selected:
        return False
    if not day_row_ids:
        # Left pane has a day; right pane empty/still loading — discard by date.
        return True
    if not selected_row_ids:
        return True
    return set(selected_row_ids) >= set(day_row_ids)


def is_ambiguous_single_row_discard(
    *,
    selected_row_ids: Sequence[ObjectId],
    day_row_ids: Sequence[ObjectId],
) -> bool:
    """True when exactly one of many day rows is selected (often Qt auto-select)."""
    if len(selected_row_ids) != 1:
        return False
    if len(day_row_ids) <= 1:
        return False
    return set(selected_row_ids) < set(day_row_ids)


def discard_target_ids(
    *,
    selected_row_ids: Sequence[ObjectId],
    day_row_ids: Sequence[ObjectId],
    day_selected: bool = True,
) -> List[ObjectId]:
    """IDs to delete for a partial discard (subset of day rows)."""
    if should_discard_whole_day(
        selected_row_ids=selected_row_ids,
        day_row_ids=day_row_ids,
        day_selected=day_selected,
    ):
        return list(day_row_ids)
    return list(selected_row_ids)


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
            "font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}"
            "QPushButton:hover{background:#005EA3;}"
            "QPushButton:disabled{background:#93C5FD;}"
        )
    elif gold:
        style = (
            f"QPushButton{{background:{_GOLD};color:#FFF;border:none;border-radius:4px;"
            "font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}"
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
    item.setFont(_table_cell_font())
    return item


class DraftsView(QWidget):
    """List draft days and rows; submit or discard without opening Verify."""

    open_register_date = Signal(object)  # date
    # date when a specific day changed; None = unknown / refresh lists only
    drafts_changed = Signal(object)
    drafts_submitted = Signal(int)

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
            f"Saved drafts and parked truck rows — not yet submitted ({scope})",
            size=11, color=_T2,
        ))
        hl.addLayout(title_vl, 1)

        self._btn_refresh = _tool_btn("Refresh")
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_open = _tool_btn("Open in Register")
        self._btn_open.clicked.connect(self._on_open_register)
        self._btn_submit = _tool_btn("Submit day", primary=True)
        self._btn_submit.clicked.connect(self._on_submit_day)
        self._btn_discard = _tool_btn("Discard draft", danger=True)
        self._btn_discard.setToolTip(
            "Discard selected rows, or the whole day's draft when no rows are selected."
        )
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
        left_vl.addWidget(_lbl("Days with drafts / parked", size=12, weight=600, color=_T2))

        self._days_table = QTableWidget(0, 5)
        self._days_table.setHorizontalHeaderLabels(
            ["Date", "Saved", "Parked", "Total TZS", "Cashiers"]
        )
        self._days_table.verticalHeader().setVisible(False)
        self._days_table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._days_table.setStyleSheet(_table_style())
        self._days_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._days_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._days_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._days_table.setShowGrid(True)
        days_hdr = self._days_table.horizontalHeader()
        days_hdr.setStretchLastSection(False)
        days_hdr.setSectionsMovable(False)
        days_hdr.setMinimumSectionSize(48)
        for col in range(self._days_table.columnCount()):
            self._days_table.setColumnWidth(col, _DAYS_COL_DEFAULTS[col])
            if col == _DAYS_COL_CASHIERS:
                days_hdr.setSectionResizeMode(col, QHeaderView.Stretch)
            else:
                days_hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._days_table,
            _DAYS_COL_KEY,
            _DAYS_COL_DEFAULTS,
            stretch_columns=(_DAYS_COL_CASHIERS,),
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
        self._rows_table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._rows_table.setStyleSheet(_table_style())
        self._rows_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._rows_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._rows_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._rows_table.setShowGrid(True)
        rows_hdr = self._rows_table.horizontalHeader()
        rows_hdr.setStretchLastSection(False)
        rows_hdr.setSectionsMovable(False)
        rows_hdr.setMinimumSectionSize(48)
        for col in range(self._rows_table.columnCount()):
            self._rows_table.setColumnWidth(col, _ROWS_COL_DEFAULTS[col])
            if col == _ROWS_COL_DESC:
                rows_hdr.setSectionResizeMode(col, QHeaderView.Stretch)
            else:
                rows_hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._rows_table,
            _ROWS_COL_KEY,
            _ROWS_COL_DEFAULTS,
            stretch_columns=(_ROWS_COL_DESC,),
            auto_fit_if_unset=True,
        )
        self._rows_table.itemSelectionChanged.connect(self._sync_discard_label)
        right_vl.addWidget(self._rows_table, 1)

        hint = _lbl(
            "Select a day to work the whole draft. Discard with no row selected "
            "removes every draft entry for that day; select rows to discard only those.",
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
        self._sync_discard_label()

    def refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load_days())

    async def _load_days(self) -> None:
        show_widget_loading(self, "Loading drafts…")
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
            self._populate_days()
            if self._days:
                self._days_table.selectRow(0)
            else:
                self._selected_day = None
                self._rows = []
                self._populate_rows()
        except Exception as exc:
            QMessageBox.critical(self, "Drafts", f"Could not load drafts:\n{exc}")
        finally:
            hide_widget_loading(self)

    def _populate_days(self) -> None:
        self._days_table.setRowCount(len(self._days))
        for i, d in enumerate(self._days):
            day: date = d["date"]
            n = int(d.get("entries_count") or 0)
            parked_n = int(d.get("parked_count") or 0)
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
            self._days_table.setItem(
                i, _DAYS_COL_SAVED, _cell(str(n), align=Qt.AlignCenter)
            )
            self._days_table.setItem(
                i, _DAYS_COL_PARKED, _cell(str(parked_n), align=Qt.AlignCenter)
            )
            self._days_table.setItem(i, 3, _cell(f"TZS {tzs:,.0f}" if tzs else "—"))
            self._days_table.setItem(i, _DAYS_COL_CASHIERS, _cell(cashiers))
            self._days_table.item(i, 0).setData(Qt.UserRole, day)
            _finish_table_row(self._days_table, i, _stripe_bg(i))

        apply_pending_column_autofit(self._days_table)
        self._sync_discard_label()

    def _on_day_selected(self) -> None:
        items = self._days_table.selectedItems()
        if not items:
            return
        day = items[0].data(Qt.UserRole)
        if not isinstance(day, date):
            return
        self._selected_day = day
        self._sync_discard_label()
        asyncio.ensure_future(self._load_rows(day))

    async def _load_rows(self, day: date) -> None:
        show_widget_loading(self, "Loading draft entries…")
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
            self._populate_rows()
        except Exception as exc:
            QMessageBox.critical(self, "Drafts", f"Could not load entries:\n{exc}")
        finally:
            hide_widget_loading(self)

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
            # Master Expenses-style alternating white / slate stripes.
            _finish_table_row(self._rows_table, i, _stripe_bg(i))

        apply_pending_column_autofit(self._rows_table)
        self._autofit_truck_amount_columns()
        # Avoid Qt leaving the first detail row selected after a day load —
        # that made Discard delete only one row instead of the whole day.
        self._rows_table.clearSelection()
        sm = self._rows_table.selectionModel()
        if sm is not None:
            sm.clearCurrentIndex()
        self._sync_discard_label()

    def _autofit_truck_amount_columns(self) -> None:
        """Size Truck / Amount to content so values are not ellipsized."""
        if self._rows_table.rowCount() <= 0:
            return
        self._rows_table._col_width_suspend = True  # type: ignore[attr-defined]
        try:
            for col in (_ROWS_COL_TRUCK, _ROWS_COL_AMOUNT):
                self._rows_table.resizeColumnToContents(col)
                width = self._rows_table.columnWidth(col) + 16
                floor = _ROWS_COL_DEFAULTS[col]
                self._rows_table.setColumnWidth(col, min(max(width, floor), 520))
            saver = getattr(self._rows_table, "_col_width_save", None)
            if callable(saver):
                saver()
        finally:
            self._rows_table._col_width_suspend = False  # type: ignore[attr-defined]

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

    def _day_row_ids(self) -> List[ObjectId]:
        return [tx._id for tx in self._rows if tx._id is not None]

    def _sync_discard_label(self) -> None:
        selected = self._selected_row_ids()
        day_ids = self._day_row_ids()
        day_selected = self._selected_day is not None
        if day_selected and should_discard_whole_day(
            selected_row_ids=selected,
            day_row_ids=day_ids,
            day_selected=day_selected,
        ):
            self._btn_discard.setText("Discard whole day")
        elif day_selected and is_ambiguous_single_row_discard(
            selected_row_ids=selected,
            day_row_ids=day_ids,
        ):
            # Prefer whole-day wording; click will confirm row vs day.
            self._btn_discard.setText("Discard whole day")
        elif selected:
            n = len(selected)
            self._btn_discard.setText(
                f"Discard {n} row{'s' if n != 1 else ''}"
            )
        else:
            self._btn_discard.setText("Discard draft")

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
        cashier_id = None if self._show_all else self._user._id
        n = await submit_draft_day_with_gates(
            self,
            day,
            cashier_id=cashier_id,
            user_id=self._user._id,
        )
        if n is None:
            return
        QMessageBox.information(
            self, "Submitted",
            f"{n:,} entr{'y' if n == 1 else 'ies'} sent to Verify for "
            f"{day.strftime('%d %b %Y')}.",
        )
        self.drafts_submitted.emit(int(n or 0))
        self.drafts_changed.emit(day)
        await self._load_days()

    def _on_discard(self) -> None:
        selected = self._selected_row_ids()
        day_ids = self._day_row_ids()
        day_selected = self._selected_day is not None
        whole_day = should_discard_whole_day(
            selected_row_ids=selected,
            day_row_ids=day_ids,
            day_selected=day_selected,
        )
        if (
            not whole_day
            and day_selected
            and is_ambiguous_single_row_discard(
                selected_row_ids=selected,
                day_row_ids=day_ids,
            )
        ):
            # Qt often leaves one row selected; ask before deleting only that row.
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Discard drafts")
            box.setText(
                "One draft row is selected.\n\n"
                "Discard only that row, or the whole draft day?"
            )
            whole_btn = box.addButton("Whole day", QMessageBox.AcceptRole)
            row_btn = box.addButton("This row only", QMessageBox.ActionRole)
            cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
            box.setDefaultButton(whole_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked == cancel_btn or clicked is None:
                return
            if clicked == whole_btn:
                whole_day = True
            else:
                asyncio.ensure_future(self._do_discard_ids(list(selected)))
                return

        if whole_day:
            if self._selected_day is None:
                QMessageBox.information(
                    self, "Discard",
                    "Select a draft day to discard the whole day.",
                )
                return
            n = len(day_ids) or int(
                next(
                    (
                        int(d.get("entries_count") or 0)
                        + int(d.get("parked_count") or 0)
                        for d in self._days
                        if d.get("date") == self._selected_day
                    ),
                    0,
                )
            )
            label = self._selected_day.strftime("%d %b %Y")
            msg = (
                f"Permanently delete the whole draft for {label}"
                + (f" ({n} entr{'y' if n == 1 else 'ies'})" if n else "")
                + "?\n\nThis cannot be undone."
            )
            resp = QMessageBox.warning(
                self, "Discard drafts",
                msg,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
            asyncio.ensure_future(self._do_discard_day(self._selected_day))
            return

        ids = discard_target_ids(
            selected_row_ids=selected,
            day_row_ids=day_ids,
            day_selected=day_selected,
        )
        if not ids:
            QMessageBox.information(
                self, "Discard",
                "Select a draft day (to discard the whole day) or one or more rows.",
            )
            return
        n = len(ids)
        msg = (
            f"Permanently delete {n} draft entr{'y' if n == 1 else 'ies'}?\n\n"
            "This cannot be undone."
        )
        resp = QMessageBox.warning(
            self, "Discard drafts",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_discard_ids(ids))

    async def _do_discard_day(self, day: date) -> None:
        try:
            role = getattr(self._user, "role", "") or ""
            allow_any = self._show_all and role in ("admin", "accountant")
            n = await discard_draft_day(
                day,
                self._user._id,
                allow_any_cashier=allow_any,
            )
            if n == 0:
                QMessageBox.warning(
                    self, "Discard",
                    "No rows were discarded. You may only discard your own drafts.",
                )
                return
            QMessageBox.information(
                self, "Discarded",
                f"Deleted {n:,} draft entr{'y' if n == 1 else 'ies'} for "
                f"{day.strftime('%d %b %Y')}.",
            )
            self.drafts_changed.emit(day)
            await self._load_days()
        except Exception as exc:
            QMessageBox.critical(self, "Discard Failed", str(exc))

    async def _do_discard_ids(self, ids: List[ObjectId]) -> None:
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
            self.drafts_changed.emit(self._selected_day)
            if self._selected_day:
                await self._load_rows(self._selected_day)
            await self._load_days()
        except Exception as exc:
            QMessageBox.critical(self, "Discard Failed", str(exc))


async def fetch_draft_badge_count(user: User, *, all_cashiers: bool = False) -> int:
    """Sidebar badge helper."""
    cashier_id = None if all_cashiers else user._id
    return await count_draft_transactions(cashier_id, all_cashiers=all_cashiers)
