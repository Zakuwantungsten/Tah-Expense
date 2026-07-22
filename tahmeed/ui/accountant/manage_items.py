"""AccountantDashboard — Manage Items (with optional sub-items) tab.

Two-panel layout:
  Left  — items table (add / edit / deactivate / delete)
  Right — sub-items panel for the selected item (add / edit / delete)

Sub-items are optional — any item can have zero, one, or many sub-items.
Each sub-item is a description-based filter that carves the parent item's
transaction table into a named sub-view (e.g. Mileage → "Dar to Congo").
"""

from __future__ import annotations

import asyncio
import re
from typing import List, Optional
from bson import ObjectId

import qtawesome as qta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QPushButton, QMessageBox, QAbstractItemView,
    QDialog, QFormLayout, QCheckBox, QColorDialog,
    QSplitter, QStackedWidget, QSizePolicy,
    QScrollArea, QGridLayout, QToolButton, QFileDialog,
)
from PySide6.QtCore import Qt, QSize, Signal, QTimer
from PySide6.QtGui import QColor, QBrush, QFont

from tahmeed.models.category import Category
from tahmeed.models.sub_table import SubTable
from tahmeed.services.category_service import (
    list_categories, count_categories,
    create_category, update_category,
    toggle_category, delete_category, item_key,
)
from tahmeed.services.subtable_service import (
    get_subtables, create_subtable, update_subtable, delete_subtable,
)
from tahmeed.services.settings_service import get_setting, set_setting
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

# ── Design tokens ──────────────────────────────────────────────────────────────

_WHITE   = "#FFFFFF"
_BG      = "#F4F6F8"
_BORDER  = "#E5E7EB"
_BLUE    = "#0077C5"
_NAVY    = "#1B2B4B"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"
_HDR_BG  = "#F1F5F9"
_GREEN   = "#16A34A"
_GREEN_L = "#DCFCE7"
_RED     = "#DC2626"
_RED_L   = "#FEE2E2"
_AMBER   = "#D97706"
_AMBER_L = "#FEF3C7"
_BLUE_L  = "#E8F4FD"   # row selection highlight (matches SM Burhani Bonds)
_STRIPE  = "#F1F5F9"   # subtle slate zebra stripe (matches SM Burhani Bonds)
_ROW_H   = 32
_HDR_H   = 28
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


def _item_table_font(*, bold: bool = False, italic: bool = False) -> QFont:
    """Uniform 11px cell font — matches the Description column (CSS px, not pt)."""
    f = QFont("Segoe UI")
    f.setPixelSize(11)
    f.setBold(bold)
    f.setItalic(italic)
    return f


def _item_key(item_name: str) -> str:
    """Return the sidebar / sub-table parent_key for an item name."""
    return item_key(item_name)


# Curated qtawesome mdi.* glyphs offered in the icon picker. Line-style icons
# chosen to match the look already used across the sidebar.
ICON_CHOICES: list[str] = [
    "mdi.tag-outline", "mdi.tag-multiple-outline", "mdi.label-outline",
    "mdi.road-variant", "mdi.truck", "mdi.truck-delivery", "mdi.truck-trailer",
    "mdi.truck-fast", "mdi.truck-check", "mdi.car", "mdi.bus", "mdi.tanker-truck",
    "mdi.gas-station", "mdi.gas-station-outline", "mdi.fuel", "mdi.water-pump",
    "mdi.oil", "mdi.barrel",
    "mdi.parking", "mdi.boom-gate", "mdi.boom-gate-outline", "mdi.sign-direction",
    "mdi.map", "mdi.map-marker", "mdi.map-marker-path", "mdi.routes", "mdi.earth",
    "mdi.cash", "mdi.cash-multiple", "mdi.account-cash", "mdi.cash-register",
    "mdi.credit-card-outline", "mdi.bank-outline", "mdi.wallet-outline",
    "mdi.currency-usd", "mdi.receipt", "mdi.calculator-variant",
    "mdi.file-document-outline", "mdi.file-table-outline", "mdi.clipboard-text-outline",
    "mdi.clipboard-list-outline", "mdi.book-open-outline", "mdi.notebook-outline",
    "mdi.card-account-details-outline", "mdi.certificate", "mdi.shield-account",
    "mdi.shield-check-outline", "mdi.scale", "mdi.scale-balance", "mdi.weight",
    "mdi.city-variant", "mdi.office-building", "mdi.warehouse", "mdi.factory",
    "mdi.hospital-box", "mdi.medical-bag", "mdi.leaf", "mdi.radioactive",
    "mdi.link-variant", "mdi.lock-outline", "mdi.shield-outline",
    "mdi.account-tie", "mdi.account-group-outline", "mdi.account-hard-hat",
    "mdi.satellite-variant", "mdi.devices", "mdi.cellphone", "mdi.cog-outline",
    "mdi.wrench-outline", "mdi.toolbox-outline", "mdi.hammer-wrench",
    "mdi.package-variant-closed", "mdi.dolly", "mdi.forklift", "mdi.crane",
    "mdi.anchor", "mdi.ferry", "mdi.airplane", "mdi.train", "mdi.bridge",
    "mdi.highway", "mdi.toll", "mdi.car-brake-alert", "mdi.shield-car",
    "mdi.flag-outline", "mdi.star-outline", "mdi.fire", "mdi.water-outline",
    "mdi.lightning-bolt-outline", "mdi.basket-outline", "mdi.store-outline",
    "mdi.chart-line", "mdi.chart-bar", "mdi.percent-outline", "mdi.ticket-outline",
]


# ── Icon picker dialog ──────────────────────────────────────────────────────────

