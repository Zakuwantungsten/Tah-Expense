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
    QDialog, QFormLayout, QMenu, QWidgetAction,
    QSplitter, QStackedWidget, QSizePolicy,
    QFileDialog, QListWidget, QListWidgetItem,
)
from PySide6.QtCore import Qt, QSize, Signal, QTimer
from PySide6.QtGui import QColor, QBrush, QFont

from tahmeed.models.category import Category
from tahmeed.models.sub_table import SubTable
from tahmeed.services.category_service import (
    list_categories, count_categories, get_all_categories,
    create_category, update_category,
    toggle_category, delete_category, item_key,
)
from tahmeed.services.subtable_service import (
    get_subtables, create_subtable, update_subtable, delete_subtable,
)
from tahmeed.services.settings_service import get_setting, set_setting
from tahmeed.services.export_restriction_service import (
    EXPORT_SURFACES,
    get_enabled_export_surfaces,
    set_enabled_export_surfaces,
)
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay
from tahmeed.ui.dialogs.item_dialog import ItemDialog as _ItemDialog
from tahmeed.ui.accountant.item_quick_report import ItemQuickReportView

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


class _ExportScopesDialog(QDialog):
    """Multi-select export surfaces that apply restrict_in_pdf / restrict_in_excel."""

    def __init__(self, enabled: set[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export scopes")
        self.setMinimumWidth(520)
        self._enabled = set(enabled)
        self._build()

    def _build(self) -> None:
        self.setStyleSheet(f"background: {_WHITE};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 20, 24, 20)
        vl.setSpacing(12)

        title = _lbl("Export restriction scopes", size=16, weight=600, color=_NAVY)
        vl.addWidget(title)
        intro = _lbl(
            "Tick each export destination that should omit items marked "
            "Restrict in PDF or Restrict in Excel. Item Quick Report is never affected.",
            size=12, color=_T2,
        )
        intro.setWordWrap(True)
        vl.addWidget(intro)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ border: 1px solid {_BORDER}; border-radius: 6px;"
            f" font-size: 13px; font-family:'Segoe UI'; padding: 4px; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 4px; }}"
            f"QListWidget::item:hover {{ background: {_BG}; }}"
        )
        for key, label in EXPORT_SURFACES.items():
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if key in self._enabled else Qt.Unchecked)
            self._list.addItem(item)
        vl.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = QPushButton("Save scopes")
        save.setDefault(True)
        save.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600; padding: 0 16px;"
            " min-height: 34px; }}"
        )
        save.clicked.connect(self._save)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

    def _save(self) -> None:
        picked: list[str] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.Checked:
                picked.append(str(item.data(Qt.UserRole)))
        self._enabled = set(picked)
        self.accept()

    @property
    def selected(self) -> set[str]:
        return set(self._enabled)


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
        ("Name",       260,  False, "left"),
        ("Description", 140, False, "left"),
        ("Sidebar",      96, False, "center"),
        ("Req. Receipt", 96, False, "center"),
        ("Req. Truck",   90, False, "center"),
        ("Excl. PDF",    80, False, "center"),
        ("Excl. Excel",  88, False, "center"),
        ("Status",        88, False, "center"),
        ("Amount",      120, False, "right"),
        ("Actions",       56, False, "center"),
    ]
    _ITEM_COL_DEFAULTS = [44, 260, 140, 96, 96, 90, 80, 88, 88, 120, 56]
    _COL_AMOUNT = 9
    _COL_ACTIONS = 10

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: List[Category] = []
        self._visible: List[Category] = []
        self._show_inactive = False
        self._show_export_restricted = False
        self._selected_id: Optional[ObjectId] = None
        self._page = 0
        self._total = 0
        self._loading = False
        self._scroll_loading = False
        self._usage_by_name: dict = {}
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(350)
        self._search_debounce.timeout.connect(self._on_search_commit)
        self._report_item: Optional[Category] = None
        self._build()
        asyncio.ensure_future(self._load_initial())
        asyncio.ensure_future(self._load_cashier_settings())

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setStyleSheet(f"background: {_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_catalog_page())   # 0 — items list
        self._stack.addWidget(self._build_report_shell())   # 1 — item QuickReport
        root.addWidget(self._stack, 1)

        self._loading_overlay = LoadingOverlay(self, "Loading items…")

    def _build_catalog_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {_BG};")
        vl = QVBoxLayout(page)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        vl.addWidget(self._build_title_bar())
        vl.addWidget(self._build_filter_bar())

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
        vl.addWidget(splitter, 1)
        return page

    def _build_report_shell(self) -> QWidget:
        """In-page Account QuickReport shell (table + filters land in later phases)."""
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

        back_btn = QPushButton("← Items")
        back_btn.setFixedHeight(30)
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
        back_btn.clicked.connect(self._close_item_report)
        nl.addWidget(back_btn)

        self._report_nav_lbl = _lbl("", size=13, weight=600, color=_NAVY)
        nl.addWidget(self._report_nav_lbl)
        nl.addStretch()
        vl.addWidget(nav)

        body = QFrame()
        body.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border: none; }}"
        )
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 20, 24, 16)
        bl.setSpacing(4)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        self._report_meta_lbl = QLabel("")
        self._report_meta_lbl.setStyleSheet(
            f"color: {_T2}; font-size: 11px;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        meta_row.addWidget(self._report_meta_lbl)
        meta_row.addStretch()
        bl.addLayout(meta_row)

        self._report_company_lbl = QLabel("TAHMEED COACH TZ LTD")
        self._report_company_lbl.setAlignment(Qt.AlignCenter)
        self._report_company_lbl.setStyleSheet(
            f"color: {_T1}; font-size: 15px; font-weight: 700;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        bl.addWidget(self._report_company_lbl)

        self._report_kind_lbl = QLabel("Account QuickReport")
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

        self._report_content = QFrame()
        self._report_content.setObjectName("itemReportContent")
        self._report_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._report_content.setStyleSheet(
            f"#itemReportContent {{ background: {_WHITE}; border: none; }}"
        )
        cl = QVBoxLayout(self._report_content)
        cl.setContentsMargins(0, 16, 0, 0)
        cl.setSpacing(0)
        self._report_table = ItemQuickReportView()
        self._report_table.header_context_changed.connect(self._on_report_header_context)
        cl.addWidget(self._report_table, 1)
        bl.addWidget(self._report_content, 1)

        vl.addWidget(body, 1)
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
            "When on, the cashier's Item column flags unknown entries.\n"
            "Unknown text is kept but marked; saving still requires a known item."
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

        self._export_restricted_btn = QPushButton("Export restricted")
        self._export_restricted_btn.setFixedHeight(32)
        self._export_restricted_btn.setCheckable(True)
        self._export_restricted_btn.setCursor(Qt.PointingHandCursor)
        self._export_restricted_btn.setToolTip(
            "Show only items marked Restrict in PDF and/or Restrict in Excel."
        )
        self._export_restricted_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:checked {{ background: {_AMBER_L}; color: {_AMBER};"
            f" border-color: {_AMBER}; }}"
            f"QPushButton:hover:!checked {{ background: {_BG}; }}"
        )
        self._export_restricted_btn.toggled.connect(self._on_export_restricted_toggled)
        hl.addWidget(self._export_restricted_btn)

        scopes_btn = QPushButton("Export scopes…")
        scopes_btn.setFixedHeight(32)
        scopes_btn.setCursor(Qt.PointingHandCursor)
        scopes_btn.setToolTip(
            "Choose which export destinations honour per-item PDF/Excel restrictions."
        )
        scopes_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 12px; font-family:'Segoe UI'; padding: 0 12px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
        scopes_btn.clicked.connect(self._on_export_scopes)
        hl.addWidget(scopes_btn)

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
        self._table.cellDoubleClicked.connect(self._on_item_double_clicked)
        self._table.verticalScrollBar().valueChanged.connect(self._on_scroll)

        vl.addWidget(self._table, 1)

        footer = QFrame()
        footer.setFixedHeight(40)
        footer.setStyleSheet(
            f"QFrame {{ background: {_WHITE}; border-top: 1px solid {_BORDER}; }}"
        )
        pl = QHBoxLayout(footer)
        pl.setContentsMargins(12, 0, 12, 0)
        self._page_info = _lbl("—", size=12, color=_T2)
        pl.addWidget(self._page_info)
        pl.addStretch()
        vl.addWidget(footer)
        return w

    # ── Public API ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        self._page = 0
        asyncio.ensure_future(self._load_initial())
        asyncio.ensure_future(self._load_cashier_settings())

    # ── Item QuickReport navigation ────────────────────────────────────────────

    def _on_item_double_clicked(self, row: int, col: int) -> None:
        if col == self._COL_ACTIONS:
            return  # Actions ⋯ — use the menu, don't open the report
        if row < 0 or row >= len(self._visible):
            return
        self._open_item_report(self._visible[row])

    def _open_item_report(self, item: Category) -> None:
        from datetime import date as _date, datetime as _dt

        self._report_item = item
        self._selected_id = item._id
        self._report_nav_lbl.setText(f"Account QuickReport  ·  {item.name}")
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

    def _close_item_report(self) -> None:
        self._report_item = None
        self._report_table.clear()
        self._stack.setCurrentIndex(0)

    # ── Data ───────────────────────────────────────────────────────────────────

    def _on_search_changed(self) -> None:
        self._search_debounce.start()

    def _on_search_commit(self) -> None:
        self._page = 0
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
            f"{total:,} item{'s' if total != 1 else ''}"
            + (f"  ·  {loaded} loaded" if total > loaded else "")
        )

    async def _load_initial(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._page = 0
        self._loading_overlay.show_loading("Loading items…")
        try:
            search = self._search.text().strip().lower()
            if self._show_export_restricted:
                all_items = await get_all_categories(
                    include_inactive=self._show_inactive,
                    is_supplier=False,
                )
                filtered = [
                    item for item in all_items
                    if item.restrict_in_pdf or item.restrict_in_excel
                ]
                if search:
                    filtered = [
                        item for item in filtered
                        if search in (item.name or "").lower()
                        or search in (item.description or "").lower()
                    ]
                self._items = filtered
                self._visible = list(filtered)
                self._total = len(filtered)
            else:
                items, total = await asyncio.gather(
                    list_categories(
                        search=self._search.text().strip(),
                        include_inactive=self._show_inactive,
                        is_supplier=False,
                        limit=_PAGE_SIZE,
                        skip=0,
                    ),
                    count_categories(
                        search=self._search.text().strip(),
                        include_inactive=self._show_inactive,
                        is_supplier=False,
                    ),
                )
                self._items = list(items)
                self._visible = list(items)
                self._total = total
            self._usage_by_name = {}
            await self._fetch_usage_for(self._visible)
            self._populate(reset=True)
            self._update_scroll_footer()
            self._fill_if_needed()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load items:\n{exc}")
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()
            self._update_scroll_footer()

    async def _load_more(self) -> None:
        if self._scroll_loading or self._loading or self._show_export_restricted:
            return
        if len(self._visible) >= self._total:
            return
        self._scroll_loading = True
        self._update_scroll_footer()
        try:
            search = self._search.text().strip()
            skip = len(self._visible)
            items = await list_categories(
                search=search,
                include_inactive=self._show_inactive,
                is_supplier=False,
                limit=_PAGE_SIZE,
                skip=skip,
            )
            if not items:
                return
            self._items.extend(items)
            self._visible.extend(items)
            await self._fetch_usage_for(items)
            self._populate(reset=False, new_items=items, start_row=skip)
            self._fill_if_needed()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load more items:\n{exc}")
        finally:
            self._scroll_loading = False
            self._update_scroll_footer()

    async def _fetch_usage_for(self, items: List[Category]) -> None:
        names = [it.name for it in items if it.name]
        if not names:
            return
        try:
            from tahmeed.services.accountant_service import get_categories_usage_totals

            usage = await get_categories_usage_totals(names)
            self._usage_by_name.update(usage)
        except Exception:
            pass

    async def _load(self) -> None:
        """Compatibility wrapper — full refresh from the top."""
        await self._load_initial()

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

    def _populate(
        self,
        *,
        reset: bool = True,
        new_items: Optional[List[Category]] = None,
        start_row: int = 0,
    ) -> None:
        if reset:
            items = self._visible
            self._table.selectionModel().blockSignals(True)
            self._table.setRowCount(len(items))
            restore_row = None
            for i, item in enumerate(items):
                if self._selected_id and item._id == self._selected_id:
                    restore_row = i
                self._fill_item_row(i, item)
            self._table.selectionModel().blockSignals(False)
            self._repaint_row_widgets()
            if restore_row is not None:
                self._table.selectRow(restore_row)
            else:
                self._selected_id = None
                self._sub_panel.clear()
            self._update_scroll_footer()
            return

        batch = new_items or []
        self._table.selectionModel().blockSignals(True)
        for offset, item in enumerate(batch):
            r = start_row + offset
            if r >= self._table.rowCount():
                self._table.insertRow(r)
            self._fill_item_row(r, item)
        self._table.selectionModel().blockSignals(False)
        self._repaint_row_widgets()
        self._update_scroll_footer()

    def _fill_item_row(self, i: int, item: Category) -> None:
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

        # Col 3: sidebar tab indicator
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

        # Col 6: exclude from PDF export
        self._table.setItem(i, 6, _status_item(
            "Yes" if item.restrict_in_pdf else "No",
            _AMBER if item.restrict_in_pdf else _TM, row_bg,
        ))

        # Col 7: exclude from Excel export
        self._table.setItem(i, 7, _status_item(
            "Yes" if item.restrict_in_excel else "No",
            _AMBER if item.restrict_in_excel else _TM, row_bg,
        ))

        # Col 8: status
        self._table.setItem(i, 8, _status_item(
            "Active" if item.active else "Inactive",
            _GREEN if item.active else _RED, row_bg,
        ))

        # Col 9: amount used (lifetime, always positive, black)
        usage = self._usage_by_name.get((item.name or "").strip().lower(), {})
        tzs_used = abs(float(usage.get("tzs") or 0.0))
        amt_it = QTableWidgetItem(f"{tzs_used:,.0f}" if tzs_used else "—")
        amt_it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        amt_it.setBackground(QBrush(QColor(row_bg)))
        amt_it.setForeground(QBrush(QColor(_T1 if tzs_used else _TM)))
        amt_it.setFont(_item_table_font())
        amt_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self._table.setItem(i, self._COL_AMOUNT, amt_it)

        # Col 10: actions
        self._table.setCellWidget(i, self._COL_ACTIONS, self._action_btns(item, row_bg))
        self._table.setRowHeight(i, _ROW_H)

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

    def _on_export_restricted_toggled(self, checked: bool) -> None:
        self._show_export_restricted = checked
        self._page = 0
        self.refresh()

    def _on_export_scopes(self) -> None:
        asyncio.ensure_future(self._do_export_scopes())

    async def _do_export_scopes(self) -> None:
        try:
            enabled = await get_enabled_export_surfaces()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load export scopes:\n{exc}")
            return
        dlg = _ExportScopesDialog(enabled, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            await set_enabled_export_surfaces(sorted(dlg.selected))
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save export scopes:\n{exc}")

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
                show_in_cashier_sidebar=data.get("show_in_cashier_sidebar", False),
                lock_description=data.get("lock_description", False),
                restrict_in_pdf=data.get("restrict_in_pdf", False),
                restrict_in_excel=data.get("restrict_in_excel", False),
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

    # ── Actions cell (⋯ menu) ─────────────────────────────────────────────────

    def _action_btns(self, item: Category, row_bg: str) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background: {row_bg};")
        hl = QHBoxLayout(container)
        hl.setContentsMargins(4, 4, 4, 4)
        hl.setSpacing(0)
        hl.setAlignment(Qt.AlignCenter)

        menu_btn = _icon_btn("mdi.dots-vertical", "Item actions", _T2, _BG)
        menu_btn.clicked.connect(lambda _, it=item, b=menu_btn: self._open_item_menu(it, b))
        hl.addWidget(menu_btn)
        return container

    def _open_item_menu(self, item: Category, anchor: QWidget) -> None:
        asyncio.ensure_future(self._open_item_menu_async(item, anchor))

    async def _open_item_menu_async(self, item: Category, anchor: QWidget) -> None:
        from tahmeed.services.accountant_service import get_category_lifetime_usage

        try:
            usage = await get_category_lifetime_usage(item.name)
        except Exception:
            usage = {"count": 0, "tzs": 0.0, "usd": 0.0}

        if not anchor.isVisible():
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {_WHITE}; border: 1px solid {_BORDER};"
            " padding: 4px; border-radius: 6px; }}"
            f"QMenu::item {{ padding: 6px 20px; border-radius: 4px;"
            f" color: {_T1}; font-size: 12px; font-family:'Segoe UI'; }}"
            f"QMenu::item:selected {{ background: {_BLUE_L}; }}"
            f"QMenu::separator {{ height: 1px; background: {_BORDER}; margin: 4px 8px; }}"
        )

        usage_act = QWidgetAction(menu)
        usage_act.setDefaultWidget(self._usage_summary_widget(item.name, usage))
        menu.addAction(usage_act)
        menu.addSeparator()

        edit_act = menu.addAction(qta.icon("mdi.pencil-outline", color=_BLUE), "Edit")
        toggle_lbl = "Activate" if not item.active else "Deactivate"
        toggle_icon = "mdi.check-circle-outline" if not item.active else "mdi.pause-circle-outline"
        toggle_act = menu.addAction(qta.icon(toggle_icon, color=_T2), toggle_lbl)
        menu.addSeparator()
        delete_act = menu.addAction(
            qta.icon("mdi.trash-can-outline", color=_RED), "Delete"
        )

        chosen = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        if chosen == edit_act:
            self._on_edit(item)
        elif chosen == toggle_act:
            self._on_toggle(item)
        elif chosen == delete_act:
            self._on_delete(item)

    def _usage_summary_widget(self, item_name: str, usage: dict) -> QWidget:
        """Compact lifetime usage block shown at the top of the ⋯ menu."""
        count = int(usage.get("count") or 0)
        tzs = abs(float(usage.get("tzs") or 0.0))
        usd = abs(float(usage.get("usd") or 0.0))

        wrap = QFrame()
        wrap.setFixedWidth(220)
        wrap.setStyleSheet(
            f"QFrame {{ background: {_BG}; border: none; border-radius: 4px; }}"
        )
        vl = QVBoxLayout(wrap)
        vl.setContentsMargins(12, 10, 12, 10)
        vl.setSpacing(2)

        title = QLabel("Total used (all years)")
        title.setStyleSheet(
            f"color: {_T2}; font-size: 10px; font-weight: 600;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        vl.addWidget(title)

        name_lbl = QLabel(item_name)
        name_lbl.setWordWrap(True)
        name_lbl.setStyleSheet(
            f"color: {_NAVY}; font-size: 12px; font-weight: 600;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        vl.addWidget(name_lbl)

        count_lbl = QLabel(f"{count:,} entries")
        count_lbl.setStyleSheet(
            f"color: {_T2}; font-size: 11px;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        vl.addWidget(count_lbl)

        tzs_lbl = QLabel(f"TZS  {tzs:,.0f}")
        tzs_lbl.setStyleSheet(
            f"color: {_T1}; font-size: 13px; font-weight: 700;"
            " font-family:'Cascadia Code','Consolas',monospace;"
            " background: transparent; margin-top: 4px;"
        )
        vl.addWidget(tzs_lbl)

        if usd:
            usd_lbl = QLabel(f"USD  ${usd:,.2f}")
            usd_lbl.setStyleSheet(
                f"color: {_T1}; font-size: 12px; font-weight: 600;"
                " font-family:'Cascadia Code','Consolas',monospace;"
                " background: transparent;"
            )
            vl.addWidget(usd_lbl)

        return wrap
