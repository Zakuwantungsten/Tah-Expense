"""Add / Edit Item dialog — shared by Manage Items and import mapping."""

from __future__ import annotations

from typing import List, Optional

import qtawesome as qta
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QToolButton, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor

from tahmeed.models.category import Category

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_NAVY = "#1B2B4B"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"
_RED = "#DC2626"


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

# ── Item add/edit dialog ───────────────────────────────────────────────────────

class ItemDialog(QDialog):
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
        self._show_cashier_sidebar = QCheckBox("Show in the cashier's sidebar")
        self._show_cashier_sidebar.setStyleSheet(f"color: {_T1}; font-size: 13px;")
        self._show_cashier_sidebar.toggled.connect(self._on_sidebar_toggled)
        self._lock_desc = QCheckBox("Lock description to sub-items")
        self._lock_desc.setStyleSheet(f"color: {_T1}; font-size: 13px;")
        self._lock_desc.setToolTip(
            "When on, the cashier can only choose a description from this item's "
            "sub-items. When off, any description is allowed for this item."
        )
        checks_vl.addWidget(self._req_receipt)
        checks_vl.addWidget(self._req_truck)
        checks_vl.addWidget(self._show_sidebar)
        checks_vl.addWidget(self._show_cashier_sidebar)
        checks_vl.addWidget(self._lock_desc)
        form.addRow(_lbl("Options", size=12, weight=500, color=_T2), checks_w)

        # Sidebar icon picker — relevant when either sidebar option is on
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
        self._show_cashier_sidebar.setChecked(item.show_in_cashier_sidebar)
        self._lock_desc.setChecked(item.lock_description)
        self._icon = item.icon or "mdi.tag-outline"
        self._sidebar_name.setText(item.sidebar_name or "")
        self._refresh_icon_btn()
        self._on_sidebar_toggled()

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

    def _on_sidebar_toggled(self, _on: bool = False) -> None:
        on = self._show_sidebar.isChecked() or self._show_cashier_sidebar.isChecked()
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
            "show_in_cashier_sidebar": self._show_cashier_sidebar.isChecked(),
            "requires_receipt": self._req_receipt.isChecked(),
            "requires_truck":   self._req_truck.isChecked(),
            "lock_description": self._lock_desc.isChecked(),
        }
        self.accept()


_ItemDialog = ItemDialog