class _IconPickerDialog(QDialog):
    """Grid of mdi.* icons with a live name filter. Click an icon to pick it."""

    def __init__(self, current: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.selected: str = current or ""
        self._buttons: List[tuple] = []   # (QToolButton, name)
        self.setWindowTitle("Choose Sidebar Icon")
        self.setFixedSize(520, 460)
        self.setStyleSheet(f"background: {_WHITE};")
        self._build(current)

    def _build(self, current: str) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 20)
        vl.setSpacing(12)

        vl.addWidget(_lbl("Choose Sidebar Icon", size=16, weight=700, color=_NAVY))

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter icons… (e.g. truck, fuel, cash)")
        self._search.setStyleSheet(_input_ss())
        self._search.textChanged.connect(self._apply_filter)
        vl.addWidget(self._search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")
        grid_host = QWidget()
        grid_host.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)

        cols = 8
        for i, name in enumerate(ICON_CHOICES):
            btn = QToolButton()
            btn.setFixedSize(50, 50)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(name.replace("mdi.", ""))
            try:
                btn.setIcon(qta.icon(name, color=_T1))
                btn.setIconSize(QSize(22, 22))
            except Exception:
                btn.setText("?")
            btn.setChecked(name == current)
            btn.setStyleSheet(
                f"QToolButton {{ background: {_WHITE}; border: 1px solid {_BORDER};"
                " border-radius: 6px; }}"
                f"QToolButton:hover {{ background: {_BG}; border-color: {_BLUE}; }}"
                f"QToolButton:checked {{ background: #EFF6FF; border: 2px solid {_BLUE}; }}"
            )
            btn.clicked.connect(lambda _=False, n=name: self._on_pick(n))
            self._grid.addWidget(btn, i // cols, i % cols)
            self._buttons.append((btn, name))

        scroll.setWidget(grid_host)
        vl.addWidget(scroll, 1)

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
        vl.addLayout(btn_row)

    def _apply_filter(self, text: str) -> None:
        q = text.strip().lower()
        for btn, name in self._buttons:
            btn.setVisible(not q or q in name.lower())

    def _on_pick(self, name: str) -> None:
        self.selected = name
        for btn, n in self._buttons:
            btn.setChecked(n == name)
        self.accept()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _lbl(text: str = "", size: int = 13, weight: int = 400, color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color: {color}; font-size: {size}px; font-weight: {weight};"
        " font-family:'Segoe UI'; background: transparent;"
    )
    return w


def _input_ss() -> str:
    return (
        f"QLineEdit, QTextEdit {{"
        f"  border: 1px solid {_BORDER}; border-radius: 5px;"
        f"  background: {_WHITE}; color: {_T1}; font-size: 12px;"
        "  font-family:'Segoe UI'; padding: 4px 8px; }}"
        f"QLineEdit {{ min-height: 32px; max-height: 32px; }}"
        f"QLineEdit:focus, QTextEdit:focus {{ border-color: {_BLUE}; }}"
    )


def _status_item(text: str, color: str, row_bg: str, bold: bool = False) -> QTableWidgetItem:
    """Flat, centered status text cell (matches the Description column size)."""
    it = QTableWidgetItem(text)
    it.setTextAlignment(Qt.AlignCenter)
    it.setForeground(QBrush(QColor(color)))
    it.setBackground(QBrush(QColor(row_bg)))
    it.setFont(_item_table_font(bold=bold))
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    return it


def _icon_btn(icon_name: str, tooltip: str, color: str = _T2,
              hover_bg: str = _BG) -> QPushButton:
    btn = QPushButton()
    btn.setFixedSize(24, 24)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip(tooltip)
    btn.setStyleSheet(
        f"QPushButton {{ background: {_WHITE}; border: 1px solid {_BORDER};"
        " border-radius: 4px; }}"
        f"QPushButton:hover {{ background: {hover_bg}; border-color: {color}; }}"
    )
    try:
        btn.setIcon(qta.icon(icon_name, color=color))
        btn.setIconSize(QSize(13, 13))
    except Exception:
        pass
    return btn


# ── Sub-item dialog ────────────────────────────────────────────────────────────

class _SubItemDialog(QDialog):
    """Add or edit a single sub-item (name + description match text)."""

    def __init__(self, parent_name: str, sub: Optional[SubTable] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._sub = sub
        self.result_data: dict = {}

        self.setWindowTitle("Edit Sub-item" if sub else f"Add Sub-item — {parent_name}")
        self.setFixedWidth(460)
        self.setStyleSheet(f"background: {_WHITE};")
        self._build(parent_name)
        if sub:
            self._populate(sub)

    def _build(self, parent_name: str) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(0)

        vl.addWidget(_lbl(
            "Edit Sub-item" if self._sub else f"Add Sub-item",
            size=16, weight=700, color=_NAVY,
        ))
        vl.addSpacing(4)
        sub = _lbl(
            f"Sub-items of «{parent_name}» filter its transaction table "
            "by matching the Description field.",
            size=12, color=_T2,
        )
        sub.setWordWrap(True)
        vl.addWidget(sub)
        vl.addSpacing(20)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {_BORDER};")
        vl.addWidget(sep)
        vl.addSpacing(20)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(14)

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. Dar to Congo")
        self._name.setStyleSheet(_input_ss())
        form.addRow(_lbl("Name *", size=12, weight=500, color=_T2), self._name)

        self._match = QLineEdit()
        self._match.setPlaceholderText("Leave blank to use the name as-is")
        self._match.setStyleSheet(_input_ss())
        note = _lbl(
            "Case-insensitive — transactions whose description contains this text "
            "will appear in this sub-item's table.",
            size=11, color=_TM,
        )
        note.setWordWrap(True)
        match_col = QWidget()
        match_col.setStyleSheet("background: transparent;")
        mc = QVBoxLayout(match_col)
        mc.setContentsMargins(0, 0, 0, 0)
        mc.setSpacing(4)
        mc.addWidget(self._match)
        mc.addWidget(note)
        form.addRow(_lbl("Match text", size=12, weight=500, color=_T2), match_col)

        # Auto-fill match from name while match is empty
        self._name.textChanged.connect(self._on_name_changed)
        self._match_user_edited = False
        self._match.textEdited.connect(lambda _: setattr(self, "_match_user_edited", True))

        vl.addLayout(form)
        vl.addSpacing(8)

        self._error = QLabel("")
        self._error.setStyleSheet(f"color: {_RED}; font-size: 12px;")
        self._error.setWordWrap(True)
        vl.addWidget(self._error)

        vl.addSpacing(20)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background: {_BORDER};")
        vl.addWidget(sep2)
        vl.addSpacing(16)

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
        btn_row.addSpacing(8)

        save = QPushButton("Save")
        save.setFixedHeight(34)
        save.setDefault(True)
        save.setCursor(Qt.PointingHandCursor)
        save.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600;"
            " font-family:'Segoe UI'; padding: 0 18px; }}"
            "QPushButton:hover { background: #005EA3; }"
        )
        save.clicked.connect(self._validate)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

    def _populate(self, sub: SubTable) -> None:
        self._name.setText(sub.name)
        if sub.match and sub.match != sub.name:
            self._match_user_edited = True
            self._match.setText(sub.match)

    def _on_name_changed(self, text: str) -> None:
        if not self._match_user_edited:
            self._match.setText(text)

    def _validate(self) -> None:
        name = self._name.text().strip()
        if not name:
            self._error.setText("Name is required.")
            return
        match = self._match.text().strip() or name
        self.result_data = {"name": name, "match": match}
        self.accept()


# ── Item add/edit dialog ───────────────────────────────────────────────────────

class _ItemDialog(QDialog):
    """Add / Edit Item dialog — includes description hint field."""

    def __init__(self, item: Optional[Category] = None,
                 parent: Optional[QWidget] = None,
                 prefill_name: str = "") -> None:
        super().__init__(parent)
        self._item = item
        self.result_data: dict = {}
        self._color = item.color if item else "#4A90D9"
        self._icon = item.icon if item else "mdi.tag-outline"

        self.setWindowTitle("Edit Item" if item else "Add New Item")
        self.setFixedWidth(460)
        self.setStyleSheet(f"background: {_WHITE};")
        self._build()
        if item:
            self._populate(item)
        elif prefill_name:
            self._name.setText(prefill_name)

    def _build(self) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(0)

        vl.addWidget(_lbl(
            "Edit Item" if self._item else "Add New Item",
            size=16, weight=700, color=_NAVY,
        ))
        vl.addSpacing(4)
        sub = _lbl(
            "Items appear in the cashier entry form as selectable expense types.",
            size=12, color=_T2,
        )
        sub.setWordWrap(True)
        vl.addWidget(sub)
        vl.addSpacing(20)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {_BORDER};")
        vl.addWidget(sep)
        vl.addSpacing(20)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(14)

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. Mileage, Council Fees, LATRA")
        self._name.setStyleSheet(_input_ss())
        form.addRow(_lbl("Item Name *", size=12, weight=500, color=_T2), self._name)

        desc_col = QWidget()
        desc_col.setStyleSheet("background: transparent;")
        dc = QVBoxLayout(desc_col)
        dc.setContentsMargins(0, 0, 0, 0)
        dc.setSpacing(4)
        self._description = QLineEdit()
        self._description.setPlaceholderText("Optional — auto-fills cashier description field")
        self._description.setStyleSheet(_input_ss())
        desc_note = _lbl(
            "Leave blank for items with multiple sub-items (routes, stations, etc.).",
            size=11, color=_TM,
        )
        desc_note.setWordWrap(True)
        dc.addWidget(self._description)
        dc.addWidget(desc_note)
        form.addRow(_lbl("Description hint", size=12, weight=500, color=_T2), desc_col)

        color_row = QHBoxLayout()
        color_row.setSpacing(10)
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(52, 32)
        self._color_btn.setCursor(Qt.PointingHandCursor)
        self._color_btn.clicked.connect(self._pick_color)
        self._color_hex = _lbl(self._color, size=12, color=_T2)
        color_row.addWidget(self._color_btn)
        color_row.addWidget(self._color_hex)
        color_row.addStretch()
        self._refresh_color_btn()
        form.addRow(_lbl("Colour", size=12, weight=500, color=_T2), color_row)

        checks_w = QWidget()
        checks_w.setStyleSheet("background: transparent;")
        checks_vl = QVBoxLayout(checks_w)
        checks_vl.setContentsMargins(0, 0, 0, 0)
        checks_vl.setSpacing(6)
        self._req_receipt = QCheckBox("Requires receipt")
        self._req_receipt.setStyleSheet(f"color: {_T1}; font-size: 13px;")
        self._req_truck = QCheckBox("Requires truck number")
        self._req_truck.setStyleSheet(f"color: {_T1}; font-size: 13px;")
        self._req_truck.setChecked(True)
        self._show_sidebar = QCheckBox("Show as its own sidebar tab")
        self._show_sidebar.setStyleSheet(f"color: {_T1}; font-size: 13px;")
        self._show_sidebar.toggled.connect(self._on_sidebar_toggled)
        self._lock_desc = QCheckBox("Lock description to sub-items")
        self._lock_desc.setStyleSheet(f"color: {_T1}; font-size: 13px;")
        self._lock_desc.setToolTip(
            "When on, the cashier can only choose a description from this item's "
            "sub-items. When off, any description is allowed for this item."
        )
        checks_vl.addWidget(self._req_receipt)
        checks_vl.addWidget(self._req_truck)
        checks_vl.addWidget(self._show_sidebar)
        checks_vl.addWidget(self._lock_desc)
        form.addRow(_lbl("Options", size=12, weight=500, color=_T2), checks_w)

        # Sidebar icon picker — only relevant when "Show as its own sidebar tab"
        icon_col = QWidget()
        icon_col.setStyleSheet("background: transparent;")
        icl = QHBoxLayout(icon_col)
        icl.setContentsMargins(0, 0, 0, 0)
        icl.setSpacing(10)
        self._icon_btn = QPushButton()
        self._icon_btn.setFixedSize(40, 40)
        self._icon_btn.setCursor(Qt.PointingHandCursor)
        self._icon_btn.clicked.connect(self._pick_icon)
        self._choose_icon_btn = QPushButton("Choose icon…")
        self._choose_icon_btn.setFixedHeight(32)
        self._choose_icon_btn.setCursor(Qt.PointingHandCursor)
        self._choose_icon_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
            f"QPushButton:disabled {{ color: {_TM}; }}"
        )
        self._choose_icon_btn.clicked.connect(self._pick_icon)
        icl.addWidget(self._icon_btn)
        icl.addWidget(self._choose_icon_btn)
        icl.addStretch()
        form.addRow(_lbl("Sidebar icon", size=12, weight=500, color=_T2), icon_col)

        self._sidebar_name = QLineEdit()
        self._sidebar_name.setPlaceholderText("e.g. Mileage — leave blank to use item name")
        self._sidebar_name.setStyleSheet(_input_ss())
        sidebar_name_note = _lbl(
            "Optional short label shown on the sidebar tab instead of the full item name.",
            size=11, color=_TM,
        )
        sidebar_name_note.setWordWrap(True)
        sidebar_name_col = QWidget()
        sidebar_name_col.setStyleSheet("background: transparent;")
        snc = QVBoxLayout(sidebar_name_col)
        snc.setContentsMargins(0, 0, 0, 0)
        snc.setSpacing(4)
        snc.addWidget(self._sidebar_name)
        snc.addWidget(sidebar_name_note)
        form.addRow(_lbl("Sidebar name", size=12, weight=500, color=_T2), sidebar_name_col)

        self._refresh_icon_btn()
        self._on_sidebar_toggled(False)

        vl.addLayout(form)
        vl.addSpacing(8)

        self._error = QLabel("")
        self._error.setStyleSheet(f"color: {_RED}; font-size: 12px;")
        self._error.setWordWrap(True)
        vl.addWidget(self._error)

        vl.addSpacing(20)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background: {_BORDER};")
        vl.addWidget(sep2)
        vl.addSpacing(16)

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
        btn_row.addSpacing(8)

        save = QPushButton("Save Item")
        save.setFixedHeight(34)
        save.setDefault(True)
        save.setCursor(Qt.PointingHandCursor)
        save.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600;"
            " font-family:'Segoe UI'; padding: 0 18px; }}"
            "QPushButton:hover { background: #005EA3; }"
        )
        save.clicked.connect(self._validate)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

    def _populate(self, item: Category) -> None:
        self._name.setText(item.name)
        self._description.setText(item.description or "")
        self._req_receipt.setChecked(item.requires_receipt)
        self._req_truck.setChecked(item.requires_truck)
        self._show_sidebar.setChecked(item.show_in_sidebar)
        self._lock_desc.setChecked(item.lock_description)
        self._icon = item.icon or "mdi.tag-outline"
        self._sidebar_name.setText(item.sidebar_name or "")
        self._refresh_icon_btn()
        self._on_sidebar_toggled(item.show_in_sidebar)

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, "Pick Item Colour")
        if c.isValid():
            self._color = c.name()
            self._refresh_color_btn()

    def _refresh_color_btn(self) -> None:
        self._color_btn.setStyleSheet(
            f"background: {self._color}; border: 1px solid #CBD5E1; border-radius: 5px;"
        )
        self._color_hex.setText(self._color)

    def _on_sidebar_toggled(self, on: bool) -> None:
        self._icon_btn.setEnabled(on)
        self._choose_icon_btn.setEnabled(on)
        self._sidebar_name.setEnabled(on)

    def _pick_icon(self) -> None:
        dlg = _IconPickerDialog(self._icon, parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.selected:
            self._icon = dlg.selected
            self._refresh_icon_btn()

    def _refresh_icon_btn(self) -> None:
        try:
            self._icon_btn.setIcon(qta.icon(self._icon, color=_NAVY))
            self._icon_btn.setIconSize(QSize(20, 20))
        except Exception:
            pass
        self._icon_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; border: 1px solid {_BORDER};"
            " border-radius: 6px; }}"
            f"QPushButton:hover {{ border-color: {_BLUE}; }}"
            f"QPushButton:disabled {{ background: {_BG}; }}"
        )

    def _validate(self) -> None:
        name = self._name.text().strip()
        if not name:
            self._error.setText("Item name is required.")
            return
        self.result_data = {
            "name":             name,
            "description":      self._description.text().strip(),
            "color":            self._color,
            "icon":             self._icon,
            "sidebar_name":     self._sidebar_name.text().strip(),
            "show_in_sidebar":  self._show_sidebar.isChecked(),
            "requires_receipt": self._req_receipt.isChecked(),
            "requires_truck":   self._req_truck.isChecked(),
            "lock_description": self._lock_desc.isChecked(),
        }
        self.accept()


