"""Saved inbox — one summary row per draft day (not per transaction).

Same Mongo draft rows as the Drafts tab (so discard/submit stay in sync).
Actions per day via ⋯ / right-click menu → Open / Submit / Discard.
Double-click opens Merged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.services.cashier_service import (
    discard_draft_day,
    get_draft_day_summaries,
    get_draft_transactions,
)
from tahmeed.ui.accountant.separate_expenses import (
    _finish_table_row, _table_cell_font, _table_style, _ROW_H,
)
from tahmeed.ui.cashier.draft_submit import submit_draft_day_with_gates
from tahmeed.ui.cashier.register_delegates import _is_refund_float
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading

# S/N | Date | Entries | Mapped | Unmapped | TZS | USD | Kwacha | Refund | ⋯
_COL_DEFAULTS = [44, 110, 70, 70, 80, 130, 110, 110, 130, 44]
_COL_SNO = 0
_COL_DATE = 1
_COL_ENTRIES = 2
_COL_MAPPED = 3
_COL_UNMAPPED = 4
_COL_TZS = 5
_COL_USD = 6
_COL_ZMW = 7
_COL_REFUND = 8
_COL_ACTIONS = 9
_COL_KEY = "saved_days_table_v2"

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_GOLD = "#B18E5E"
_T1 = "#111827"
_T2 = "#6B7280"
_GREEN = "#15803D"
_GREEN_L = "#DCFCE7"
_SAVED_ROW = "#E8F4FD"
_SAVED_ROW_ALT = "#EFF6FF"
_ZMW_CODES = frozenset({"ZMW", "ZMB", "ZK"})

# Legacy aliases kept for older tests / callers.
SavedRow = Tuple[date, Transaction]


@dataclass(frozen=True)
class SavedDaySummary:
    """One Saved-tab row: aggregates for a single draft register day."""

    day: date
    entries_count: int
    mapped_count: int
    unmapped_count: int
    total_tzs: float
    total_usd: float
    total_zmw: float
    total_refund: float


def tx_register_day(tx: Transaction) -> Optional[date]:
    """Calendar day for a draft row (register / import primary)."""
    raw = getattr(tx, "import_primary_date", None) or getattr(tx, "date", None)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    return None


def flatten_draft_day_rows(
    day_batches: Sequence[Tuple[date, Sequence[Transaction]]],
) -> List[SavedRow]:
    """Newest day first; within a day keep API order. (Legacy flat-list helper.)"""
    out: List[SavedRow] = []
    for day, rows in day_batches:
        for tx in rows:
            out.append((day, tx))
    return out


def tx_is_mapped(tx: Transaction) -> bool:
    """True when both Item (or category) and Payee are present."""
    item = (getattr(tx, "item", None) or getattr(tx, "category_name", None) or "").strip()
    payee = (getattr(tx, "payee", None) or "").strip()
    return bool(item) and bool(payee)


def tx_is_refund_to_float(tx: Transaction) -> bool:
    if getattr(tx, "notes_flag", False):
        return True
    return _is_refund_float(getattr(tx, "ref_float", None) or "")


def tx_money_buckets(tx: Transaction) -> tuple[float, float, float]:
    """Return (tzs, usd, zmw) for day totals."""
    cur = (getattr(tx, "currency", None) or "TZS").upper()
    amt = float(getattr(tx, "amount", 0.0) or 0.0)
    amount_usd = getattr(tx, "amount_usd", None)
    if cur in _ZMW_CODES:
        usd = float(amount_usd or 0.0) if amount_usd is not None else 0.0
        return 0.0, usd, amt
    tzs, usd = tx.money_parts()
    return tzs, usd, 0.0


def summarize_draft_day(day: date, rows: Sequence[Transaction]) -> SavedDaySummary:
    """Build one Saved summary from draft transactions for *day*."""
    mapped = unmapped = 0
    tzs = usd = zmw = refund = 0.0
    for tx in rows:
        if tx_is_mapped(tx):
            mapped += 1
        else:
            unmapped += 1
        b_tzs, b_usd, b_zmw = tx_money_buckets(tx)
        tzs += b_tzs
        usd += b_usd
        zmw += b_zmw
        if tx_is_refund_to_float(tx):
            # Refund-to-float stays TZS-primary (register footer rule).
            refund += b_tzs if b_tzs else abs(float(getattr(tx, "amount", 0.0) or 0.0))
    return SavedDaySummary(
        day=day,
        entries_count=len(rows),
        mapped_count=mapped,
        unmapped_count=unmapped,
        total_tzs=tzs,
        total_usd=usd,
        total_zmw=zmw,
        total_refund=refund,
    )


def summarize_draft_day_batches(
    day_batches: Sequence[Tuple[date, Sequence[Transaction]]],
) -> List[SavedDaySummary]:
    """Newest day first — one summary per batch."""
    return [summarize_draft_day(day, rows) for day, rows in day_batches]


def filter_day_summaries(
    rows: Sequence[SavedDaySummary],
    query: str,
) -> List[SavedDaySummary]:
    """Case-insensitive match on date label / iso / money text."""
    q = (query or "").strip().lower()
    if not q:
        return list(rows)
    matched: List[SavedDaySummary] = []
    for s in rows:
        hay = " ".join([
            s.day.strftime("%d %b %Y"),
            s.day.isoformat(),
            str(s.entries_count),
            str(s.mapped_count),
            str(s.unmapped_count),
            f"{s.total_tzs:.0f}",
            f"{s.total_usd:.2f}",
            f"{s.total_zmw:.2f}",
            f"{s.total_refund:.0f}",
        ]).lower()
        if q in hay:
            matched.append(s)
    return matched


def filter_saved_rows(rows: Sequence[SavedRow], query: str) -> List[SavedRow]:
    """Legacy flat-row filter (kept for older tests)."""
    q = (query or "").strip().lower()
    if not q:
        return list(rows)
    matched: List[SavedRow] = []
    for day, tx in rows:
        hay = " ".join([
            day.strftime("%d %b %Y"),
            day.isoformat(),
            tx.description or "",
            tx.item or "",
            tx.truck_number or "",
            tx.payee or "",
            tx.memo or "",
        ]).lower()
        if q in hay:
            matched.append((day, tx))
    return matched


def format_tx_amount(tx: Transaction) -> str:
    tzs, usd = tx.money_parts()
    if tzs and usd:
        return f"TZS {tzs:,.0f} / USD {usd:,.2f}"
    if tzs:
        return f"TZS {tzs:,.0f}"
    if usd:
        return f"USD {usd:,.2f}"
    return "—"


def _fmt_tzs(n: float) -> str:
    return f"TZS {n:,.0f}" if n else "—"


def _fmt_usd(n: float) -> str:
    return f"USD {n:,.2f}" if n else "—"


def _fmt_zmw(n: float) -> str:
    return f"ZMW {n:,.2f}" if n else "—"


def _lbl(text: str = "", *, size: int = 13, weight: int = 400, color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI',sans-serif;background:transparent;border:none;"
    )
    return w


def _tool_btn(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(34)
    btn.setStyleSheet(
        f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
        "border-radius:4px;font-size:12px;font-weight:500;font-family:'Segoe UI';"
        "padding:0 14px;}"
        f"QPushButton:hover{{background:{_BG};}}"
        "QPushButton:disabled{color:#D1D5DB;}"
    )
    return btn


def _cell(text: object, *, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(align)
    item.setFont(_table_cell_font())
    return item


class SavedView(QWidget):
    """Day-summary inbox of saved drafts; open / submit / discard per day."""

    open_merged_date = Signal(object)  # date
    # date when a specific day changed; None = unknown / refresh lists only
    drafts_changed = Signal(object)
    drafts_submitted = Signal(int)

    def __init__(self, user: User, *, show_all_cashiers: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._user = user
        self._show_all = bool(show_all_cashiers)
        self._all_days: List[SavedDaySummary] = []
        self._visible: List[SavedDaySummary] = []
        self._filter = ""
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
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(_lbl("Saved", size=18, weight=700, color="#1B2B4B"))
        self._count_chip = QLabel("0")
        self._count_chip.setAlignment(Qt.AlignCenter)
        self._count_chip.setFixedHeight(22)
        self._count_chip.setMinimumWidth(28)
        self._count_chip.setStyleSheet(
            f"background:{_GREEN_L};color:{_GREEN};border-radius:11px;"
            "font-size:11px;font-weight:700;font-family:'Segoe UI';padding:0 8px;"
        )
        title_row.addWidget(self._count_chip)
        title_row.addStretch(1)
        title_vl.addLayout(title_row)
        scope = "all cashiers" if self._show_all else "your entries"
        title_vl.addWidget(_lbl(
            f"One row per saved day — not yet submitted ({scope})",
            size=11, color=_T2,
        ))
        hl.addLayout(title_vl, 1)

        self._btn_refresh = _tool_btn("Refresh")
        self._btn_refresh.clicked.connect(self.refresh)
        hl.addWidget(self._btn_refresh)
        root.addWidget(header)

        search_bar = QFrame()
        search_bar.setStyleSheet(f"background:{_WHITE};border-bottom:1px solid {_BORDER};")
        sl = QHBoxLayout(search_bar)
        sl.setContentsMargins(20, 10, 20, 10)
        sl.setSpacing(10)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search date, counts, totals…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(32)
        self._search.setStyleSheet(
            f"QLineEdit{{background:{_BG};border:1px solid {_BORDER};border-radius:6px;"
            "padding:0 12px;font-size:12px;font-family:'Segoe UI';color:#111827;}"
            f"QLineEdit:focus{{border:1px solid {_BLUE};background:{_WHITE};}}"
        )
        self._search.textChanged.connect(self._on_filter_changed)
        sl.addWidget(self._search, 1)
        self._hint = _lbl(
            "⋯ or right-click · Open / Submit / Discard · Double-click opens Merged",
            size=11, color=_T2,
        )
        self._hint.setWordWrap(True)
        sl.addWidget(self._hint, 1)
        root.addWidget(search_bar)

        body = QFrame()
        body.setStyleSheet(f"background:{_BG};")
        body_vl = QVBoxLayout(body)
        body_vl.setContentsMargins(16, 16, 16, 16)
        body_vl.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background:transparent;")

        empty = QFrame()
        empty.setObjectName("savedEmpty")
        empty.setStyleSheet(
            f"QFrame#savedEmpty{{background:{_WHITE};border:1px solid {_BORDER};"
            "border-radius:8px;}"
        )
        empty_vl = QVBoxLayout(empty)
        empty_vl.setAlignment(Qt.AlignCenter)
        empty_vl.setSpacing(8)
        empty_vl.addWidget(_lbl("No saved drafts", size=15, weight=600, color=_T1))
        empty_vl.addWidget(_lbl(
            "Entries you Save in the Daily Register appear here until you Submit.",
            size=12, color=_T2,
        ))
        self._stack.addWidget(empty)

        table_host = QFrame()
        table_host.setObjectName("savedTableHost")
        table_host.setStyleSheet(
            f"QFrame#savedTableHost{{background:{_WHITE};border:1px solid {_BORDER};"
            "border-radius:8px;}"
        )
        th_vl = QVBoxLayout(table_host)
        th_vl.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 10)
        self._table.setHorizontalHeaderLabels([
            "S/N", "Date", "Entries", "Mapped", "Unmapped",
            "TZS", "USD", "Kwacha", "Refund", "",
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setStyleSheet(_table_style())
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setShowGrid(True)
        self._table.setAlternatingRowColors(False)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionsMovable(False)
        hdr.setMinimumSectionSize(36)
        for col in range(self._table.columnCount()):
            self._table.setColumnWidth(col, _COL_DEFAULTS[col])
            if col in (_COL_SNO, _COL_ACTIONS):
                hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            else:
                hdr.setSectionResizeMode(col, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._table,
            _COL_KEY,
            _COL_DEFAULTS,
            auto_fit_if_unset=True,
        )
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)
        th_vl.addWidget(self._table)
        self._stack.addWidget(table_host)

        body_vl.addWidget(self._stack, 1)
        root.addWidget(body, 1)
        self.setStyleSheet(f"background:{_BG};")

    def refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load_rows())

    async def _load_rows(self) -> None:
        show_widget_loading(self, "Loading saved drafts…")
        try:
            cashier_id = None if self._show_all else self._user._id
            days = await get_draft_day_summaries(
                cashier_id,
                all_cashiers=self._show_all,
            )
            merged = self._show_all
            tasks = [
                get_draft_transactions(
                    d["date"],
                    None if merged else cashier_id,
                    merged=merged,
                )
                for d in days
                if isinstance(d.get("date"), date)
            ]
            batches_raw = await asyncio.gather(*tasks, return_exceptions=True)
            day_batches: List[Tuple[date, Sequence[Transaction]]] = []
            for summary, result in zip(
                [d for d in days if isinstance(d.get("date"), date)],
                batches_raw,
            ):
                if isinstance(result, Exception):
                    continue
                day_batches.append((summary["date"], result))
            self._all_days = summarize_draft_day_batches(day_batches)
            self._apply_filter()
        except Exception as exc:
            QMessageBox.critical(self, "Saved", f"Could not load saved drafts:\n{exc}")
        finally:
            hide_widget_loading(self)

    def _on_filter_changed(self, text: str) -> None:
        self._filter = text or ""
        self._apply_filter()

    def _apply_filter(self) -> None:
        self._visible = filter_day_summaries(self._all_days, self._filter)
        self._populate()

    def _populate(self) -> None:
        entries_total = sum(s.entries_count for s in self._visible)
        self._count_chip.setText(str(entries_total))
        self._count_chip.setVisible(entries_total > 0)
        if not self._visible:
            self._stack.setCurrentIndex(0)
            self._table.setRowCount(0)
            return
        self._stack.setCurrentIndex(1)
        self._table.setRowCount(len(self._visible))
        for i, s in enumerate(self._visible):
            self._table.setItem(
                i, _COL_SNO, _cell(str(i + 1), align=Qt.AlignCenter),
            )
            self._table.setItem(i, _COL_DATE, _cell(s.day.strftime("%d %b %Y")))
            self._table.setItem(
                i, _COL_ENTRIES, _cell(str(s.entries_count), align=Qt.AlignCenter),
            )
            self._table.setItem(
                i, _COL_MAPPED, _cell(str(s.mapped_count), align=Qt.AlignCenter),
            )
            unmapped = _cell(str(s.unmapped_count), align=Qt.AlignCenter)
            if s.unmapped_count:
                unmapped.setForeground(QColor(_GOLD))
            self._table.setItem(i, _COL_UNMAPPED, unmapped)
            self._table.setItem(i, _COL_TZS, _cell(_fmt_tzs(s.total_tzs)))
            self._table.setItem(i, _COL_USD, _cell(_fmt_usd(s.total_usd)))
            self._table.setItem(i, _COL_ZMW, _cell(_fmt_zmw(s.total_zmw)))
            refund = _cell(_fmt_tzs(s.total_refund))
            if s.total_refund:
                refund.setForeground(QColor("#EA580C"))
            self._table.setItem(i, _COL_REFUND, refund)
            for col in range(_COL_ACTIONS):
                it = self._table.item(i, col)
                if it is not None:
                    it.setData(Qt.UserRole, s.day)
            self._table.setCellWidget(i, _COL_ACTIONS, self._make_actions_btn(s))
            bg = _SAVED_ROW if i % 2 == 0 else _SAVED_ROW_ALT
            _finish_table_row(self._table, i, bg)
        self._autofit_columns()
        self._table.clearSelection()

    def _autofit_columns(self) -> None:
        """Size data columns to content so money totals are not ellipsized."""
        if self._table.rowCount() <= 0:
            return
        self._table._col_width_suspend = True  # type: ignore[attr-defined]
        try:
            for col in range(_COL_ACTIONS):
                if col == _COL_SNO:
                    self._table.setColumnWidth(col, _COL_DEFAULTS[_COL_SNO])
                    continue
                self._table.resizeColumnToContents(col)
                width = self._table.columnWidth(col) + 16
                floor = _COL_DEFAULTS[col] if col < len(_COL_DEFAULTS) else 48
                self._table.setColumnWidth(col, min(max(width, floor), 520))
            self._table.setColumnWidth(_COL_ACTIONS, _COL_DEFAULTS[_COL_ACTIONS])
            saver = getattr(self._table, "_col_width_save", None)
            if callable(saver):
                saver()
        finally:
            self._table._col_width_suspend = False  # type: ignore[attr-defined]

    def _make_actions_btn(self, summary: SavedDaySummary) -> QToolButton:
        btn = QToolButton()
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip("Open, Submit, or Discard this day")
        try:
            btn.setIcon(qta.icon("mdi.dots-vertical", color=_T2))
        except Exception:
            btn.setText("⋯")
        btn.setStyleSheet(
            "QToolButton{border:none;background:transparent;padding:2px;}"
            f"QToolButton:hover{{background:{_BG};border-radius:4px;}}"
        )
        day = summary.day
        btn.clicked.connect(lambda *_: self._show_day_actions_menu_for_btn(day, btn))
        return btn

    def _summary_for_day(self, day: date) -> Optional[SavedDaySummary]:
        for s in self._visible:
            if s.day == day:
                return s
        for s in self._all_days:
            if s.day == day:
                return s
        return None

    def _selected_day(self) -> Optional[date]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not rows:
            return None
        row = rows[0]
        if row < 0 or row >= len(self._visible):
            return None
        return self._visible[row].day

    def _on_row_double_clicked(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._visible):
            return
        self.open_merged_date.emit(self._visible[row].day)

    def _on_table_context_menu(self, pos) -> None:
        row = self._table.indexAt(pos).row()
        if row < 0:
            rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
            row = rows[0] if rows else -1
        if row < 0 or row >= len(self._visible):
            return
        self._table.selectRow(row)
        day = self._visible[row].day
        self._show_day_actions_menu(day, self._table.viewport().mapToGlobal(pos))

    def _show_day_actions_menu_for_btn(self, day: date, btn: QToolButton) -> None:
        self._show_day_actions_menu(day, btn.mapToGlobal(btn.rect().bottomLeft()))

    def _show_day_actions_menu(self, day: date, global_pos) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{_WHITE};border:1px solid {_BORDER};padding:4px;}}"
            f"QMenu::item{{padding:6px 18px;font-size:12px;font-family:'Segoe UI';color:{_T1};}}"
            "QMenu::item:selected{background:#EFF6FF;color:#1D4ED8;}"
            f"QMenu::separator{{height:1px;background:{_BORDER};margin:4px 8px;}}"
        )
        open_act = menu.addAction("Open Merged")
        submit_act = menu.addAction("Submit day")
        menu.addSeparator()
        discard_act = menu.addAction("Discard day")
        chosen = menu.exec(global_pos)
        if chosen is open_act:
            self.open_merged_date.emit(day)
        elif chosen is submit_act:
            self._confirm_submit_day(day)
        elif chosen is discard_act:
            self._confirm_discard_day(day)

    def _confirm_submit_day(self, day: date) -> None:
        label = day.strftime("%d %b %Y")
        scope = (
            "the whole day's transactions (all cashiers)"
            if self._show_all
            else "your draft entries for that day"
        )
        resp = QMessageBox.question(
            self, "Submit for Verify",
            f"Submit all draft entries for {label} to the Verify inbox?\n\n"
            f"This sends {scope}.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if resp != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_submit_day(day))

    async def _do_submit_day(self, day: date) -> None:
        show_widget_loading(self, f"Submitting {day.strftime('%d %b %Y')}…")
        try:
            n = await submit_draft_day_with_gates(self, day)
            if n is None:
                return
            QMessageBox.information(
                self, "Submitted",
                f"{n:,} entr{'y' if n == 1 else 'ies'} sent to Verify for "
                f"{day.strftime('%d %b %Y')}.",
            )
            self.drafts_submitted.emit(int(n or 0))
            self.drafts_changed.emit(day)
            await self._load_rows()
        finally:
            hide_widget_loading(self)

    def _confirm_discard_day(self, day: date) -> None:
        summary = self._summary_for_day(day)
        n = summary.entries_count if summary else 0
        label = day.strftime("%d %b %Y")
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
        asyncio.ensure_future(self._do_discard_day(day))

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
            await self._load_rows()
        except Exception as exc:
            QMessageBox.critical(self, "Discard Failed", str(exc))
