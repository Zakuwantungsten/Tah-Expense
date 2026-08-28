"""AccountantDashboard — Manage Suppliers tab.

Suppliers are categories flagged ``is_supplier``. They appear in the cashier
Item column and description maps so payments can target them, but those
payments are excluded from Master Expenses.
"""

from __future__ import annotations

import asyncio
from datetime import date as _date, datetime as _dt
from typing import List, Optional

import qtawesome as qta
from bson import ObjectId
from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from tahmeed.models.category import Category
from tahmeed.services.category_service import (
    create_category, delete_category, get_all_categories,
    toggle_category, update_category,
)
from tahmeed.ui.accountant.item_quick_report import ItemQuickReportView
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_BLUE_L = "#E8F4FD"
_NAVY = "#1B2B4B"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"
_HDR_BG = "#F1F5F9"
_GREEN = "#16A34A"
_GREEN_L = "#DCFCE7"
_RED = "#DC2626"
_RED_L = "#FEE2E2"
_STRIPE = "#F1F5F9"
_ROW_H = 32
_HDR_H = 28
_PAGE_SIZE = 100

_TABLE_SS = (
    f"QTableWidget {{"
    f"  background: {_WHITE}; gridline-color: {_BORDER};"
    f"  font-size: 11px; font-family:'Segoe UI';"
    f"  color: {_T1}; border: none;"
    f"}}"
    f"QTableWidget::item {{ padding: 2px 8px; border: none; }}"
    f"QTableWidget::item:selected {{ background: {_BLUE_L}; color: {_T1}; }}"
    f"QHeaderView::section {{"
    f"  background: {_HDR_BG}; color: {_T2};"
    f"  font-size: 10px; font-weight: 600; font-family:'Segoe UI';"
    f"  border: none; border-bottom: 1px solid {_BORDER};"
    f"  padding: 0 8px; min-height: {_HDR_H}px;"
    f"}}"
    f"QHeaderView::section:hover {{ background: #E2E8F0; }}"
    f"QScrollBar:vertical {{ background: {_BG}; width: 8px; margin: 0; }}"
    f"QScrollBar::handle:vertical {{ background: #D1D5DB; border-radius: 4px; min-height: 24px; }}"
    f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ width: 0; height: 0; }}"
)


def _lbl(text: str = "", size: int = 13, weight: int = 400, color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color: {color}; font-size: {size}px; font-weight: {weight};"
        " font-family:'Segoe UI'; background: transparent;"
    )
    return w


def _input_ss() -> str:
    return (
        f"QLineEdit {{"
        f"  border: 1px solid {_BORDER}; border-radius: 5px;"
        f"  background: {_WHITE}; color: {_T1}; font-size: 12px;"
        "  font-family:'Segoe UI'; padding: 4px 8px;"
        "  min-height: 32px; max-height: 32px; }}"
        f"QLineEdit:focus {{ border-color: {_BLUE}; }}"
    )


def _item_table_font(*, bold: bool = False) -> QFont:
    f = QFont("Segoe UI")
    f.setPixelSize(11)
    f.setBold(bold)
    return f