# ── Sub-items panel (right side) ───────────────────────────────────────────────

class _SubItemsPanel(QWidget):
    """
    Right-side panel that shows and manages sub-items for the selected item.
    Sub-items are optional — the panel stays useful even with zero sub-items.
    """

    subitems_changed = Signal(str)   # parent_key — emitted after any sub-item change

    _SUB_COLS = [
        ("Name",        0,  True,   "left"),     # stretch
        ("Match text",  200, False, "left"),
        ("",            76,  False, "center"),   # actions
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._parent_key:  Optional[str] = None
        self._parent_name: Optional[str] = None
        self._subs: List[SubTable] = []
        self._build()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setStyleSheet(f"background: {_BG};")
        self.setMinimumWidth(280)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        self._hdr = QFrame()
        self._hdr.setFixedHeight(56)
        self._hdr.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-bottom: 1px solid {_BORDER};"
            f" border-left: 1px solid {_BORDER}; }}"
        )
        hl = QHBoxLayout(self._hdr)
        hl.setContentsMargins(16, 0, 12, 0)
        hl.setSpacing(10)

        try:
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(18, 18)
            icon_lbl.setStyleSheet("background: transparent;")
            icon_lbl.setPixmap(
                qta.icon("mdi.format-list-bulleted-square", color=_T2).pixmap(18, 18)
            )
            hl.addWidget(icon_lbl)
        except Exception:
            pass

        title_col = QWidget()
        title_col.setStyleSheet("background: transparent;")
        tcl = QVBoxLayout(title_col)
        tcl.setContentsMargins(0, 0, 0, 0)
        tcl.setSpacing(1)
        self._hdr_title = _lbl("Sub-items", size=14, weight=700)
        self._hdr_sub   = _lbl("Select an item ←", size=11, color=_TM)
        tcl.addWidget(self._hdr_title)
        tcl.addWidget(self._hdr_sub)
        hl.addWidget(title_col, 1)

        self._add_btn = QPushButton("+ Add")
        self._add_btn.setFixedHeight(30)
        self._add_btn.setCursor(Qt.PointingHandCursor)
        self._add_btn.setVisible(False)
        self._add_btn.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 12px; font-weight: 600;"
            " font-family:'Segoe UI'; padding: 0 12px; }}"
            "QPushButton:hover { background: #005EA3; }"
        )
        self._add_btn.clicked.connect(self._on_add)
        hl.addWidget(self._add_btn)

        root.addWidget(self._hdr)

        # Content stack: page 0 = placeholder, page 1 = table area
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")

        # Page 0: no-selection placeholder
        ph = QWidget()
        ph.setStyleSheet("background: transparent;")
        ph_vl = QVBoxLayout(ph)
        ph_vl.setAlignment(Qt.AlignCenter)
        ph_vl.setSpacing(12)
        try:
            ph_icon = QLabel()
            ph_icon.setAlignment(Qt.AlignCenter)
            ph_icon.setPixmap(
                qta.icon("mdi.arrow-left-circle-outline", color="#D1D5DB").pixmap(48, 48)
            )
            ph_icon.setStyleSheet("background: transparent;")
            ph_vl.addWidget(ph_icon)
        except Exception:
            pass
        ph_msg = _lbl("Select an item to manage\nits sub-items", size=13, color=_TM)
        ph_msg.setAlignment(Qt.AlignCenter)
        ph_msg.setWordWrap(True)
        ph_vl.addWidget(ph_msg)
        self._stack.addWidget(ph)  # index 0

        # Page 1: sub-items content (table or empty state)
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c_vl = QVBoxLayout(content)
        c_vl.setContentsMargins(0, 0, 0, 0)
        c_vl.setSpacing(0)

        # Empty-state widget (shown inside content when no sub-items)
        self._empty_state = QWidget()
        self._empty_state.setStyleSheet("background: transparent;")
        es_vl = QVBoxLayout(self._empty_state)
        es_vl.setAlignment(Qt.AlignCenter)
        es_vl.setSpacing(12)
        try:
            es_icon = QLabel()
            es_icon.setAlignment(Qt.AlignCenter)
            es_icon.setPixmap(
                qta.icon("mdi.plus-circle-outline", color="#D1D5DB").pixmap(44, 44)
            )
            es_icon.setStyleSheet("background: transparent;")
            es_vl.addWidget(es_icon)
        except Exception:
            pass
        self._empty_msg = _lbl("No sub-items yet", size=13, color=_TM)
        self._empty_msg.setAlignment(Qt.AlignCenter)
        es_vl.addWidget(self._empty_msg)
        es_hint = _lbl('Click "+ Add" to create the first one.', size=11, color=_TM)
        es_hint.setAlignment(Qt.AlignCenter)
        es_vl.addWidget(es_hint)

        # Sub-items table
        self._sub_table = QTableWidget(0, len(self._SUB_COLS))
        self._sub_table.setHorizontalHeaderLabels([c[0] for c in self._SUB_COLS])
        self._sub_table.setStyleSheet(_TABLE_SS)
        self._sub_table.verticalHeader().setVisible(False)
        self._sub_table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._sub_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._sub_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._sub_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._sub_table.setShowGrid(True)
        hh = self._sub_table.horizontalHeader()
        hh.setSectionsMovable(False)
        for i, (_, w, stretch, _align) in enumerate(self._SUB_COLS):
            if stretch:
                hh.setSectionResizeMode(i, QHeaderView.Stretch)
            else:
                hh.setSectionResizeMode(i, QHeaderView.Fixed)
                self._sub_table.setColumnWidth(i, w)

        # Use a stacked widget inside content: 0=table, 1=empty
        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet("background: transparent;")
        self._content_stack.addWidget(self._sub_table)   # index 0
        self._content_stack.addWidget(self._empty_state) # index 1

        c_vl.addWidget(self._content_stack, 1)
        self._stack.addWidget(content)  # index 1

        root.addWidget(self._stack, 1)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_item(self, item: Optional[Category]) -> None:
        if item is None:
            self.clear()
            return
        self._parent_name = item.name
        self._parent_key  = _item_key(item.name)
        self._hdr_title.setText(f"Sub-items")
        self._hdr_sub.setText(item.name)
        self._add_btn.setVisible(True)
        self._stack.setCurrentIndex(1)
        asyncio.ensure_future(self._load())

    def clear(self) -> None:
        self._parent_key  = None
        self._parent_name = None
        self._subs = []
        self._hdr_title.setText("Sub-items")
        self._hdr_sub.setText("Select an item ←")
        self._add_btn.setVisible(False)
        self._stack.setCurrentIndex(0)

    # ── Data ───────────────────────────────────────────────────────────────────

    async def _load(self) -> None:
        if not self._parent_key:
            return
        try:
            self._subs = await get_subtables(self._parent_key)
        except Exception:
            self._subs = []
        self._populate()

    def _populate(self) -> None:
        count = len(self._subs)
        self._hdr_sub.setText(
            f"{self._parent_name}  ·  {count} sub-item{'s' if count != 1 else ''}"
        )

        if not self._subs:
            self._content_stack.setCurrentIndex(1)
            return

        self._content_stack.setCurrentIndex(0)
        self._sub_table.setRowCount(len(self._subs))

        for i, sub in enumerate(self._subs):
            row_bg = _STRIPE if i % 2 else _WHITE

            name_it = QTableWidgetItem(sub.name)
            name_it.setBackground(QBrush(QColor(row_bg)))
            name_it.setFont(_item_table_font())
            name_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._sub_table.setItem(i, 0, name_it)

            match_text = sub.match if sub.match != sub.name else "—"
            match_it = QTableWidgetItem(match_text)
            match_it.setBackground(QBrush(QColor(row_bg)))
            match_it.setForeground(QBrush(QColor(_T2 if match_text == "—" else _T1)))
            match_it.setFont(_item_table_font())
            match_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._sub_table.setItem(i, 1, match_it)

            self._sub_table.setCellWidget(i, 2, self._sub_action_btns(sub, row_bg))
            self._sub_table.setRowHeight(i, _ROW_H)

    # ── Sub-item actions ───────────────────────────────────────────────────────

    def _on_add(self) -> None:
        if not self._parent_name:
            return
        dlg = _SubItemDialog(self._parent_name, parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_add(dlg.result_data))

    async def _do_add(self, data: dict) -> None:
        try:
            await create_subtable(
                parent_key=self._parent_key,
                parent_category=self._parent_name,
                name=data["name"],
                match=data["match"],
            )
            await self._load()
            self.subitems_changed.emit(self._parent_key or "")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create sub-item:\n{exc}")

    def _on_edit_sub(self, sub: SubTable) -> None:
        dlg = _SubItemDialog(self._parent_name or "", sub=sub, parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_edit_sub(sub._id, dlg.result_data))

    async def _do_edit_sub(self, sub_id: ObjectId, data: dict) -> None:
        try:
            await update_subtable(sub_id, name=data["name"], match=data["match"])
            await self._load()
            self.subitems_changed.emit(self._parent_key or "")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update sub-item:\n{exc}")

    def _on_delete_sub(self, sub: SubTable) -> None:
        if QMessageBox.question(
            self, "Delete Sub-item",
            f"Delete \"{sub.name}\"?\n\n"
            "Transactions are not affected — only this sub-view is removed.",
        ) == QMessageBox.Yes:
            asyncio.ensure_future(self._do_delete_sub(sub._id))

    async def _do_delete_sub(self, sub_id: ObjectId) -> None:
        try:
            await delete_subtable(sub_id)
            await self._load()
            self.subitems_changed.emit(self._parent_key or "")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to delete sub-item:\n{exc}")

    def _sub_action_btns(self, sub: SubTable, row_bg: str) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background: {row_bg};")
        hl = QHBoxLayout(container)
        hl.setContentsMargins(6, 4, 6, 4)
        hl.setSpacing(4)

        edit_btn = _icon_btn("mdi.pencil-outline", "Edit sub-item", _BLUE, "#EFF6FF")
        edit_btn.clicked.connect(lambda _, s=sub: self._on_edit_sub(s))
        hl.addWidget(edit_btn)

        del_btn = _icon_btn("mdi.trash-can-outline", "Delete sub-item", _RED, _RED_L)
        del_btn.clicked.connect(lambda _, s=sub: self._on_delete_sub(s))
        hl.addWidget(del_btn)

        hl.addStretch()
        return container


