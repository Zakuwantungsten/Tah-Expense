"""AccountantDashboard — Collapsible sidebar navigation.

Category items (CATEGORIES section) are expandable: clicking the chevron
reveals their user-created sub-tables (e.g. Mileage routes) plus a
"+ Add Sub-table" row. Clicking the item body still opens the full
(all-rows) category table.
"""

from __future__ import annotations
import asyncio
from typing import Optional, Dict, List

import qtawesome as qta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QToolButton, QSizePolicy, QDialog, QLineEdit,
    QPushButton, QMessageBox, QMenu,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon

from tahmeed.ui.accountant.category_tables import CATEGORY_DEFS

# ── Design tokens ─────────────────────────────────────────────────────────────

_NAVY       = "#1B2B4B"
_ACTIVE_BG  = "#253A5C"
_SUB_BG     = "#16243f"   # slightly darker strip behind sub-items
_BLUE       = "#0077C5"
_WHITE      = "#F9FAFB"
_MUTED      = "#94A3B8"
_RED        = "#DC2626"

EXPANDED_W  = 220
COLLAPSED_W = 56

# ── Nav data ──────────────────────────────────────────────────────────────────
#  Each entry: (key, label, mdi_icon, options_dict)
#  options: badge=True, chevron=True

_SECTIONS: list[tuple[Optional[str], list[tuple]]] = [
    (None, [
        ("overview",        "Overview",          "mdi.view-dashboard-outline", {}),
    ]),
    ("CASHIER FLOW", [
        ("verify",          "Verify",            "mdi.inbox-arrow-down",             {"badge": True}),
        ("master_expenses", "Master Expenses",   "mdi.table-large",                  {}),
    ]),
    ("CATEGORIES", [
        ("mileage",         "Mileage",                "mdi.road-variant",                 {}),
        ("latra",           "LATRA",                  "mdi.card-account-details-outline", {}),
        ("c28",             "C28",                    "mdi.file-document-outline",        {}),
        ("c40",             "C40",                    "mdi.file-document-outline",        {}),
        ("carbon_permit",   "Carbon & Permit",        "mdi.leaf",                         {}),
        ("council_fees",    "Council Fees",           "mdi.city-variant",                 {}),
        ("return_weigh",    "Return & Weighbridge",   "mdi.scale",                        {}),
        ("parking_petroda", "Parking Petroda",        "mdi.parking",                      {}),
        ("backload",        "Backload Facilitation",  "mdi.truck-delivery",               {}),
        ("rope_sealing",    "Rope & Sealing",         "mdi.link-variant",                 {}),
        ("radiation",       "Radiation Taxes",        "mdi.radioactive",                  {}),
        ("health_fee",      "Health Fee",             "mdi.hospital-box",                 {}),
        ("halmashauri",     "Halmashauri Parking",    "mdi.parking",                      {}),
    ]),
    ("SEPARATE EXPENSES", [
        ("toll_plaza",      "Toll Plaza",           "mdi.boom-gate",         {}),
        ("parking_congo",   "Parking Congo",        "mdi.parking",           {}),
        ("congo_exp",       "Congo Expenses",       "mdi.map-marker",        {}),
        ("ahmed_kimvi",     "Ahmed Kimvi (Klesa)",  "mdi.account-cash",      {}),
        ("zambia_parking",  "Zambia Parking",       "mdi.map",               {}),
        ("harrison",        "Harrison Expenses",    "mdi.account-tie",       {}),
        ("afritrack",       "Afritrack",            "mdi.satellite-variant", {}),
        ("third_party",     "Third Party Covers",   "mdi.shield-account",    {}),
        ("comesa",          "COMESA Covers",        "mdi.certificate",       {}),
        ("sm_burhani",      "SM Burhani",           "mdi.scale-balance",     {}),
        ("rahntech",        "RahnTech",             "mdi.devices",           {}),
    ]),
    ("FUEL CONSUMPTION", [
        ("diesel_cash",     "Diesel Cash",   "mdi.gas-station-outline", {}),
        ("infinity",        "Infinity",      "mdi.gas-station",  {}),
        ("lake_zambia",     "Lake Zambia",   "mdi.water-pump",   {}),
        ("lake_tunduma",    "Lake Tunduma",  "mdi.water-pump",   {}),
        ("gbp_diesel",      "GBP Diesel",    "mdi.fuel",         {}),
    ]),
    ("MANAGE", [
        ("manage_categories", "Categories",       "mdi.tag-edit",        {}),
        ("manage_diesel",     "Diesel Stations",  "mdi.gas-station",     {}),
        ("manage_recon",      "Recon. Stations",  "mdi.office-building", {}),
        ("manage_separate",   "Separate Expenses","mdi.view-list",       {}),
        ("manage_trucks",     "Trucks",           "mdi.truck",           {}),
        ("manage_trailers",   "Trailers",         "mdi.truck-trailer",   {}),
    ]),
]

