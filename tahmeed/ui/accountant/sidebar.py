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
    QScrollArea, QToolButton, QSizePolicy,
    QPushButton, QMessageBox, QMenu,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon

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
        ("truck_overview",  "Truck Overview",    "mdi.truck-fast-outline",     {}),
    ]),
    ("CASHIER FLOW", [
        ("verify",          "Verify",            "mdi.inbox-arrow-down",             {"badge": True}),
        ("master_expenses", "Master Expenses",   "mdi.table-large",                  {}),
    ]),
    # ITEMS are loaded dynamically from the DB (accountant-managed). The header
    # is rendered statically; the rows below it are built by _load_items().
    ("ITEMS", []),
    ("SEPARATE EXPENSES", [
        ("toll_plaza",      "Toll Plaza",           "mdi.boom-gate",         {}),
        ("parking_congo",   "Parking Congo",        "mdi.parking",           {}),
        ("congo_exp",       "Congo Expenses",       "mdi.map-marker",        {}),
        ("ahmed_kimvi",     "Ahmed Kimvi (Klesa)",  "mdi.account-cash",      {}),
        ("zambia_parking",  "Zambia Parking",       "mdi.map",               {}),
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
        ("manage_categories", "Items",             "mdi.tag-multiple-outline", {}),
        ("manage_trucks",     "Trucks",           "mdi.truck",           {}),
        ("manage_trailers",   "Trailers",         "mdi.truck-trailer",   {}),
        ("manage_users",      "Users",            "mdi.account-multiple-outline", {}),
        ("backup",            "Backups",          "mdi.database-export-outline", {}),
    ]),
]

# Dynamic ITEMS rows are always expandable (they can host sub-tables). Static
# sections have no expandable keys except those in _FIXED_CHILDREN.
_EXPANDABLE_KEYS: set[str] = set()

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
        self._has_subtables = False   # chevron only shown after DB confirms sub-tables exist
        self._is_collapsed = False

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
            " font-family:'Segoe UI'; background: transparent;"
        )
        self._text_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl.addWidget(self._text_lbl)

        self._badge_lbl: Optional[QLabel] = None
        if badge:
            self._badge_lbl = QLabel("0")
            self._badge_lbl.setAlignment(Qt.AlignCenter)
            self._badge_lbl.setFixedHeight(18)
            self._badge_lbl.setMinimumWidth(24)
            self._badge_lbl.setStyleSheet(
                f"background: {_RED}; color: #ffffff; font-size: 10px;"
                " font-weight: 700; border-radius: 9px; padding: 0 5px;"
                " font-family:'Segoe UI';"
            )
            self._badge_lbl.setVisible(False)
            hl.addWidget(self._badge_lbl)
            hl.addSpacing(4)

        self._chevron_lbl: Optional[QLabel] = None
        if expandable:
            self._chevron_lbl = QLabel()
            self._chevron_lbl.setFixedSize(16, 16)
            self._chevron_lbl.setAlignment(Qt.AlignCenter)
            self._chevron_lbl.setStyleSheet("background: transparent;")
            self._chevron_lbl.setPixmap(_qta("mdi.chevron-right", color=_MUTED).pixmap(14, 14))
            self._chevron_lbl.setVisible(False)   # hidden until sub-tables confirmed
            hl.addWidget(self._chevron_lbl)

        self._refresh_icon(active=False)

    # ── State setters ──────────────────────────────────────────────────────

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint(hover=False)
        self._refresh_icon(active=active)

    def set_has_subtables(self, has: bool) -> None:
        """Show or hide the chevron based on whether sub-tables exist in the DB."""
        self._has_subtables = has
        if self._chevron_lbl:
            self._chevron_lbl.setVisible(has and not self._is_collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._is_collapsed = collapsed
        self._text_lbl.setVisible(not collapsed)
        px = 26 if collapsed else 18
        self._icon_lbl.setFixedSize(px, px)
        self._refresh_icon(active=self._active)
        if self._badge_lbl:
            self._badge_lbl.setVisible(not collapsed)
        if self._chevron_lbl:
            # Only show when expanded AND this item actually has sub-tables
            self._chevron_lbl.setVisible(self._has_subtables and not collapsed)

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
        px = 26 if self._is_collapsed else 18
        self._icon_lbl.setPixmap(_qta(self._icon_name, color=color).pixmap(px, px))

    def _paint(self, hover: bool) -> None:
        if self._active:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet(f"background: {_BLUE};")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 13px; font-weight: 600;"
                " font-family:'Segoe UI'; background: transparent;"
            )
        elif hover:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 13px;"
                " font-family:'Segoe UI'; background: transparent;"
            )
        else:
            self.setStyleSheet("background: transparent;")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_MUTED}; font-size: 13px;"
                " font-family:'Segoe UI'; background: transparent;"
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
            " font-family:'Segoe UI'; background: transparent;"
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
                " font-family:'Segoe UI'; background: transparent;"
            )
        elif hover:
            self.setStyleSheet(f"background: {_ACTIVE_BG};")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 12px;"
                " font-family:'Segoe UI'; background: transparent;"
            )
        else:
            self.setStyleSheet(f"background: {_SUB_BG};")
            self._indicator.setStyleSheet("background: transparent;")
            self._text_lbl.setStyleSheet(
                f"color: {_MUTED}; font-size: 12px;"
                " font-family:'Segoe UI'; background: transparent;"
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


# ── SectionLabel ──────────────────────────────────────────────────────────────

class _SectionLabel(QLabel):
    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text.upper(), parent)
        self.setStyleSheet(
            f"color: {_MUTED}; font-size: 10px; font-weight: 600;"
            " font-family:'Segoe UI'; background: transparent;"
            " padding: 6px 16px 2px 16px; letter-spacing: 1px;"
        )