def _status_item(text: str, color: str, row_bg: str, bold: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setTextAlignment(Qt.AlignCenter)
    it.setForeground(QBrush(QColor(color)))
    it.setBackground(QBrush(QColor(row_bg)))
    it.setFont(_item_table_font(bold=bold))
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    return it


def _money(tzs: float, usd: float) -> str:
    parts = []
    if tzs:
        parts.append(f"TZS {tzs:,.0f}")
    if usd:
        parts.append(f"USD {usd:,.2f}")
    return "  ·  ".join(parts) if parts else "—"


class _SupplierDialog(QDialog):
    """Add / edit a supplier payment target."""

    def __init__(
        self,
        supplier: Optional[Category] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._supplier = supplier
        self.result_data: dict = {}
        self.setWindowTitle("Edit Supplier" if supplier else "Add Supplier")
        self.setFixedWidth(420)
        self.setStyleSheet(f"background: {_WHITE};")
        self._build()
        if supplier:
            self._name.setText(supplier.name)
            self._description.setText(supplier.description or "")

    def _build(self) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(0)

        vl.addWidget(_lbl(
            "Edit Supplier" if self._supplier else "Add Supplier",
            size=16, weight=700, color=_NAVY,
        ))
        vl.addSpacing(4)
        note = _lbl(
            "Suppliers appear in the table Item column and description maps. "
            "Payments to them stay out of Master Expenses.",
            size=12, color=_T2,
        )
        note.setWordWrap(True)
        vl.addWidget(note)
        vl.addSpacing(20)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(14)

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. ABC SPARES, LAKE OIL")
        self._name.setStyleSheet(_input_ss())
        form.addRow(_lbl("Supplier Name *", size=12, weight=500, color=_T2), self._name)

        self._description = QLineEdit()
        self._description.setPlaceholderText("Optional hint for cashiers")
        self._description.setStyleSheet(_input_ss())
        form.addRow(_lbl("Description hint", size=12, weight=500, color=_T2), self._description)

        vl.addLayout(form)
        vl.addSpacing(24)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setFixedHeight(34)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 13px; font-family:'Segoe UI'; padding: 0 18px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        save = QPushButton("Save")
        save.setFixedHeight(34)
        save.setCursor(Qt.PointingHandCursor)
        save.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600;"
            " font-family:'Segoe UI'; padding: 0 18px; }}"
            "QPushButton:hover { background: #005EA3; }"
        )
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

        self._name.setFocus()

    def _accept(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Required", "Supplier name is required.")
            return
        self.result_data = {
            "name": name.upper(),
            "description": self._description.text().strip(),
            "color": "#0077C5",
            "icon": "mdi.truck-delivery-outline",
            "requires_receipt": False,
            "requires_truck": False,
            "show_in_sidebar": False,
            "show_in_cashier_sidebar": False,
            "lock_description": False,
            "restrict_in_pdf": False,
            "restrict_in_excel": False,
            "is_supplier": True,
        }
        self.accept()


class ManageSuppliersWidget(QWidget):
    """Catalog of suppliers with Amount totals and QuickReport drill-down."""

    suppliers_changed = Signal()

    _COLS = [
        ("Name", 280, "left"),
        ("Description", 200, "left"),
        ("Status", 88, "center"),
        ("Amount", 160, "right"),
        ("Actions", 56, "center"),
    ]
    _COL_DEFAULTS = [280, 200, 88, 160, 56]
    _COL_AMOUNT = 3
    _COL_ACTIONS = 4

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: List[Category] = []
        self._visible: List[Category] = []
        self._show_inactive = False
        self._selected_id: Optional[ObjectId] = None
        self._total = 0
        self._loading = False
        self._scroll_loading = False
        self._usage_by_name: dict = {}
        self._report_item: Optional[Category] = None
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(350)
        self._search_debounce.timeout.connect(self._on_search_commit)
        self._build()
        asyncio.ensure_future(self._load_initial())

    def _build(self) -> None:
        self.setStyleSheet(f"background: {_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_catalog_page())
        self._stack.addWidget(self._build_report_shell())
        root.addWidget(self._stack, 1)

        self._loading_overlay = LoadingOverlay(self, "Loading suppliers…")

    def _build_catalog_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {_BG};")
        vl = QVBoxLayout(page)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        vl.addWidget(self._build_title_bar())
        vl.addWidget(self._build_filter_bar())
        vl.addWidget(self._build_table(), 1)
        vl.addWidget(self._build_footer())
        return page

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-bottom: 1px solid {_BORDER}; }}"
        )
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(20, 0, 20, 0)
        hl.setSpacing(12)

        try:
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(22, 22)
            icon_lbl.setPixmap(
                qta.icon("mdi.truck-delivery-outline", color=_BLUE).pixmap(22, 22)
            )
            icon_lbl.setStyleSheet("background: transparent;")
            hl.addWidget(icon_lbl)
        except Exception:
            pass

        hl.addWidget(_lbl("Suppliers", size=17, weight=700))
        self._count_lbl = _lbl("", size=12, color=_T2)
        hl.addWidget(self._count_lbl)
        hl.addStretch()

        hint = _lbl(
            "Paid via table Item column · excluded from Master Expenses",
            size=11, color=_TM,
        )
        hl.addWidget(hint)

        add_btn = QPushButton("  Add Supplier")
        add_btn.setFixedHeight(34)
        add_btn.setCursor(Qt.PointingHandCursor)
        try:
            add_btn.setIcon(qta.icon("mdi.plus", color="#FFF"))
            add_btn.setIconSize(QSize(16, 16))
        except Exception:
            pass
        add_btn.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600;"
            " font-family:'Segoe UI'; padding: 0 16px; }}"
            "QPushButton:hover { background: #005EA3; }"
        )
        add_btn.clicked.connect(self._on_add)
        hl.addWidget(add_btn)
        return bar

    def _build_filter_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(48)
        bar.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-bottom: 1px solid {_BORDER}; }}"
        )
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(20, 0, 20, 0)
        hl.setSpacing(10)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search suppliers…")
        self._search.setFixedHeight(32)
        self._search.setStyleSheet(_input_ss())
        self._search.textChanged.connect(lambda _: self._search_debounce.start())
        hl.addWidget(self._search, 1)

        inactive_btn = QPushButton("Show inactive")
        inactive_btn.setCheckable(True)
        inactive_btn.setFixedHeight(32)
        inactive_btn.setCursor(Qt.PointingHandCursor)
        inactive_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:checked {{ background: {_BG}; color: {_T1};"
            f" border-color: {_BLUE}; }}"
        )
        inactive_btn.toggled.connect(self._on_inactive_toggled)
        hl.addWidget(inactive_btn)
        return bar

    def _build_table(self) -> QFrame:
        host = QFrame()
        host.setStyleSheet("QFrame { background: transparent; border: none; }")
        vl = QVBoxLayout(host)
        vl.setContentsMargins(16, 12, 16, 0)
        vl.setSpacing(0)

        self._table = QTableWidget(0, len(self._COLS))
        self._table.setHorizontalHeaderLabels([c[0] for c in self._COLS])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setStyleSheet(_TABLE_SS)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self._table.verticalScrollBar().valueChanged.connect(self._on_scroll)

        hdr = self._table.horizontalHeader()
        hdr.setSectionsMovable(False)
        hdr.setStretchLastSection(False)
        for i, (_name, width, _align) in enumerate(self._COLS):
            self._table.setColumnWidth(i, width)
            hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._table, "manage_suppliers", self._COL_DEFAULTS,
        )
        vl.addWidget(self._table)
        return host

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setFixedHeight(40)
        footer.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-top: 1px solid {_BORDER}; }}"
        )
        pl = QHBoxLayout(footer)
        pl.setContentsMargins(20, 0, 20, 0)
        self._page_info = _lbl("—", size=12, color=_T2)
        pl.addWidget(self._page_info)
        pl.addStretch()
        return footer

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

        back_btn = QPushButton("← Suppliers")
        back_btn.setFixedHeight(30)
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
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

        self._report_kind_lbl = QLabel("Supplier QuickReport")
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
        content.setObjectName("supplierReportContent")
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content.setStyleSheet(
            f"#supplierReportContent {{ background: {_WHITE}; border: none; }}"
        )
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 16, 0, 0)
        cl.setSpacing(0)
        self._report_table = ItemQuickReportView()
        self._report_table.header_context_changed.connect(self._on_report_header_context)
        cl.addWidget(self._report_table, 1)
        bl.addWidget(content, 1)

        vl.addWidget(body, 1)
        return page

    def refresh(self) -> None:
        asyncio.ensure_future(self._load_initial())

    def _on_inactive_toggled(self, on: bool) -> None:
        self._show_inactive = on
        asyncio.ensure_future(self._load_initial())

    def _on_search_commit(self) -> None:
        asyncio.ensure_future(self._load_initial())

    def _on_scroll(self, _value: int) -> None:
        bar = self._table.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if bar.value() >= bar.maximum() - 24:
            asyncio.ensure_future(self._load_more())

    def _fill_if_needed(self) -> None:
        bar = self._table.verticalScrollBar()
        if (
            not self._loading
            and not self._scroll_loading
            and len(self._visible) < self._total
            and bar.maximum() <= 0
        ):
            asyncio.ensure_future(self._load_more())

    def _update_scroll_footer(self) -> None:
        loaded = len(self._visible)
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
        self._count_lbl.setText(
            f"{total:,} supplier{'s' if total != 1 else ''}"
            + (f"  ·  {loaded} loaded" if total > loaded else "")
        )

    async def _load_initial(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._loading_overlay.show_loading("Loading suppliers…")
        try:
            search = self._search.text().strip().lower()
            suppliers = await get_all_categories(
                include_inactive=self._show_inactive,
                is_supplier=True,
            )
            if search:
                suppliers = [
                    s for s in suppliers
                    if search in (s.name or "").lower()
                    or search in (s.description or "").lower()
                ]
            self._items = list(suppliers)
            self._visible = list(suppliers)
            self._total = len(suppliers)
            self._usage_by_name = {}
            await self._fetch_usage_for(self._visible)
            self._populate(reset=True)
            self._update_scroll_footer()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load suppliers:\n{exc}")
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()
            self._update_scroll_footer()

    async def _load_more(self) -> None:
        return

    async def _fetch_usage_for(self, items: List[Category]) -> None:
        names = [i.name for i in items if i.name]
        if not names:
            return
        from tahmeed.services.accountant_service import get_categories_usage_totals

        usage = await get_categories_usage_totals(names)
        self._usage_by_name.update(usage)

    def _populate(self, *, reset: bool) -> None:
        if reset:
            self._table.setRowCount(0)
        start = 0 if reset else self._table.rowCount()
        for offset, item in enumerate(self._visible[start:]):
            row = start + offset
            self._table.insertRow(row)
            row_bg = _WHITE if row % 2 == 0 else _STRIPE
            usage = self._usage_by_name.get((item.name or "").strip().lower(), {})
            cells = [
                (item.name or "", Qt.AlignLeft | Qt.AlignVCenter),
                (item.description or "—", Qt.AlignLeft | Qt.AlignVCenter),
            ]
            for col, (text, align) in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setTextAlignment(align)
                it.setBackground(QBrush(QColor(row_bg)))
                it.setFont(_item_table_font(bold=(col == 0)))
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._table.setItem(row, col, it)

            if item.active:
                self._table.setItem(
                    row, 2, _status_item("Active", _GREEN, row_bg, bold=True),
                )
            else:
                self._table.setItem(
                    row, 2, _status_item("Inactive", _RED, row_bg, bold=True),
                )

            amount = _money(float(usage.get("tzs") or 0), float(usage.get("usd") or 0))
            amt = QTableWidgetItem(amount)
            amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            amt.setBackground(QBrush(QColor(row_bg)))
            amt.setFont(_item_table_font())
            amt.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._table.setItem(row, self._COL_AMOUNT, amt)

            self._table.setCellWidget(row, self._COL_ACTIONS, self._actions_cell(item, row_bg))

            if self._selected_id and item._id == self._selected_id:
                self._table.selectRow(row)

    def _actions_cell(self, item: Category, row_bg: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background: {row_bg};")
        hl = QHBoxLayout(w)
        hl.setContentsMargins(4, 0, 4, 0)
        hl.setSpacing(0)
        btn = QPushButton("⋯")
        btn.setFixedSize(28, 24)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; border: 1px solid {_BORDER};"
            " border-radius: 4px; font-size: 14px; font-weight: 700; }"
            f"QPushButton:hover {{ background: {_BG}; border-color: {_BLUE}; }}"
        )
        btn.clicked.connect(lambda _=False, it=item: self._show_row_menu(it, btn))
        hl.addWidget(btn)
        hl.addStretch()
        return w

    def _show_row_menu(self, item: Category, anchor: QWidget) -> None:
        menu = QMenu(self)
        menu.addAction("Open report", lambda: self._open_report(item))
        menu.addAction("Edit…", lambda: self._on_edit(item))
        menu.addAction(
            "Deactivate" if item.active else "Activate",
            lambda: self._on_toggle(item),
        )
        menu.addSeparator()
        del_act = menu.addAction("Delete…", lambda: self._on_delete(item))
        del_act.setEnabled(True)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._visible):
            return
        self._show_row_menu(self._visible[row], self._table.viewport())

    def _on_row_double_clicked(self, row: int, col: int) -> None:
        if col == self._COL_ACTIONS:
            return
        if row < 0 or row >= len(self._visible):
            return
        self._open_report(self._visible[row])

    def _open_report(self, item: Category) -> None:
        self._report_item = item
        self._selected_id = item._id
        self._report_nav_lbl.setText(f"Supplier QuickReport  ·  {item.name}")
        self._report_item_lbl.setText(item.name)
        now = _dt.now()
        self._report_meta_lbl.setText(
            f"{now.strftime('%I:%M %p').lstrip('0')}  {now.strftime('%d-%m-%y')}"
        )
        self._on_report_header_context(str(_date.today().year), "All Transactions")
        self._stack.setCurrentIndex(1)
        self._report_table.load(item.name)

    def _on_report_header_context(self, year_label: str, scope_label: str) -> None:
        self._report_company_lbl.setText(f"TAHMEED COACH TZ LTD - {year_label}")
        self._report_scope_lbl.setText(scope_label)

    def _close_report(self) -> None:
        self._report_item = None
        self._report_table.clear()
        self._stack.setCurrentIndex(0)

    def _on_add(self) -> None:
        dlg = _SupplierDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_add(dlg.result_data))

    async def _do_add(self, data: dict) -> None:
        try:
            cat = await create_category(
                data["name"], data["color"],
                data["requires_receipt"], data["requires_truck"],
                data.get("description", ""),
                icon=data.get("icon", "mdi.truck-delivery-outline"),
                show_in_sidebar=False,
                show_in_cashier_sidebar=False,
                is_supplier=True,
            )
            self._selected_id = cat._id
            await self._load_initial()
            self.suppliers_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create supplier:\n{exc}")

    def _on_edit(self, item: Category) -> None:
        self._selected_id = item._id
        dlg = _SupplierDialog(supplier=item, parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_edit(item._id, dlg.result_data))

    async def _do_edit(self, item_id: ObjectId, data: dict) -> None:
        try:
            await update_category(item_id, **data)
            await self._load_initial()
            self.suppliers_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update supplier:\n{exc}")

    def _on_toggle(self, item: Category) -> None:
        self._selected_id = item._id
        going_inactive = item.active
        msg = (
            f"Deactivate \"{item.name}\"?\n\n"
            "Deactivated suppliers won't appear in the table Item column."
            if going_inactive else
            f"Activate \"{item.name}\"?\n\n"
            "The supplier will be available in the table Item column again."
        )
        if QMessageBox.question(self, "Confirm", msg) == QMessageBox.Yes:
            asyncio.ensure_future(self._do_toggle(item._id, not item.active))

    async def _do_toggle(self, item_id: ObjectId, active: bool) -> None:
        try:
            await toggle_category(item_id, active)
            await self._load_initial()
            self.suppliers_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update supplier:\n{exc}")

    def _on_delete(self, item: Category) -> None:
        reply = QMessageBox.warning(
            self, "Delete Supplier",
            f"Permanently delete \"{item.name}\"?\n\n"
            "Existing payments keep their data but this supplier won't appear "
            "in the table Item column or description maps.\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Yes:
            asyncio.ensure_future(self._do_delete(item._id))

    async def _do_delete(self, item_id: ObjectId) -> None:
        try:
            await delete_category(item_id)
            if self._selected_id == item_id:
                self._selected_id = None
            await self._load_initial()
            self.suppliers_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to delete supplier:\n{exc}")