# ── ManageItemsWidget ──────────────────────────────────────────────────────────

class ManageItemsWidget(QWidget):
    """
    Two-panel widget: items table on the left, sub-items panel on the right.
    The right panel activates when an item row is selected.
    """

    items_changed = Signal()        # emitted after any add / edit / toggle / delete
    subitems_changed = Signal(str)  # parent_key — emitted after any sub-item change

    # Items table columns
    _ITEM_COLS = [
        ("",            44,  False, "center"),   # colour dot
        ("Name",       300,  False, "left"),
        ("Description", 140, False, "left"),
        ("Sidebar",      96, False, "center"),
        ("Req. Receipt", 96, False, "center"),
        ("Req. Truck",   90, False, "center"),
        ("Status",        88, False, "center"),
        ("Actions",      196, False, "center"),
    ]
    _ITEM_COL_DEFAULTS = [44, 300, 140, 96, 96, 90, 88, 196]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: List[Category] = []
        self._visible: List[Category] = []
        self._show_inactive = False
        self._selected_id: Optional[ObjectId] = None
        self._page = 0
        self._total = 0
        self._loading = False
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(350)
        self._search_debounce.timeout.connect(self._on_search_commit)
        self._build()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setStyleSheet(f"background: {_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_title_bar())
        root.addWidget(self._build_filter_bar())

        # ── Body: splitter ────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #E5E7EB; }"
        )

        left = self._build_left_panel()
        left.setMinimumWidth(380)
        splitter.addWidget(left)

        self._sub_panel = _SubItemsPanel()
        self._sub_panel.subitems_changed.connect(self.subitems_changed)
        splitter.addWidget(self._sub_panel)

        splitter.setSizes([600, 340])
        root.addWidget(splitter, 1)

        self._loading_overlay = LoadingOverlay(self, "Loading items…")

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
                qta.icon("mdi.tag-multiple-outline", color=_BLUE).pixmap(22, 22)
            )
            icon_lbl.setStyleSheet("background: transparent;")
            hl.addWidget(icon_lbl)
        except Exception:
            pass

        hl.addWidget(_lbl("Items", size=17, weight=700))
        self._count_lbl = _lbl("", size=12, color=_T2)
        hl.addWidget(self._count_lbl)
        hl.addStretch()

        add_btn = QPushButton("  Add Item")
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

        import_coa_btn = QPushButton("  Import Chart of Accounts")
        import_coa_btn.setFixedHeight(34)
        import_coa_btn.setCursor(Qt.PointingHandCursor)
        try:
            import_coa_btn.setIcon(qta.icon("mdi.file-upload-outline", color="#FFF"))
            import_coa_btn.setIconSize(QSize(16, 16))
        except Exception:
            pass
        import_coa_btn.setStyleSheet(
            f"QPushButton {{ background: {_NAVY}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600;"
            " font-family:'Segoe UI'; padding: 0 16px; }}"
            "QPushButton:hover { background: #253A5C; }"
        )
        import_coa_btn.clicked.connect(self._on_import_coa)
        hl.addWidget(import_coa_btn)
        return bar

    def _build_filter_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(52)
        bar.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-bottom: 1px solid {_BORDER}; }}"
        )
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(10)

        try:
            si = QLabel()
            si.setFixedSize(16, 16)
            si.setPixmap(qta.icon("mdi.magnify", color=_TM).pixmap(16, 16))
            si.setStyleSheet("background: transparent;")
            hl.addWidget(si)
        except Exception:
            pass

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search items…")
        self._search.setFixedWidth(260)
        self._search.setStyleSheet(
            f"QLineEdit {{ border: 1px solid {_BORDER}; border-radius: 5px;"
            f" background: {_WHITE}; color: {_T1}; font-size: 12px;"
            " font-family:'Segoe UI'; padding: 0 8px;"
            " min-height: 32px; max-height: 32px; }}"
            f"QLineEdit:focus {{ border-color: {_BLUE}; }}"
        )
        self._search.textChanged.connect(self._on_search_changed)
        hl.addWidget(self._search)

        hl.addStretch()

        self._restrict_btn = QPushButton("  Restrict items: Off")
        self._restrict_btn.setFixedHeight(32)
        self._restrict_btn.setCheckable(True)
        self._restrict_btn.setCursor(Qt.PointingHandCursor)
        self._restrict_btn.setToolTip(
            "When on, the cashier's Item column only accepts existing items.\n"
            "Unknown entries prompt the cashier to add the item."
        )
        try:
            self._restrict_btn.setIcon(qta.icon("mdi.lock-outline", color=_T2))
            self._restrict_btn.setIconSize(QSize(15, 15))
        except Exception:
            pass
        self._restrict_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:checked {{ background: {_GREEN_L}; color: {_GREEN};"
            f" border-color: {_GREEN}; }}"
            f"QPushButton:hover:!checked {{ background: {_BG}; }}"
        )
        self._restrict_btn.toggled.connect(self._on_restrict_toggled)
        hl.addWidget(self._restrict_btn)

        self._defer_item_btn = QPushButton("  Description-only entries: Off")
        self._defer_item_btn.setFixedHeight(32)
        self._defer_item_btn.setCheckable(True)
        self._defer_item_btn.setCursor(Qt.PointingHandCursor)
        self._defer_item_btn.setToolTip(
            "When on, cashiers may save register rows with a description only.\n"
            "Items are assigned on verify; repeated descriptions are remembered."
        )
        try:
            self._defer_item_btn.setIcon(qta.icon("mdi.text-box-outline", color=_T2))
            self._defer_item_btn.setIconSize(QSize(15, 15))
        except Exception:
            pass
        self._defer_item_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:checked {{ background: {_GREEN_L}; color: {_GREEN};"
            f" border-color: {_GREEN}; }}"
            f"QPushButton:hover:!checked {{ background: {_BG}; }}"
        )
        self._defer_item_btn.toggled.connect(self._on_defer_item_toggled)
        hl.addWidget(self._defer_item_btn)

        self._inactive_btn = QPushButton("Show Inactive")
        self._inactive_btn.setFixedHeight(32)
        self._inactive_btn.setCheckable(True)
        self._inactive_btn.setCursor(Qt.PointingHandCursor)
        self._inactive_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:checked {{ background: {_NAVY}; color: #FFF; border-color: {_NAVY}; }}"
            f"QPushButton:hover:!checked {{ background: {_BG}; }}"
        )
        self._inactive_btn.toggled.connect(self._on_inactive_toggled)
        hl.addWidget(self._inactive_btn)

        refresh_btn = QPushButton()
        refresh_btn.setFixedSize(32, 32)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; }"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
        try:
            refresh_btn.setIcon(qta.icon("mdi.refresh", color=_T2))
            refresh_btn.setIconSize(QSize(18, 18))
        except Exception:
            refresh_btn.setText("↻")
        refresh_btn.clicked.connect(self.refresh)
        hl.addWidget(refresh_btn)
        return bar

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._table = QTableWidget(0, len(self._ITEM_COLS))
        self._table.setHorizontalHeaderLabels([c[0] for c in self._ITEM_COLS])
        self._table.setStyleSheet(_TABLE_SS)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setShowGrid(True)

        hh = self._table.horizontalHeader()
        hh.setSectionsMovable(False)
        hh.setStretchLastSection(True)
        for i, (_, w_col, _stretch, _align) in enumerate(self._ITEM_COLS):
            hh.setSectionResizeMode(i, QHeaderView.Interactive)
            if w_col:
                self._table.setColumnWidth(i, w_col)
        bind_column_width_persistence(
            self._table, "manage_items", self._ITEM_COL_DEFAULTS,
        )

        self._table.selectionModel().currentRowChanged.connect(self._on_row_changed)

        vl.addWidget(self._table, 1)

        # Pagination (100 items per page; search hits the full collection)
        pager = QFrame()
        pager.setFixedHeight(44)
        pager.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-top: 1px solid {_BORDER}; }}"
        )
        pl = QHBoxLayout(pager)
        pl.setContentsMargins(12, 0, 12, 0)
        pl.setSpacing(10)

        self._page_info = _lbl("—", size=12, color=_T2)
        pl.addWidget(self._page_info)
        pl.addStretch()

        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.setFixedSize(88, 30)
        self._prev_btn.setCursor(Qt.PointingHandCursor)
        self._prev_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
            f"QPushButton:disabled {{ color: {_TM}; }}"
        )
        self._prev_btn.clicked.connect(self._on_prev_page)
        pl.addWidget(self._prev_btn)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setFixedSize(88, 30)
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
            f"QPushButton:disabled {{ color: {_TM}; }}"
        )
        self._next_btn.clicked.connect(self._on_next_page)
        pl.addWidget(self._next_btn)

        vl.addWidget(pager)
        return w

    # ── Public API ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())
        asyncio.ensure_future(self._load_cashier_settings())

    # ── Data ───────────────────────────────────────────────────────────────────

    def _on_search_changed(self) -> None:
        self._search_debounce.start()

    def _on_search_commit(self) -> None:
        self._page = 0
        asyncio.ensure_future(self._load())

    def _on_prev_page(self) -> None:
        if self._page > 0:
            self._page -= 1
            asyncio.ensure_future(self._load())

    def _on_next_page(self) -> None:
        max_pg = max(0, (self._total - 1) // _PAGE_SIZE) if self._total else 0
        if self._page < max_pg:
            self._page += 1
            asyncio.ensure_future(self._load())

    def _update_pager(self) -> None:
        total = self._total
        size = _PAGE_SIZE
        page = self._page
        max_pg = max(0, (total - 1) // size) if total else 0
        start = page * size + 1 if total else 0
        end = min((page + 1) * size, total)
        self._page_info.setText(
            f"Showing {start:,}–{end:,} of {total:,}  ·  Page {page + 1} of {max_pg + 1}"
        )
        self._prev_btn.setEnabled(page > 0)
        self._next_btn.setEnabled(page < max_pg)

    async def _load(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._loading_overlay.show_loading("Loading items…")
        try:
            search = self._search.text().strip()
            skip = self._page * _PAGE_SIZE
            items, total = await asyncio.gather(
                list_categories(
                    search=search,
                    include_inactive=self._show_inactive,
                    limit=_PAGE_SIZE,
                    skip=skip,
                ),
                count_categories(
                    search=search,
                    include_inactive=self._show_inactive,
                ),
            )
            # If the current page is past the end (e.g. after delete), snap back.
            max_pg = max(0, (total - 1) // _PAGE_SIZE) if total else 0
            if self._page > max_pg:
                self._page = max_pg
                skip = self._page * _PAGE_SIZE
                items = await list_categories(
                    search=search,
                    include_inactive=self._show_inactive,
                    limit=_PAGE_SIZE,
                    skip=skip,
                )
            self._items = items
            self._visible = items
            self._total = total
            self._populate()
            self._update_pager()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load items:\n{exc}")
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()

    # ── Cashier register settings ────────────────────────────────────────────────

    async def _load_cashier_settings(self) -> None:
        try:
            restrict_on = bool(await get_setting("restrict_items"))
        except Exception:
            restrict_on = False
        self._restrict_btn.blockSignals(True)
        self._restrict_btn.setChecked(restrict_on)
        self._restrict_btn.setText("  Restrict items: On" if restrict_on else "  Restrict items: Off")
        self._restrict_btn.blockSignals(False)

        try:
            defer_on = bool(await get_setting("defer_item_to_verify"))
        except Exception:
            defer_on = False
        self._defer_item_btn.blockSignals(True)
        self._defer_item_btn.setChecked(defer_on)
        self._defer_item_btn.setText(
            "  Description-only entries: On" if defer_on else "  Description-only entries: Off"
        )
        self._defer_item_btn.blockSignals(False)

    def _on_restrict_toggled(self, on: bool) -> None:
        self._restrict_btn.setText("  Restrict items: On" if on else "  Restrict items: Off")
        asyncio.ensure_future(self._save_restrict_setting(on))

    def _on_defer_item_toggled(self, on: bool) -> None:
        self._defer_item_btn.setText(
            "  Description-only entries: On" if on else "  Description-only entries: Off"
        )
        asyncio.ensure_future(self._save_defer_item_setting(on))

    async def _save_restrict_setting(self, on: bool) -> None:
        try:
            await set_setting("restrict_items", on)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save setting:\n{exc}")

    async def _save_defer_item_setting(self, on: bool) -> None:
        try:
            await set_setting("defer_item_to_verify", on)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save setting:\n{exc}")

    # ── Restrict-items global toggle (legacy section header) ─────────────────────

    def _populate(self) -> None:
        total = self._total
        shown = len(self._visible)
        self._count_lbl.setText(
            f"{total:,} item{'s' if total != 1 else ''}"
            + (f"  ·  {shown} on this page" if total > shown else "")
        )

        # Block selection signals while rebuilding rows
        self._table.selectionModel().blockSignals(True)
        self._table.setRowCount(len(self._visible))

        restore_row = None
        for i, item in enumerate(self._visible):
            if self._selected_id and item._id == self._selected_id:
                restore_row = i

            row_bg = _STRIPE if i % 2 else _WHITE

            # Col 0: colour dot
            dot_w = QWidget()
            dot_w.setStyleSheet(f"background: {row_bg};")
            dl = QHBoxLayout(dot_w)
            dl.setContentsMargins(0, 0, 0, 0)
            dl.setAlignment(Qt.AlignCenter)
            dot = QLabel()
            dot.setFixedSize(14, 14)
            dot.setStyleSheet(
                f"background: {item.color}; border-radius: 7px;"
                " border: 1px solid rgba(0,0,0,0.1);"
            )
            dl.addWidget(dot)
            self._table.setCellWidget(i, 0, dot_w)

            # Col 1: name
            name_it = QTableWidgetItem(item.name)
            name_it.setBackground(QBrush(QColor(row_bg)))
            name_it.setForeground(QBrush(QColor(_T1 if item.active else _TM)))
            name_it.setFont(_item_table_font(italic=not item.active))
            name_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._table.setItem(i, 1, name_it)

            # Col 2: description hint
            desc_text = item.description or ""
            desc_it = QTableWidgetItem(desc_text if desc_text else "—")
            desc_it.setBackground(QBrush(QColor(row_bg)))
            desc_it.setForeground(QBrush(QColor(_T2 if desc_text else _TM)))
            desc_it.setFont(_item_table_font())
            desc_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._table.setItem(i, 2, desc_it)

            # Col 3: sidebar tab indicator (icon + On / —)
            if item.show_in_sidebar:
                self._table.setCellWidget(i, 3, self._sidebar_cell(item, row_bg))
            else:
                self._table.removeCellWidget(i, 3)
                self._table.setItem(i, 3, _status_item("—", _TM, row_bg, bold=False))

            # Col 4: req receipt
            self._table.setItem(i, 4, _status_item(
                "Yes" if item.requires_receipt else "No",
                _GREEN if item.requires_receipt else _TM, row_bg,
            ))

            # Col 5: req truck
            self._table.setItem(i, 5, _status_item(
                "Yes" if item.requires_truck else "No",
                _GREEN if item.requires_truck else _TM, row_bg,
            ))

            # Col 6: status
            self._table.setItem(i, 6, _status_item(
                "Active" if item.active else "Inactive",
                _GREEN if item.active else _RED, row_bg,
            ))

            # Col 7: action buttons
            self._table.setCellWidget(i, 7, self._action_btns(item, row_bg))
            self._table.setRowHeight(i, _ROW_H)

        self._table.selectionModel().blockSignals(False)
        self._repaint_row_widgets()

        # Restore previous selection
        if restore_row is not None:
            self._table.selectRow(restore_row)
        else:
            self._selected_id = None
            self._sub_panel.clear()

    # ── Row selection → sub-items panel ───────────────────────────────────────

    def _on_row_changed(self, current, previous) -> None:
        row = current.row()
        if 0 <= row < len(self._visible):
            item = self._visible[row]
            self._selected_id = item._id
            self._sub_panel.set_item(item)
        else:
            self._selected_id = None
            self._sub_panel.clear()
        self._repaint_row_widgets()

    # ── Item CRUD ──────────────────────────────────────────────────────────────

    def _on_inactive_toggled(self, checked: bool) -> None:
        self._show_inactive = checked
        self._page = 0
        self.refresh()

    def _on_import_coa(self) -> None:
        asyncio.ensure_future(self._do_import_coa())

    async def _do_import_coa(self) -> None:
        from pathlib import Path

        from tahmeed.services.chart_of_accounts_service import import_chart_of_accounts

        default_dir = str(Path(__file__).resolve().parents[3])
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Chart of Accounts",
            default_dir,
            "Excel Files (*.xlsx)",
        )
        if not path:
            return

        reply = QMessageBox.warning(
            self,
            "Replace All Items?",
            "This will delete every existing item, sub-item, keyword rule, "
            "and description mapping, then load all accounts from the "
            "Chart of Accounts file.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply != QMessageBox.Yes:
            return

        self._loading_overlay.show_loading("Importing Chart of Accounts…")
        try:
            result = await import_chart_of_accounts(path, replace_existing=True)
        except Exception as exc:
            self._loading_overlay.hide_loading()
            QMessageBox.critical(self, "Import Error", f"Could not import Chart of Accounts:\n{exc}")
            return

        removed = result["removed"]
        self._loading_overlay.hide_loading()
        QMessageBox.information(
            self,
            "Import Complete",
            f"Imported {result['imported']:,} items from Chart of Accounts.\n\n"
            f"Removed {removed['categories']:,} old item(s), "
            f"{removed['subtables']:,} sub-item(s), "
            f"{removed['keyword_rules']:,} keyword rule(s), "
            f"and {removed['mappings']:,} description mapping(s).",
        )
        self._page = 0
        await self._load()
        self.items_changed.emit()

    def _on_add(self) -> None:
        dlg = _ItemDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_add(dlg.result_data))

    async def _do_add(self, data: dict) -> None:
        try:
            cat = await create_category(
                data["name"], data["color"],
                data["requires_receipt"], data["requires_truck"],
                data.get("description", ""),
                icon=data.get("icon", "mdi.tag-outline"),
                sidebar_name=data.get("sidebar_name", ""),
                show_in_sidebar=data.get("show_in_sidebar", False),
                lock_description=data.get("lock_description", False),
            )
            self._selected_id = cat._id
            await self._load()
            self.items_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create item:\n{exc}")

    def _on_edit(self, item: Category) -> None:
        self._selected_id = item._id
        dlg = _ItemDialog(item=item, parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_edit(item._id, dlg.result_data))

    async def _do_edit(self, item_id: ObjectId, data: dict) -> None:
        try:
            await update_category(item_id, **data)
            await self._load()
            self.items_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update item:\n{exc}")

    def _on_toggle(self, item: Category) -> None:
        self._selected_id = item._id
        going_inactive = item.active
        msg = (
            f"Deactivate \"{item.name}\"?\n\n"
            "Deactivated items won't appear in the cashier entry form."
            if going_inactive else
            f"Activate \"{item.name}\"?\n\n"
            "The item will be available in the cashier entry form again."
        )
        if QMessageBox.question(self, "Confirm", msg) == QMessageBox.Yes:
            asyncio.ensure_future(self._do_toggle(item._id, not item.active))

    async def _do_toggle(self, item_id: ObjectId, active: bool) -> None:
        try:
            await toggle_category(item_id, active)
            await self._load()
            self.items_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update item:\n{exc}")

    def _on_delete(self, item: Category) -> None:
        reply = QMessageBox.warning(
            self, "Delete Item",
            f"Permanently delete \"{item.name}\"?\n\n"
            "All sub-items under this item will also be removed. "
            "Existing transactions keep their data but the item won't appear "
            "in any dropdown or filter.\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Yes:
            asyncio.ensure_future(self._do_delete(item._id, item.name))

    async def _do_delete(self, item_id: ObjectId, item_name: str) -> None:
        try:
            await delete_category(item_id)
            # Also clean up any sub-tables for this item
            key = _item_key(item_name)
            subs = await get_subtables(key, include_inactive=True)
            for sub in subs:
                await delete_subtable(sub._id)
            if self._selected_id == item_id:
                self._selected_id = None
                self._sub_panel.clear()
            await self._load()
            self.items_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to delete item:\n{exc}")

    # ── Sidebar indicator cell ─────────────────────────────────────────────────

    def _sidebar_cell(self, item: Category, row_bg: str) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background: {row_bg};")
        hl = QHBoxLayout(container)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(5)
        hl.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(15, 15)
        icon_lbl.setStyleSheet("background: transparent;")
        try:
            icon_lbl.setPixmap(
                qta.icon(item.icon or "mdi.tag-outline", color=_BLUE).pixmap(15, 15)
            )
        except Exception:
            pass
        hl.addWidget(icon_lbl)

        on = QLabel(item.sidebar_label if item.sidebar_name else "On")
        on.setStyleSheet(
            f"color: {_GREEN}; background: transparent;"
            " font-size: 11px; font-weight: 400; font-family:'Segoe UI';"
        )
        hl.addWidget(on)
        return container

    # ── Uniform selection highlight for widget cells ───────────────────────────

    def _repaint_row_widgets(self) -> None:
        """Repaint the dot / sidebar / action cell-widgets so the selected row
        highlights uniformly with the plain text cells (which Qt handles via QSS)."""
        sel = self._table.currentRow()
        last = len(self._ITEM_COLS) - 1
        for r in range(self._table.rowCount()):
            bg = _BLUE_L if r == sel else (_STRIPE if r % 2 else _WHITE)
            for c in (0, 3, last):
                w = self._table.cellWidget(r, c)
                if w is not None:
                    w.setStyleSheet(f"background: {bg};")

    # ── Action buttons cell ────────────────────────────────────────────────────

    def _action_btns(self, item: Category, row_bg: str) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background: {row_bg};")
        hl = QHBoxLayout(container)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(5)

        edit_btn = QPushButton("Edit")
        edit_btn.setFixedHeight(24)
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_BLUE};"
            f" border: 1px solid {_BLUE}; border-radius: 4px;"
            " font-size: 11px; font-weight: 400;"
            " font-family:'Segoe UI'; padding: 0 10px; }}"
            f"QPushButton:hover {{ background: #EFF6FF; }}"
        )
        edit_btn.clicked.connect(lambda _, it=item: self._on_edit(it))
        hl.addWidget(edit_btn)

        toggle_lbl = "Activate" if not item.active else "Deactivate"
        toggle_btn = QPushButton(toggle_lbl)
        toggle_btn.setFixedHeight(24)
        toggle_btn.setCursor(Qt.PointingHandCursor)
        toggle_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 4px;"
            " font-size: 11px;"
            " font-family:'Segoe UI'; padding: 0 10px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
        toggle_btn.clicked.connect(lambda _, it=item: self._on_toggle(it))
        hl.addWidget(toggle_btn)

        del_btn = _icon_btn("mdi.trash-can-outline", "Delete item permanently", _RED, _RED_L)
        del_btn.clicked.connect(lambda _, it=item: self._on_delete(it))
        hl.addWidget(del_btn)

        hl.addStretch()
        return container
