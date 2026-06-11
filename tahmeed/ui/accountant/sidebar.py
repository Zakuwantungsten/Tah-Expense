"""AccountantDashboard — Collapsible sidebar navigation."""

from __future__ import annotations
from typing import Optional, Dict, List

import qtawesome as qta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QToolButton, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon

# ── Design tokens ─────────────────────────────────────────────────────────────

_NAVY       = "#1B2B4B"
_ACTIVE_BG  = "#253A5C"
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
        ("mileage",         "Mileage",                "mdi.road-variant",                 {"chevron": True}),
        ("latra",           "LATRA",                  "mdi.card-account-details-outline", {}),
        ("c28",             "C28",                    "mdi.file-document-outline",        {}),
        ("c40",             "C40",                    "mdi.file-document-outline",        {}),
        ("carbon_permit",   "Carbon & Permit",        "mdi.leaf",                         {}),
        ("diesel_cash",     "Diesel Cash",            "mdi.gas-station-outline",          {}),
        ("council_fees",    "Council Fees",           "mdi.city-variant",                 {"chevron": True}),
        ("return_weigh",    "Return & Weighbridge",   "mdi.scale",                        {}),
        ("parking_petroda", "Parking Petroda",        "mdi.parking",                      {}),
        ("backload",        "Backload Facilitation",  "mdi.truck-delivery",               {}),
        ("rope_sealing",    "Rope & Sealing",         "mdi.link-variant",                 {}),
        ("radiation",       "Radiation Taxes",        "mdi.radioactive",                  {}),
        ("health_fee",      "Health Fee",             "mdi.hospital-box",                 {}),
        ("halmashauri",     "Halmashauri Parking",    "mdi.parking",                      {}),
    ]),
    ("DIESEL STATIONS", [
        ("infinity",        "Infinity",      "mdi.gas-station",  {}),
        ("lake_zambia",     "Lake Zambia",   "mdi.water-pump",   {}),
        ("lake_tunduma",    "Lake Tunduma",  "mdi.water-pump",   {}),
        ("gbp_diesel",      "GBP Diesel",    "mdi.fuel",         {}),
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
    ]),
    ("RECONCILIATION", [
        ("sm_burhani",      "SM Burhani",    "mdi.scale-balance",  {"chevron": True}),
        ("rahntech",        "RahnTech",      "mdi.devices",         {}),
    ]),
    ("MANAGE", [
        ("manage_categories", "Categories",       "mdi.tag-edit",        {}),
        ("manage_diesel",     "Diesel Stations",  "mdi.gas-station",     {}),
        ("manage_recon",      "Recon. Stations",  "mdi.office-building", {}),
        ("manage_separate",   "Separate Expenses","mdi.view-list",       {}),
    ]),
]


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

    def __init__(
        self,
        key: str,
        label: str,
        icon_name: str,
        badge: bool = False,
        has_chevron: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self._icon_name = icon_name
        self._active = False
        self._has_badge = badge

        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self._build(label, badge, has_chevron)
        self._paint(hover=False)

    # ── Build ──────────────────────────────────────────────────────────────

    def _build(self, label: str, badge: bool, has_chevron: bool) -> None:
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 8, 0)
        hl.setSpacing(0)

        # 3-px active indicator on the left edge
        self._indicator = QFrame()
        self._indicator.setFixedWidth(3)
        self._indicator.setStyleSheet("background: transparent;")
        hl.addWidget(self._indicator)

        hl.addSpacing(10)

        # Icon
        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(18, 18)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setStyleSheet("background: transparent;")
        hl.addWidget(self._icon_lbl)

        hl.addSpacing(10)

        # Text label
        self._text_lbl = QLabel(label)
        self._text_lbl.setStyleSheet(
            f"color: {_MUTED}; font-size: 13px;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        self._text_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl.addWidget(self._text_lbl)

        # Badge
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

        # Chevron
        self._chevron_lbl: Optional[QLabel] = None
        if has_chevron:
            self._chevron_lbl = QLabel()
            self._chevron_lbl.setFixedSize(14, 14)
            self._chevron_lbl.setAlignment(Qt.AlignCenter)
            self._chevron_lbl.setStyleSheet("background: transparent;")
            self._chevron_lbl.setPixmap(
                _qta("mdi.chevron-right", color=_MUTED).pixmap(12, 12)
            )
            hl.addWidget(self._chevron_lbl)

        # Apply initial icon
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
            self.activated.emit(self._key)
        super().mousePressEvent(event)


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
    """Collapsible sidebar with nav sections, active state, and badge support."""

    nav_selected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._collapsed = False
        self._items: Dict[str, _NavItem] = {}
        self._section_labels: List[_SectionLabel] = []
        self._separators: List[QFrame] = []
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

        # ── Scroll area ──
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
                nav = _NavItem(
                    key=key,
                    label=label,
                    icon_name=icon_name,
                    badge=opts.get("badge", False),
                    has_chevron=opts.get("chevron", False),
                )
                nav.activated.connect(self._on_item_clicked)
                self._items[key] = nav
                vl.addWidget(nav)

        vl.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        # ── Collapse button ──
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

    def select(self, key: str) -> None:
        if self._active_key and self._active_key in self._items:
            self._items[self._active_key].set_active(False)
        self._active_key = key
        if key in self._items:
            self._items[key].set_active(True)

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

    # ── Internal ───────────────────────────────────────────────────────────

    def _on_item_clicked(self, key: str) -> None:
        self.select(key)
        self.nav_selected.emit(key)
