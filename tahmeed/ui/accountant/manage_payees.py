"""Payee Registry — QuickBooks PAYEE names for Day Issues mapping.

Supports add / activate / deactivate / delete and seeding from a QuickBooks
OTHERNAME IIF export (same list used by Expense Automation).

Each row shows lifetime verified spend (Amount). Double-click opens a
Payee QuickReport breakdown (same pattern as Manage Items / Suppliers).
"""

from __future__ import annotations

import asyncio
from datetime import date as _date, datetime as _dt
from typing import List, Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from tahmeed.services.settings_service import get_setting, set_setting
from tahmeed.ui.accountant.item_quick_report import ItemQuickReportView
from tahmeed.ui.accountant.master_ledger_table import _TABLE_SS as _MASTER_TABLE_SS
from tahmeed.ui.accountant.separate_expenses import (
    _finish_table_row, _make_table, _stripe_bg, _table_cell_font,
)
from tahmeed.ui.async_utils import pause_background_polls, schedule_call, schedule_coro
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.combo_styles import with_combo_popup
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

_WHITE   = "#FFFFFF"
_BG      = "#F4F6F8"
_BORDER  = "#E5E7EB"
_BLUE    = "#0077C5"
_BLUE_L  = "#E8F4FD"
_NAVY    = "#1B2B4B"
_GREEN   = "#16A34A"
_GREEN_L = "#DCFCE7"
_RED     = "#DC2626"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"

# Stretch Name so Currency / Status / Amount stay readable (Amount = lifetime total).
_COL_DEFAULTS = [52, 0, 80, 72, 160]
_COL_NAME = 1
_COL_STATUS = 3
_COL_AMOUNT = 4
_PAGE_SIZE = 100


def _money(tzs: float, usd: float) -> str:
    parts = []
    if tzs:
        parts.append(f"TZS {abs(tzs):,.0f}")
    if usd:
        parts.append(f"USD {abs(usd):,.2f}")
    return "  ·  ".join(parts) if parts else "—"


def _lbl(text: str = "", size: int = 13, weight: int = 400,
         color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI';background:transparent;"
    )
    return w