# Sidebar keys that can host user-created sub-tables.
_EXPANDABLE_KEYS = {"mileage"}

# Sidebar keys with a fixed (non-editable) set of children.
#   key -> [(display name, match/route id, mdi icon), ...]
_FIXED_CHILDREN: dict[str, list[tuple[str, str, str]]] = {
    "sm_burhani": [
        ("RPA Schedule", "rpa_schedule", "mdi.file-table-outline"),
        ("Bonds",        "bonds",        "mdi.bank-outline"),
    ],
}


# ── Icon helper ───────────────────────────────────────────────────────────────

def _qta(name: str, color: str) -> QIcon:
    try:
        return qta.icon(name, color=color)
    except Exception:
        return qta.icon("mdi.circle-small", color=color)


# ── NavItem ───────────────────────────────────────────────────────────────────

class _NavItem(QWidget):
    """Single nav row: indicator | icon | label | [badge] | [chevron]."""

    activated = Signal(str)
    toggle_requested = Signal(str)   # chevron clicked (expandable items only)

    def __init__(
        self,
        key: str,
        label: str,
        icon_name: str,
        badge: bool = False,
        expandable: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self._icon_name = icon_name
        self._active = False
        self._has_badge = badge
        self._expandable = expandable
        self._expanded = False

        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self._build(label, badge, expandable)
        self._paint(hover=False)

    # ── Build ──────────────────────────────────────────────────────────────

    def _build(self, label: str, badge: bool, expandable: bool) -> None:
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 8, 0)
        hl.setSpacing(0)

        self._indicator = QFrame()
        self._indicator.setFixedWidth(3)
        self._indicator.setStyleSheet("background: transparent;")
        hl.addWidget(self._indicator)

        hl.addSpacing(10)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(18, 18)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setStyleSheet("background: transparent;")
        hl.addWidget(self._icon_lbl)

        hl.addSpacing(10)

        self._text_lbl = QLabel(label)
        self._text_lbl.setStyleSheet(
            f"color: {_MUTED}; font-size: 13px;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        self._text_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl.addWidget(self._text_lbl)

        self._badge_lbl: Optional[QLabel] = None
        if badge:
            self._badge_lbl = QLabel("12")
            self._badge_lbl.setAlignment(Qt.AlignCenter)
            self._badge_lbl.setFixedHeight(18)
            self._badge_lbl.setMinimumWidth(24)
            self._badge_lbl.setStyleSheet(
                f"background: {_RED}; color: #ffffff; font-size: 10px;"
                " font-weight: 700; border-radius: 9px; padding: 0 5px;"
                " font-family: 'Segoe UI', sans-serif;"
            )
            hl.addWidget(self._badge_lbl)
            hl.addSpacing(4)

        self._chevron_lbl: Optional[QLabel] = None
        if expandable:
            self._chevron_lbl = QLabel()
            self._chevron_lbl.setFixedSize(16, 16)
            self._chevron_lbl.setAlignment(Qt.AlignCenter)
            self._chevron_lbl.setStyleSheet("background: transparent;")
            self._chevron_lbl.setPixmap(_qta("mdi.chevron-right", color=_MUTED).pixmap(14, 14))
            hl.addWidget(self._chevron_lbl)

        self._refresh_icon(active=False)

    # ── State setters ──────────────────────────────────────────────────────

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint(hover=False)
        self._refresh_icon(active=active)

    def set_collapsed(self, collapsed: bool) -> None:
        self._text_lbl.setVisible(not collapsed)
        if self._badge_lbl:
            self._badge_lbl.setVisible(not collapsed)
        if self._chevron_lbl:
            self._chevron_lbl.setVisible(not collapsed)

    def set_badge(self, count: int) -> None:
        if self._badge_lbl:
            self._badge_lbl.setText(str(count))
            self._badge_lbl.setVisible(count > 0)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        if self._chevron_lbl:
            icon = "mdi.chevron-down" if expanded else "mdi.chevron-right"
            self._chevron_lbl.setPixmap(_qta(icon, color=_MUTED).pixmap(14, 14))

    # ── Internal ───────────────────────────────────────────────────────────

    def _refresh_icon(self, active: bool) -> None:
        color = _WHITE if active else _MUTED
        self._icon_lbl.setPixmap(_qta(self._icon_name, color=color).pixmap(18, 18))

    def _paint(self, hover: bool) -> None:
        if self._active:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet(f"background: {_BLUE};")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 13px; font-weight: 600;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
        elif hover:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 13px;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
        else:
            self.setStyleSheet("background: transparent;")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_MUTED}; font-size: 13px;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )

    # ── Qt events ──────────────────────────────────────────────────────────

    def enterEvent(self, event) -> None:
        if not self._active:
            self._paint(hover=True)
            self._refresh_icon(active=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._active:
            self._paint(hover=False)
            self._refresh_icon(active=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # Clicking the chevron area toggles expansion; elsewhere navigates.
            if (self._expandable and self._chevron_lbl is not None
                    and self._chevron_lbl.isVisible()
                    and event.position().x() >= self._chevron_lbl.x() - 4):
                self.toggle_requested.emit(self._key)
            else:
                self.activated.emit(self._key)
        super().mousePressEvent(event)


# ── SubNavItem ────────────────────────────────────────────────────────────────

class _SubNavItem(QWidget):
    """Indented sub-table row beneath an expandable parent."""

    activated = Signal(object)        # passes self
    delete_requested = Signal(object) # passes self (not emitted for add rows)

    def __init__(
        self,
        parent_key: str,
        parent_category: str,
        name: str,
        match: str = "",
        is_add: bool = False,
        sub_id=None,
        deletable: bool = True,
        icon_name: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.parent_key = parent_key
        self.parent_category = parent_category
        self.name = name
        self.match = match or name
        self.is_add = is_add
        self.sub_id = sub_id
        self._icon_name = icon_name
        self._active = False

        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        self._build()
        if not is_add and deletable:
            self.setContextMenuPolicy(Qt.CustomContextMenu)
            self.customContextMenuRequested.connect(self._menu)
        self._paint(hover=False)

    def _build(self) -> None:
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 8, 0)
        hl.setSpacing(0)

        self._indicator = QFrame()
        self._indicator.setFixedWidth(3)
        self._indicator.setStyleSheet("background: transparent;")
        hl.addWidget(self._indicator)

        hl.addSpacing(38)   # indent under the parent icon+label

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(15, 15)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setStyleSheet("background: transparent;")
        if self.is_add:
            icon = "mdi.plus-circle-outline"
        else:
            icon = self._icon_name or "mdi.circle-medium"
        self._icon_lbl.setPixmap(_qta(icon, color=_BLUE if self.is_add else _MUTED).pixmap(15, 15))
        hl.addWidget(self._icon_lbl)

        hl.addSpacing(8)

        self._text_lbl = QLabel(self.name)
        color = _BLUE if self.is_add else _MUTED
        self._text_lbl.setStyleSheet(
            f"color: {color}; font-size: 12px;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        self._text_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl.addWidget(self._text_lbl)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint(hover=False)

    def _refresh_icon(self, bright: bool) -> None:
        if self.is_add:
            return
        icon = self._icon_name or "mdi.circle-medium"
        self._icon_lbl.setPixmap(
            _qta(icon, color=_WHITE if bright else _MUTED).pixmap(15, 15)
        )

    def _paint(self, hover: bool) -> None:
        if self.is_add:
            self.setStyleSheet(f"background: {_SUB_BG};")
            self._indicator.setStyleSheet("background: transparent;")
            return
        if self._active:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet(f"background: {_BLUE};")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 12px; font-weight: 600;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
        elif hover:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 12px;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
        else:
            self.setStyleSheet(f"background: {_SUB_BG};")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_MUTED}; font-size: 12px;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
        self._refresh_icon(bright=self._active or hover)

    def enterEvent(self, event) -> None:
        if not self._active and not self.is_add:
            self._paint(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._active and not self.is_add:
            self._paint(hover=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self)
        super().mousePressEvent(event)

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        act = menu.addAction("Delete sub-table")
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == act:
            self.delete_requested.emit(self)


# ── Add Sub-table dialog ──────────────────────────────────────────────────────

class _AddSubTableDialog(QDialog):
    def __init__(self, parent_label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Add Sub-table — {parent_label}")
        self.setMinimumWidth(380)
        self.setStyleSheet("background: #FFFFFF;")
        self.result_name: Optional[str] = None

        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 20)
        vl.setSpacing(10)

        info = QLabel(
            f"Create a sub-table under “{parent_label}”. It shows the verified "
            "rows whose Description matches this name (e.g. a route like "
            "“Dar to Congo”)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #6B7280; font-size: 12px;")
        vl.addWidget(info)

        vl.addWidget(QLabel("Sub-table name *"))
        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. Dar to Congo")
        self._name.setStyleSheet(
            "QLineEdit { border: 1px solid #E5E7EB; border-radius: 5px;"
            " padding: 6px 8px; font-size: 13px; }"
        )
        self._name.returnPressed.connect(self._accept)
        vl.addWidget(self._name)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        cancel.setStyleSheet(
            "QPushButton { background: #FFFFFF; border: 1px solid #E5E7EB;"
            " border-radius: 5px; padding: 6px 14px; }"
        )
        btn_row.addWidget(cancel)
        create = QPushButton("Create")
        create.clicked.connect(self._accept)
        create.setStyleSheet(
            "QPushButton { background: #0077C5; color: #FFF; border: none;"
            " border-radius: 5px; padding: 6px 16px; font-weight: 600; }"
        )
        btn_row.addWidget(create)
        vl.addLayout(btn_row)

    def _accept(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Please enter a name.")
            return
        self.result_name = name
        self.accept()


# ── SectionLabel ──────────────────────────────────────────────────────────────

class _SectionLabel(QLabel):
    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text.upper(), parent)
        self.setStyleSheet(
            f"color: {_MUTED}; font-size: 10px; font-weight: 600;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
            " padding: 6px 16px 2px 16px; letter-spacing: 1px;"
        )


# ── SidebarWidget ─────────────────────────────────────────────────────────────

class SidebarWidget(QFrame):
    """Collapsible sidebar with nav sections, expandable categories and badges."""

    nav_selected = Signal(str)
    # (parent_key, parent_category, name, match)
    subtable_selected = Signal(str, str, str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._collapsed = False
        self._items: Dict[str, _NavItem] = {}
        self._section_labels: List[_SectionLabel] = []
        self._separators: List[QFrame] = []
        self._child_containers: Dict[str, QWidget] = {}
        self._expanded: set[str] = set()
        self._loaded: set[str] = set()
        self._active_obj = None            # active _NavItem or _SubNavItem
        self._active_key: Optional[str] = None
        self._build()
        self.select("overview")

    # ── Build ──────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setObjectName("accountantSidebar")
        self.setFixedWidth(EXPANDED_W)
        self.setStyleSheet(
            f"QFrame#accountantSidebar {{ background: {_NAVY}; border: none; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea      {{ background: {_NAVY}; border: none; }}
            QWidget          {{ background: {_NAVY}; }}
            QScrollBar:vertical {{
                background: {_NAVY};
                width: 4px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(148,163,184,0.25);
                border-radius: 2px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        container = QWidget()
        vl = QVBoxLayout(container)
        vl.setContentsMargins(0, 6, 0, 12)
        vl.setSpacing(0)

        for section_label, items in _SECTIONS:
            if section_label is not None:
                sep = QFrame()
                sep.setFrameShape(QFrame.HLine)
                sep.setFixedHeight(1)
                sep.setStyleSheet("background: rgba(148,163,184,0.12);")
                vl.addSpacing(6)
                vl.addWidget(sep)
                self._separators.append(sep)

                lbl = _SectionLabel(section_label)
                vl.addWidget(lbl)
                self._section_labels.append(lbl)

            for key, label, icon_name, opts in items:
                expandable = key in _EXPANDABLE_KEYS or key in _FIXED_CHILDREN
                nav = _NavItem(
                    key=key,
                    label=label,
                    icon_name=icon_name,
                    badge=opts.get("badge", False),
                    expandable=expandable,
                )
                nav.activated.connect(self._on_item_clicked)
                nav.toggle_requested.connect(self._on_toggle)
                self._items[key] = nav
                vl.addWidget(nav)

                if expandable:
                    child = QWidget()
                    child.setStyleSheet(f"background: {_SUB_BG};")
                    cvl = QVBoxLayout(child)
                    cvl.setContentsMargins(0, 0, 0, 0)
                    cvl.setSpacing(0)
                    child.setVisible(False)
                    self._child_containers[key] = child
                    vl.addWidget(child)

        vl.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        bottom_sep = QFrame()
        bottom_sep.setFrameShape(QFrame.HLine)
        bottom_sep.setFixedHeight(1)
        bottom_sep.setStyleSheet("background: rgba(148,163,184,0.15);")
        root.addWidget(bottom_sep)

        self._collapse_btn = QToolButton()
        self._collapse_btn.setFixedHeight(40)
        self._collapse_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._collapse_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._collapse_btn.setIcon(_qta("mdi.chevron-left", color=_MUTED))
        self._collapse_btn.setIconSize(QSize(16, 16))
        self._collapse_btn.setText("  Collapse")
        self._collapse_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: none;
                color: {_MUTED};
                font-size: 12px;
                font-family: 'Segoe UI', sans-serif;
                padding: 0 14px;
                text-align: left;
            }}
            QToolButton:hover {{
                background: {_ACTIVE_BG};
                color: {_WHITE};
            }}
        """)
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.clicked.connect(self.toggle_collapsed)
        root.addWidget(self._collapse_btn)

    # ── Public API ─────────────────────────────────────────────────────────

    def _clear_active(self) -> None:
        if self._active_obj is not None:
            try:
                self._active_obj.set_active(False)
            except RuntimeError:
                pass  # underlying Qt object already deleted
        self._active_obj = None

    def select(self, key: str) -> None:
        """Activate a top-level nav item by key (clears any sub-table active)."""
        self._clear_active()
        self._active_key = key
        if key in self._items:
            self._items[key].set_active(True)
            self._active_obj = self._items[key]

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        target_w = COLLAPSED_W if self._collapsed else EXPANDED_W
        self.setFixedWidth(target_w)
        for item in self._items.values():
            item.set_collapsed(self._collapsed)
        for lbl in self._section_labels:
            lbl.setVisible(not self._collapsed)
        for sep in self._separators:
            sep.setVisible(not self._collapsed)
        # Hide sub-table strips while collapsed; restore expanded ones after.
        for key, child in self._child_containers.items():
            child.setVisible(not self._collapsed and key in self._expanded)
        if self._collapsed:
            self._collapse_btn.setText("")
            self._collapse_btn.setIcon(_qta("mdi.chevron-right", color=_MUTED))
            self._collapse_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        else:
            self._collapse_btn.setText("  Collapse")
            self._collapse_btn.setIcon(_qta("mdi.chevron-left", color=_MUTED))
            self._collapse_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

    def set_verify_badge(self, count: int) -> None:
        if "verify" in self._items:
            self._items["verify"].set_badge(count)

    # ── Internal: top-level nav ────────────────────────────────────────────

    def _on_item_clicked(self, key: str) -> None:
        self.select(key)
        self.nav_selected.emit(key)
        # Auto-expand a category when its parent is opened.
        if key in self._child_containers and key not in self._expanded:
            self._on_toggle(key)

    # ── Internal: expand / collapse ────────────────────────────────────────

    def _on_toggle(self, key: str) -> None:
        if self._collapsed or key not in self._child_containers:
            return
        if key in self._expanded:
            self._expanded.discard(key)
            self._child_containers[key].setVisible(False)
            self._items[key].set_expanded(False)
        else:
            self._expanded.add(key)
            self._items[key].set_expanded(True)
            self._child_containers[key].setVisible(True)
            if key not in self._loaded:
                asyncio.ensure_future(self._load_children(key))

    async def _load_children(self, key: str) -> None:
        if key in _FIXED_CHILDREN:
            self._loaded.add(key)
            self._build_fixed_children(key)
            return
        from tahmeed.services.subtable_service import get_subtables
        try:
            subs = await get_subtables(key)
        except Exception:
            subs = []
        self._loaded.add(key)
        self._rebuild_children(key, subs)

    def _build_fixed_children(self, key: str) -> None:
        """Render the fixed (non-editable) children for keys like SM Burhani."""
        container = self._child_containers[key]
        layout = container.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        parent_category = self._items[key]._text_lbl.text()
        for name, match, icon_name in _FIXED_CHILDREN[key]:
            row = _SubNavItem(
                parent_key=key,
                parent_category=parent_category,
                name=name,
                match=match,
                deletable=False,
                icon_name=icon_name,
            )
            row.activated.connect(self._on_subitem_clicked)
            layout.addWidget(row)

    def _rebuild_children(self, key: str, subs) -> None:
        container = self._child_containers[key]
        layout = container.layout()
        # Clear existing rows
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        parent_category = CATEGORY_DEFS.get(key, (self._items[key]._text_lbl.text(),))[0]

        for sub in subs:
            row = _SubNavItem(
                parent_key=key,
                parent_category=sub.parent_category or parent_category,
                name=sub.name,
                match=sub.match,
                sub_id=sub._id,
            )
            row.activated.connect(self._on_subitem_clicked)
            row.delete_requested.connect(self._on_subitem_delete)
            layout.addWidget(row)

        add_row = _SubNavItem(
            parent_key=key,
            parent_category=parent_category,
            name="Add Sub-table",
            is_add=True,
        )
        add_row.activated.connect(self._on_add_clicked)
        layout.addWidget(add_row)

    # ── Internal: sub-item actions ─────────────────────────────────────────

    def _on_subitem_clicked(self, row: _SubNavItem) -> None:
        self._clear_active()
        row.set_active(True)
        self._active_obj = row
        self._active_key = None
        self.subtable_selected.emit(
            row.parent_key, row.parent_category, row.name, row.match
        )

    def _on_add_clicked(self, row: _SubNavItem) -> None:
        parent_label = self._items[row.parent_key]._text_lbl.text()
        dlg = _AddSubTableDialog(parent_label, parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.result_name:
            asyncio.ensure_future(
                self._create_child(row.parent_key, row.parent_category, dlg.result_name)
            )

    async def _create_child(self, key: str, parent_category: str, name: str) -> None:
        from tahmeed.services.subtable_service import create_subtable, get_subtables
        try:
            await create_subtable(key, parent_category, name)
            subs = await get_subtables(key)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not create sub-table:\n{exc}")
            return
        self._rebuild_children(key, subs)
        # Open the newly-created sub-table.
        self.subtable_selected.emit(key, parent_category, name, name)

    def _on_subitem_delete(self, row: _SubNavItem) -> None:
        if QMessageBox.question(
            self, "Delete sub-table",
            f"Delete sub-table “{row.name}”?\n\n(The transactions are not affected.)",
        ) != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._delete_child(row))

    async def _delete_child(self, row: _SubNavItem) -> None:
        from tahmeed.services.subtable_service import delete_subtable, get_subtables
        try:
            if row.sub_id is not None:
                await delete_subtable(row.sub_id)
            subs = await get_subtables(row.parent_key)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not delete sub-table:\n{exc}")
            return
        self._rebuild_children(row.parent_key, subs)
        # Fall back to the parent (all-rows) view.
        self._on_item_clicked(row.parent_key)