# ── SidebarWidget ─────────────────────────────────────────────────────────────

class SidebarWidget(QFrame):
    """Collapsible sidebar with nav sections, expandable categories and badges."""

    nav_selected = Signal(str)
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
        self._item_keys: set[str] = set()          # keys of dynamic ITEMS rows
        self._item_defs: Dict[str, tuple] = {}     # key -> (name, icon)
        self._items_host_vl = None                 # layout hosting dynamic rows
        self._build()
        self.select("overview")
        asyncio.ensure_future(self._load_items())
        for key in _FIXED_CHILDREN:
            if key in self._items:
                self._items[key].set_has_subtables(True)
            if key not in self._loaded:
                self._loaded.add(key)
                self._build_fixed_children(key)

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
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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

            if section_label == "ITEMS":
                # Host for dynamic, accountant-managed item rows.
                items_host = QWidget()
                items_host.setStyleSheet(f"background: {_NAVY};")
                self._items_host_vl = QVBoxLayout(items_host)
                self._items_host_vl.setContentsMargins(0, 0, 0, 0)
                self._items_host_vl.setSpacing(0)
                vl.addWidget(items_host)
                continue

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
                font-family:'Segoe UI';
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
        if key in _FIXED_CHILDREN:
            self.select(key)
            if key not in self._expanded:
                self._on_toggle(key)
            self._activate_first_fixed_child(key)
            return
        self.select(key)
        self.nav_selected.emit(key)
        # Auto-expand only when this item has confirmed sub-tables.
        nav = self._items.get(key)
        if (key in self._child_containers and key not in self._expanded
                and nav is not None and nav._has_subtables):
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
            if key not in self._loaded:
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

        if key in self._items:
            self._items[key].set_has_subtables(True)

    def _activate_first_fixed_child(self, key: str) -> None:
        """Select the first fixed sub-item under a parent like SM Burhani."""
        container = self._child_containers.get(key)
        if container is None:
            return
        layout = container.layout()
        if layout is None:
            return
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if isinstance(w, _SubNavItem) and not w.is_add:
                self._on_subitem_clicked(w)
                return

    def _rebuild_children(self, key: str, subs) -> None:
        container = self._child_containers[key]
        layout = container.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        parent_category = (
            self._items[key]._text_lbl.text() if key in self._items else key
        )

        # Update chevron visibility based on whether sub-tables exist.
        if key in self._items:
            self._items[key].set_has_subtables(len(subs) > 0)

        if not subs:
            # Collapse the strip — nothing left to show.
            self._expanded.discard(key)
            container.setVisible(False)
            if key in self._items:
                self._items[key].set_expanded(False)
            return

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

    # ── Internal: sub-item actions ─────────────────────────────────────────

    def _on_subitem_clicked(self, row: _SubNavItem) -> None:
        self._clear_active()
        row.set_active(True)
        self._active_obj = row
        self._active_key = None
        self.subtable_selected.emit(
            row.parent_key, row.parent_category, row.name, row.match
        )

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

    # ── Chevron state management ───────────────────────────────────────────────

    # ── Dynamic ITEMS rows (accountant-managed) ────────────────────────────────

    def item_def(self, key: str):
        """Return (name, icon) for a dynamic item key, or None."""
        return self._item_defs.get(key)

    def refresh_items(self) -> None:
        """Rebuild the dynamic ITEMS rows from the DB (call after Manage Items changes)."""
        asyncio.ensure_future(self._load_items())

    async def _load_items(self) -> None:
        from tahmeed.services.category_service import get_sidebar_categories
        try:
            cats = await get_sidebar_categories()
        except Exception:
            cats = []
        self._rebuild_items(cats)

    def _rebuild_items(self, cats: list) -> None:
        from tahmeed.services.category_service import item_key as _ik

        if self._items_host_vl is None:
            return

        # Drop previously-built dynamic rows from the shared registries.
        for key in self._item_keys:
            self._items.pop(key, None)
            self._child_containers.pop(key, None)
            self._expanded.discard(key)
            self._loaded.discard(key)
            self._item_defs.pop(key, None)
        self._item_keys = set()

        # Clear the host layout entirely.
        while self._items_host_vl.count():
            it = self._items_host_vl.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        if not cats:
            hint = QLabel("  No sidebar items yet")
            hint.setStyleSheet(
                f"color: {_MUTED}; font-size: 11px; font-style: italic;"
                " font-family:'Segoe UI'; background: transparent;"
                " padding: 4px 16px;"
            )
            self._items_host_vl.addWidget(hint)
            return

        for cat in cats:
            key = _ik(cat.name)
            if not key or key in self._item_keys:
                continue
            icon = cat.icon or "mdi.tag-outline"
            self._item_defs[key] = (cat.name, icon)
            self._item_keys.add(key)
            self._add_item_row(key, cat.name, icon)

        if self._collapsed:
            for key in self._item_keys:
                self._items[key].set_collapsed(True)

        asyncio.ensure_future(self._load_item_chevrons())

    def _add_item_row(self, key: str, label: str, icon_name: str) -> None:
        nav = _NavItem(key=key, label=label, icon_name=icon_name, expandable=True)
        nav.activated.connect(self._on_item_clicked)
        nav.toggle_requested.connect(self._on_toggle)
        self._items[key] = nav
        self._items_host_vl.addWidget(nav)

        child = QWidget()
        child.setStyleSheet(f"background: {_SUB_BG};")
        cvl = QVBoxLayout(child)
        cvl.setContentsMargins(0, 0, 0, 0)
        cvl.setSpacing(0)
        child.setVisible(False)
        self._child_containers[key] = child
        self._items_host_vl.addWidget(child)

    async def _load_item_chevrons(self) -> None:
        from tahmeed.services.subtable_service import get_subtables
        for key in list(self._item_keys):
            if key not in self._items:
                continue
            try:
                subs = await get_subtables(key)
                self._items[key].set_has_subtables(len(subs) > 0)
            except Exception:
                pass

    async def refresh_chevron(self, key: str) -> None:
        """Re-check a single item's sub-table count and update its chevron.
        Call this from Manage Items after adding or deleting sub-items.
        """
        from tahmeed.services.subtable_service import get_subtables
        if key not in self._items:
            return
        try:
            subs = await get_subtables(key)
            has = len(subs) > 0
            self._items[key].set_has_subtables(has)
            if not has and key in self._expanded:
                self._expanded.discard(key)
                if key in self._child_containers:
                    self._child_containers[key].setVisible(False)
                self._items[key].set_expanded(False)
        except Exception:
            pass

    def refresh_subitems(self, key: str) -> None:
        """Live-update one item's chevron + (if visible) its sub-item strip.

        Called from the dashboard whenever the accountant adds, edits, or
        deletes a sub-item in Manage Items, so the sidebar reflects it without
        an app restart.
        """
        asyncio.ensure_future(self._refresh_subitems(key))

    async def _refresh_subitems(self, key: str) -> None:
        if key not in self._items:
            return
        from tahmeed.services.subtable_service import get_subtables
        try:
            subs = await get_subtables(key)
        except Exception:
            return
        self._items[key].set_has_subtables(len(subs) > 0)
        # Rebuild the strip live if it's already been built/expanded.
        if key in self._loaded or key in self._expanded:
            self._loaded.add(key)
            self._rebuild_children(key, subs)