def _btn(text: str, icon: str = "", primary: bool = True,
         danger: bool = False, height: int = 34) -> QPushButton:
    b = QPushButton(f"  {text}" if icon else text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    if icon:
        try:
            b.setIcon(qta.icon(icon, color="#FFFFFF" if (primary or danger) else _T1))
            b.setIconSize(QSize(16, 16))
        except Exception:
            pass
    if danger:
        ss = (f"QPushButton{{background:{_RED};color:#FFF;border:none;border-radius:5px;"
              f"font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
              f"QPushButton:hover{{background:#B91C1C;}}"
              f"QPushButton:disabled{{background:#FCA5A5;}}")
    elif primary:
        ss = (f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
              f"font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
              f"QPushButton:hover{{background:#005EA3;}}"
              f"QPushButton:disabled{{background:#93C5FD;}}")
    else:
        ss = (f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
              f"border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 14px;}}"
              f"QPushButton:hover{{background:{_BG};}}"
              f"QPushButton:disabled{{color:{_TM};}}")
    b.setStyleSheet(ss)
    return b


def _cell(text: str, align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
          bg: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    item.setFont(_table_cell_font())
    if bg:
        item.setBackground(QBrush(QColor(bg)))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class _AddPayeeDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Payee")
        self.setMinimumWidth(360)
        self.setStyleSheet("background:#FFFFFF;")
        self.result_name: Optional[str] = None
        self.result_currency: str = "TZS"

        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(12)

        vl.addWidget(_lbl("Add Payee", size=15, weight=700))
        vl.addWidget(_lbl(
            "Used for Day Issues mapping and the register Payee column.",
            size=12, color=_T2,
        ))

        vl.addWidget(_lbl("Name *", size=12, color=_T2))
        self._inp = QLineEdit()
        self._inp.setPlaceholderText("e.g. SPARE PARTS")
        self._inp.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:13px;"
            "font-family:'Segoe UI';padding:0 8px;"
            "min-height:34px;max-height:34px;}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._inp.returnPressed.connect(self._accept)
        vl.addWidget(self._inp)

        vl.addWidget(_lbl("Currency", size=12, color=_T2))
        self._currency = QComboBox()
        self._currency.addItems(["TZS", "USD"])
        self._currency.setCurrentText("TZS")
        self._currency.setFixedHeight(34)
        self._currency.setStyleSheet(with_combo_popup(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:13px;"
            "font-family:'Segoe UI';padding:0 8px;}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
            "QComboBox::drop-down{border:none;width:22px;}"
        ))
        vl.addWidget(self._currency)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = _btn("Cancel", primary=False, height=32)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = _btn("Add", primary=True, height=32)
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

    def _accept(self) -> None:
        val = " ".join(self._inp.text().upper().split())
        if not val:
            QMessageBox.warning(self, "Validation", "Name is required.")
            return
        self.result_name = val
        self.result_currency = (self._currency.currentText() or "TZS").strip().upper()
        self._inp.setText(val)
        self.accept()


class ManagePayeesWidget(QWidget):
    """Manage QuickBooks payee names (CRUD + IIF seed + verified totals)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: List[dict] = []
        self._total = 0
        self._loading = False
        self._scroll_loading = False
        self._usage_by_name: dict = {}
        self._report_payee: Optional[str] = None
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(350)
        self._search_debounce.timeout.connect(self._on_search_commit)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_catalog_page())
        self._stack.addWidget(self._build_report_shell())
        root.addWidget(self._stack, 1)

        self._loading_overlay = LoadingOverlay(self, "Loading…")

    def _build_catalog_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {_BG};")
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.setSpacing(10)
        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(qta.icon("mdi.account-cash-outline", color=_BLUE).pixmap(24, 24))
            icon_lbl.setFixedSize(24, 24)
            icon_lbl.setStyleSheet("background:transparent;")
            hdr.addWidget(icon_lbl)
        except Exception:
            pass
        hdr.addWidget(_lbl("Payees", size=18, weight=700))

        self._count_chip = QLabel("—")
        self._count_chip.setStyleSheet(
            f"background:{_BLUE_L};color:{_BLUE};font-size:11px;font-weight:700;"
            "border-radius:10px;padding:2px 10px;font-family:'Segoe UI';"
        )
        hdr.addWidget(self._count_chip)
        hdr.addStretch()

        import_btn = _btn("Import IIF…", primary=False, height=32)
        try:
            import_btn.setIcon(qta.icon("mdi.file-upload-outline", color=_T1))
            import_btn.setIconSize(QSize(15, 15))
        except Exception:
            pass
        import_btn.setToolTip(
            "Seed from a QuickBooks Payee List (.IIF OTHERNAME export)."
        )
        import_btn.clicked.connect(self._on_import_iif)
        hdr.addWidget(import_btn)

        add_btn = _btn("+ Add Payee", primary=True, height=32)
        add_btn.clicked.connect(self._add_payee)
        hdr.addWidget(add_btn)
        root.addLayout(hdr)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name…")
        self._search.setFixedHeight(32)
        self._search.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 8px;}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._search.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search, 1)

        self._filter_cb = QComboBox()
        self._filter_cb.addItems(["All", "Active only", "Inactive only"])
        self._filter_cb.setFixedHeight(32)
        self._filter_cb.setStyleSheet(with_combo_popup(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 8px;min-width:120px;}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
            "QComboBox::drop-down{border:none;width:20px;}"
        ))
        self._filter_cb.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_cb)

        toolbar.addStretch()

        self._restrict_btn = QPushButton("  Restrict payees: Off")
        self._restrict_btn.setFixedHeight(32)
        self._restrict_btn.setCheckable(True)
        self._restrict_btn.setCursor(Qt.PointingHandCursor)
        self._restrict_btn.setToolTip(
            "When on, typing a Payee that is not in the list opens a QuickBooks-style\n"
            "dialog: Cancel (clear the cell) or Add Payee (create it).\n"
            "Saving still requires a known payee."
        )
        try:
            self._restrict_btn.setIcon(qta.icon("mdi.lock-outline", color=_T2))
            self._restrict_btn.setIconSize(QSize(15, 15))
        except Exception:
            pass
        self._restrict_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }"
            f"QPushButton:checked {{ background: {_GREEN_L}; color: {_GREEN};"
            f" border-color: {_GREEN}; }}"
            f"QPushButton:hover:!checked {{ background: {_BG}; }}"
        )
        self._restrict_btn.toggled.connect(self._on_restrict_toggled)
        toolbar.addWidget(self._restrict_btn)
        root.addLayout(toolbar)

        self._table_host = QFrame()
        self._table_host.setStyleSheet("QFrame { background: transparent; border: none; }")
        table_vl = QVBoxLayout(self._table_host)
        table_vl.setContentsMargins(0, 0, 0, 0)
        table_vl.setSpacing(0)

        self._table = _make_table(["#", "Name", "Currency", "Status", "Amount"])
        self._table.setStyleSheet(_MASTER_TABLE_SS)
        self._table.setShowGrid(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self._table.verticalScrollBar().valueChanged.connect(self._on_scroll)

        hdr_view = self._table.horizontalHeader()
        hdr_view.setSectionsMovable(False)
        hdr_view.setStretchLastSection(False)
        hdr_view.setMinimumSectionSize(48)
        for i, width in enumerate(_COL_DEFAULTS):
            if width:
                self._table.setColumnWidth(i, width)
            if i == _COL_NAME:
                hdr_view.setSectionResizeMode(i, QHeaderView.Stretch)
            else:
                hdr_view.setSectionResizeMode(i, QHeaderView.Interactive)
        # Stretch Name; autofit once so Amount (total) is not crushed by Status/Currency.
        bind_column_width_persistence(
            self._table,
            "manage_payees_stretch_name",
            _COL_DEFAULTS,
            stretch_columns=(_COL_NAME,),
            auto_fit_if_unset=True,
        )
        table_vl.addWidget(self._table)
        root.addWidget(self._table_host, 1)

        # Status bar — scroll-load chrome (same as Description Maps / Items).
        status_bar = QFrame()
        status_bar.setFixedHeight(44)
        status_bar.setStyleSheet(
            f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};border-radius:6px;}}"
        )
        sl = QHBoxLayout(status_bar)
        sl.setContentsMargins(12, 0, 12, 0)
        sl.setSpacing(10)
        self._page_info = _lbl("—", size=12, color=_T2)
        sl.addWidget(self._page_info, 1)
        tip = _lbl(
            "Double-click for verified spend  ·  Import IIF to seed names",
            size=11, color=_TM,
        )
        sl.addWidget(tip)
        root.addWidget(status_bar)
        return page

    def _build_report_shell(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {_BG};")
        vl = QVBoxLayout(page)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        nav = QFrame()
        nav.setFixedHeight(52)
        nav.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-bottom: 1px solid {_BORDER}; }}"
        )
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(16, 0, 16, 0)
        nl.setSpacing(12)

        back_btn = QPushButton("← Payees")
        back_btn.setFixedHeight(30)
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
        back_btn.clicked.connect(self._close_report)
        nl.addWidget(back_btn)

        self._report_nav_lbl = _lbl("", size=13, weight=600, color=_NAVY)
        nl.addWidget(self._report_nav_lbl)
        nl.addStretch()
        vl.addWidget(nav)

        body = QFrame()
        body.setStyleSheet(f"QFrame {{ background: {_WHITE}; border: none; }}")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 20, 24, 16)
        bl.setSpacing(4)

        self._report_meta_lbl = QLabel("")
        self._report_meta_lbl.setStyleSheet(
            f"color: {_T2}; font-size: 11px;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        bl.addWidget(self._report_meta_lbl)

        self._report_company_lbl = QLabel("TAHMEED COACH TZ LTD")
        self._report_company_lbl.setAlignment(Qt.AlignCenter)
        self._report_company_lbl.setStyleSheet(
            f"color: {_T1}; font-size: 15px; font-weight: 700;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        bl.addWidget(self._report_company_lbl)

        self._report_kind_lbl = QLabel("Payee QuickReport")
        self._report_kind_lbl.setAlignment(Qt.AlignCenter)
        self._report_kind_lbl.setStyleSheet(
            f"color: {_T1}; font-size: 13px; font-weight: 600;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        bl.addWidget(self._report_kind_lbl)

        self._report_scope_lbl = QLabel("All Transactions")
        self._report_scope_lbl.setAlignment(Qt.AlignCenter)
        self._report_scope_lbl.setStyleSheet(
            f"color: {_T2}; font-size: 12px;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        bl.addWidget(self._report_scope_lbl)

        self._report_item_lbl = QLabel("")
        self._report_item_lbl.setAlignment(Qt.AlignCenter)
        self._report_item_lbl.setStyleSheet(
            f"color: {_NAVY}; font-size: 14px; font-weight: 700;"
            " font-family:'Segoe UI'; background: transparent;"
            " margin-top: 8px;"
        )
        bl.addWidget(self._report_item_lbl)

        content = QFrame()
        content.setObjectName("payeeReportContent")
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content.setStyleSheet(
            f"#payeeReportContent {{ background: {_WHITE}; border: none; }}"
        )
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 16, 0, 0)
        cl.setSpacing(0)
        self._report_table = ItemQuickReportView(entity="payee")
        self._report_table.header_context_changed.connect(self._on_report_header_context)
        cl.addWidget(self._report_table, 1)
        bl.addWidget(content, 1)

        vl.addWidget(body, 1)
        return page

    def refresh(self) -> None:
        schedule_coro(self._load())

    def _on_restrict_toggled(self, on: bool) -> None:
        self._restrict_btn.setText(
            "  Restrict payees: On" if on else "  Restrict payees: Off"
        )
        schedule_coro(self._save_restrict_setting(on))

    async def _load_restrict_setting(self) -> None:
        try:
            restrict_on = bool(await get_setting("restrict_payees"))
        except Exception:
            restrict_on = False
        self._restrict_btn.blockSignals(True)
        self._restrict_btn.setChecked(restrict_on)
        self._restrict_btn.setText(
            "  Restrict payees: On" if restrict_on else "  Restrict payees: Off"
        )
        self._restrict_btn.blockSignals(False)

    async def _save_restrict_setting(self, on: bool) -> None:
        try:
            await set_setting("restrict_payees", on)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save setting:\n{exc}")

    def _active_filter(self) -> str:
        idx = self._filter_cb.currentIndex()
        if idx == 1:
            return "active"
        if idx == 2:
            return "inactive"
        return "all"

    def _on_search_changed(self) -> None:
        self._search_debounce.start()

    def _on_search_commit(self) -> None:
        schedule_coro(self._load())

    def _on_filter_changed(self) -> None:
        schedule_coro(self._load())

    def _on_scroll(self, value: int) -> None:
        bar = self._table.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if value >= bar.maximum() - 24:
            schedule_coro(self._load_more())

    def _fill_if_needed(self) -> None:
        bar = self._table.verticalScrollBar()
        if (
            not self._loading
            and not self._scroll_loading
            and len(self._rows) < self._total
            and bar.maximum() <= 0
        ):
            schedule_coro(self._load_more())

    def _update_scroll_footer(self) -> None:
        loaded = len(self._rows)
        total = self._total
        if self._loading or self._scroll_loading:
            suffix = "  ·  Loading…"
        elif loaded >= total and total:
            suffix = ""
        elif total:
            suffix = "  ·  Scroll for more"
        else:
            suffix = ""
        self._page_info.setText(f"Showing {loaded:,} of {total:,}{suffix}")
        self._count_chip.setText(
            f"{total:,} payee{'s' if total != 1 else ''}"
            + (f"  ·  {loaded} loaded" if total > loaded else "")
        )

    async def _load(self) -> None:
        """Full refresh from the top (search / filter / mutate / import)."""
        if self._loading:
            return
        self._loading = True
        self._loading_overlay.show_loading("Loading payees…")
        self._update_scroll_footer()
        try:
            from tahmeed.services.payee_service import list_payees, count_payees

            search = self._search.text().strip()
            active_filter = self._active_filter()
            rows, total = await asyncio.gather(
                list_payees(
                    search=search,
                    active_filter=active_filter,
                    limit=_PAGE_SIZE,
                    skip=0,
                ),
                count_payees(search=search, active_filter=active_filter),
            )
            self._rows = rows
            self._total = total
            self._usage_by_name = {}
            await self._fetch_usage_for(rows)
            self._populate_table(reset=True)
            self._update_scroll_footer()
            self._fill_if_needed()
            await self._load_restrict_setting()
        except Exception as exc:
            # Defer modal so badge polls cannot wake into this task (Py 3.14).
            schedule_call(
                lambda e=exc: QMessageBox.critical(
                    self, "Error", f"Could not load payees:\n{e}",
                )
            )
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()
            self._update_scroll_footer()

    async def _load_more(self) -> None:
        if self._scroll_loading or self._loading:
            return
        if len(self._rows) >= self._total:
            return
        self._scroll_loading = True
        self._update_scroll_footer()
        try:
            from tahmeed.services.payee_service import list_payees

            search = self._search.text().strip()
            active_filter = self._active_filter()
            skip = len(self._rows)
            rows = await list_payees(
                search=search,
                active_filter=active_filter,
                limit=_PAGE_SIZE,
                skip=skip,
            )
            if not rows:
                return
            self._rows.extend(rows)
            await self._fetch_usage_for(rows)
            self._populate_table(reset=False, new_rows=rows, start_row=skip)
            self._fill_if_needed()
        except Exception as exc:
            schedule_call(
                lambda e=exc: QMessageBox.critical(
                    self, "Error", f"Could not load more payees:\n{e}",
                )
            )
        finally:
            self._scroll_loading = False
            self._update_scroll_footer()

    async def _fetch_usage_for(self, rows: List[dict]) -> None:
        names = [str(r.get("name") or "").strip() for r in rows if r.get("name")]
        if not names:
            return
        from tahmeed.services.accountant_service import get_payees_usage_totals

        try:
            usage = await get_payees_usage_totals(names)
        except Exception:
            return
        self._usage_by_name.update(usage)

    def _populate_table(
        self,
        *,
        reset: bool = True,
        new_rows: Optional[List[dict]] = None,
        start_row: int = 0,
    ) -> None:
        if reset:
            rows = self._rows
            start_row = 0
            self._table.setRowCount(0)
        else:
            rows = new_rows or []

        for i, row in enumerate(rows):
            idx = start_row + i
            self._table.insertRow(idx)
            active = bool(row.get("active", True))
            row_bg = _stripe_bg(idx)
            name = str(row.get("name") or "")
            usage = self._usage_by_name.get(name.strip().lower(), {})
            self._table.setItem(
                idx, 0,
                _cell(str(idx + 1), Qt.AlignCenter | Qt.AlignVCenter, bg=row_bg),
            )
            self._table.setItem(idx, _COL_NAME, _cell(name, bg=row_bg))
            self._table.setItem(
                idx, 2,
                _cell(
                    row.get("currency", "") or "—",
                    Qt.AlignCenter | Qt.AlignVCenter,
                    bg=row_bg,
                ),
            )
            self._table.setItem(
                idx, _COL_STATUS,
                _cell(
                    "Active" if active else "Inactive",
                    Qt.AlignCenter | Qt.AlignVCenter,
                    bg=row_bg,
                ),
            )
            amount = _money(float(usage.get("tzs") or 0), float(usage.get("usd") or 0))
            self._table.setItem(
                idx, _COL_AMOUNT,
                _cell(amount, Qt.AlignRight | Qt.AlignVCenter, bg=row_bg),
            )
            _finish_table_row(self._table, idx, row_bg)

    def _on_row_double_clicked(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._rows):
            return
        name = str(self._rows[row].get("name") or "").strip()
        if name:
            self._open_report(name)

    def _open_report(self, payee_name: str) -> None:
        name = (payee_name or "").strip()
        if not name:
            return
        self._report_payee = name
        self._report_nav_lbl.setText(f"Payee QuickReport  ·  {name}")
        self._report_item_lbl.setText(name)
        now = _dt.now()
        self._report_meta_lbl.setText(
            f"{now.strftime('%I:%M %p').lstrip('0')}  {now.strftime('%d-%m-%y')}"
        )
        self._on_report_header_context(str(_date.today().year), "All Transactions")
        self._stack.setCurrentIndex(1)
        self._report_table.load(name)

    def _on_report_header_context(self, year_label: str, scope_label: str) -> None:
        self._report_company_lbl.setText(f"TAHMEED COACH TZ LTD - {year_label}")
        self._report_scope_lbl.setText(scope_label)

    def _close_report(self) -> None:
        self._report_payee = None
        self._report_table.clear()
        self._stack.setCurrentIndex(0)

    def _context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._rows):
            return
        entry = self._rows[row]
        active = entry.get("active", True)
        name = entry.get("name", "")
        currency = entry.get("currency") or "TZS"

        menu = QMenu(self)
        open_act = menu.addAction("Open report")
        menu.addSeparator()
        toggle_act = menu.addAction("Deactivate" if active else "Activate")
        menu.addSeparator()
        delete_act = menu.addAction("Delete")
        try:
            delete_act.setIcon(qta.icon("mdi.delete-outline", color=_RED))
        except Exception:
            pass

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == open_act:
            self._open_report(name)
        elif chosen == toggle_act:
            schedule_coro(self._toggle_active(name, not active, currency=currency))
        elif chosen == delete_act:
            self._confirm_delete(name)

    async def _toggle_active(
        self, name: str, active: bool, *, currency: str = "TZS",
    ) -> None:
        from tahmeed.services.payee_service import set_payee_active

        try:
            await set_payee_active(name, active, currency=currency)
        except Exception as exc:
            schedule_call(
                lambda e=exc: QMessageBox.critical(self, "Error", str(e))
            )
            return
        await self._load()

    def _confirm_delete(self, name: str) -> None:
        if QMessageBox.question(
            self, "Delete Payee",
            f'Delete "{name}" permanently?\nThis cannot be undone.',
        ) != QMessageBox.Yes:
            return
        schedule_coro(self._do_delete(name))

    async def _do_delete(self, name: str) -> None:
        from tahmeed.services.payee_service import remove_payee

        try:
            await remove_payee(name)
        except Exception as exc:
            schedule_call(
                lambda e=exc: QMessageBox.critical(self, "Error", str(e))
            )
            return
        await self._load()

    def _add_payee(self) -> None:
        dlg = _AddPayeeDialog(parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.result_name:
            schedule_coro(self._do_add(dlg.result_name, dlg.result_currency))

    async def _do_add(self, name: str, currency: str = "TZS") -> None:
        from tahmeed.services.payee_service import add_payee

        try:
            await add_payee(name, currency=currency or "TZS")
        except Exception as exc:
            schedule_call(
                lambda e=exc: QMessageBox.critical(self, "Error", str(e))
            )
            return
        await self._load()

    def _on_import_iif(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Payee List (IIF)",
            "",
            "QuickBooks IIF (*.IIF *.iif);;All files (*)",
        )
        if not path:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Import Payee List")
        box.setIcon(QMessageBox.Question)
        box.setText(
            "Replace the entire payee list with names from this IIF file?\n\n"
            "Choose Yes to wipe existing payees and load the file.\n"
            "Choose No to merge (add/update names, keep others)."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Yes)
        choice = box.exec()
        if choice == QMessageBox.Cancel:
            return
        replace = choice == QMessageBox.Yes
        schedule_coro(self._do_import_iif(path, replace_existing=replace))

    async def _do_import_iif(self, path: str, *, replace_existing: bool) -> None:
        """Import payees while badge polls are paused (avoids Py 3.14 task clash)."""
        from tahmeed.services.payee_service import import_payee_list

        with pause_background_polls(self):
            self._loading_overlay.show_loading("Importing payees…")
            try:
                result = await import_payee_list(
                    path, replace_existing=replace_existing,
                )
            except Exception as exc:
                schedule_call(
                    lambda e=exc: QMessageBox.critical(
                        self,
                        "Import Error",
                        f"Could not import payees:\n{e}",
                    )
                )
                return
            finally:
                self._loading_overlay.hide_loading()

            imported = int(result.get("imported") or 0)
            updated = int(result.get("updated") or 0)
            removed = int(result.get("removed") or 0)
            if replace_existing:
                msg = (
                    f"Imported {imported:,} payee(s) from the IIF file.\n"
                    f"Removed {removed:,} previous payee(s)."
                )
            else:
                msg = (
                    f"Added {imported:,} new payee(s).\n"
                    f"Updated {updated:,} existing payee(s)."
                )
            # Show after this task yields — never block with a modal mid-task.
            schedule_call(
                lambda m=msg: QMessageBox.information(
                    self, "Payees Imported", m,
                )
            )
            await self._load()
