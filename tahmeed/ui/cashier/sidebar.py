"""CashierDashboard — Collapsible sidebar (navy palette matching accountant)."""

from __future__ import annotations
from typing import Optional, Dict, List

import qtawesome as qta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QToolButton, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QSize

from tahmeed.models.user import User

# ── Design tokens (identical to accountant sidebar) ──────────────────────────────
_NAVY       = "#1B2B4B"
_ACTIVE_BG  = "#253A5C"
_BLUE       = "#0077C5"
_WHITE      = "#F9FAFB"
_MUTED      = "#94A3B8"

EXPANDED_W  = 220
COLLAPSED_W = 56

# ── Category metadata — used by dashboard and category view ──────────────────────
CATEGORY_LABELS: dict[str, str] = {
    # Categories
    "mileage":         "Mileage",
    "latra":           "LATRA",
    "c28":             "C28",
    "c40":             "C40",
    "carbon_permit":   "Carbon & Permit",
    "council_fees":    "Council Fees",
    "return_weigh":    "Return & Weighbridge",
    "parking_petroda": "Parking Petroda",
    "backload":        "Backload Facilitation",
    "rope_sealing":    "Rope & Sealing",
    "radiation":       "Radiation Taxes",
    "health_fee":      "Health Fee",
    "halmashauri":     "Halmashauri Parking",
    # Separate expenses
    "toll_plaza":      "Toll Plaza",
    "parking_congo":   "Parking Congo",
    "congo_exp":       "Congo Expenses",
    "ahmed_kimvi":     "Ahmed Kimvi (Klesa)",
    "zambia_parking":  "Zambia Parking",
    "harrison":        "Harrison Expenses",
    "afritrack":       "Afritrack",
    "third_party":     "Third Party Covers",
    "comesa":          "COMESA Covers",
    "sm_burhani":      "SM Burhani",
    "rahntech":        "RahnTech",
    # Fuel consumption
    "diesel_cash":     "Diesel Cash",
    "infinity":        "Infinity",
    "lake_zambia":     "Lake Zambia",
    "lake_tunduma":    "Lake Tunduma",
    "gbp_diesel":      "GBP Diesel",
}

CATEGORY_ICONS: dict[str, str] = {
    "mileage":         "mdi.road-variant",
    "latra":           "mdi.card-account-details-outline",
    "c28":             "mdi.file-document-outline",
    "c40":             "mdi.file-document-outline",
    "carbon_permit":   "mdi.leaf",
    "council_fees":    "mdi.city-variant",
    "return_weigh":    "mdi.scale",
    "parking_petroda": "mdi.parking",
    "backload":        "mdi.truck-delivery",
    "rope_sealing":    "mdi.link-variant",
    "radiation":       "mdi.radioactive",
    "health_fee":      "mdi.hospital-box",
    "halmashauri":     "mdi.parking",
    "toll_plaza":      "mdi.boom-gate",
    "parking_congo":   "mdi.parking",
    "congo_exp":       "mdi.map-marker",
    "ahmed_kimvi":     "mdi.account-cash",
    "zambia_parking":  "mdi.map",
    "harrison":        "mdi.account-tie",
    "afritrack":       "mdi.satellite-variant",
    "third_party":     "mdi.shield-account",
    "comesa":          "mdi.certificate",
    "sm_burhani":      "mdi.scale-balance",
    "rahntech":        "mdi.devices",
    "diesel_cash":     "mdi.gas-station-outline",
    "infinity":        "mdi.gas-station",
    "lake_zambia":     "mdi.water-pump",
    "lake_tunduma":    "mdi.water-pump",
    "gbp_diesel":      "mdi.fuel",
}

# ── Nav sections ──────────────────────────────────────────────────────────────────
_SECTIONS: list[tuple[Optional[str], list[tuple]]] = [
    (None, [
        ("overview", "Overview", "mdi.view-dashboard-outline", {}),
        ("browse",   "Browse",   "mdi.magnify",                {}),
    ]),
    ("ENTRY", [
        ("table", "Table", "mdi.table-large", {}),
        ("form",  "Form",  "mdi.form-select", {}),
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
        ("diesel_cash",     "Diesel Cash",            "mdi.gas-station-outline",          {}),
    ]),
]


def _qta(name: str, color: str) -> "QIcon":
    try:
        return qta.icon(name, color=color)
    except Exception:
        return qta.icon("mdi.circle-small", color=color)


class _NavItem(QWidget):
    """Single nav row: indicator | icon | label."""

    activated = Signal(str)

    def __init__(self, key: str, label: str, icon_name: str, parent=None):
        super().__init__(parent)
        self._key = key
        self._icon_name = icon_name
        self._active = False
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self._build(label)
        self._paint(hover=False)

    def _build(self, label: str) -> None:
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

        self._refresh_icon(active=False)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint(hover=False)
        self._refresh_icon(active=active)

    def set_collapsed(self, collapsed: bool) -> None:
        self._text_lbl.setVisible(not collapsed)

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


class _SectionLabel(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setStyleSheet(
            f"color: {_MUTED}; font-size: 10px; font-weight: 600;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
            " padding: 6px 16px 2px 16px; letter-spacing: 1px;"
        )


class CashierSidebarWidget(QFrame):
    """Collapsible cashier sidebar — same visual style as accountant sidebar."""

    nav_selected = Signal(str)

    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        self._user = user
        self._collapsed = False
        self._items: Dict[str, _NavItem] = {}
        self._section_labels: List[_SectionLabel] = []
        self._separators: List[QFrame] = []
        self._active_item = None
        self._build()
        self.select("overview")

    def _build(self) -> None:
        self.setObjectName("cashierSidebar")
        self.setFixedWidth(EXPANDED_W)
        self.setStyleSheet(
            f"QFrame#cashierSidebar {{ background: {_NAVY}; border: none; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Scrollable nav ────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: {_NAVY}; border: none; }}
            QWidget      {{ background: {_NAVY}; }}
            QScrollBar:vertical {{
                background: {_NAVY}; width: 4px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(148,163,184,0.25);
                border-radius: 2px; min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        container = QWidget()
        vl = QVBoxLayout(container)
        vl.setContentsMargins(0, 6, 0, 6)
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

            for key, label, icon_name, _ in items:
                nav = _NavItem(key=key, label=label, icon_name=icon_name)
                nav.activated.connect(self._on_item_clicked)
                self._items[key] = nav
                vl.addWidget(nav)

        vl.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        # ── Collapse button ───────────────────────────────────────────────────
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
                background: transparent; border: none;
                color: {_MUTED}; font-size: 12px;
                font-family: 'Segoe UI', sans-serif;
                padding: 0 14px; text-align: left;
            }}
            QToolButton:hover {{ background: {_ACTIVE_BG}; color: {_WHITE}; }}
        """)
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.clicked.connect(self.toggle_collapsed)
        root.addWidget(self._collapse_btn)

    # ── Public API ─────────────────────────────────────────────────────────────

    def select(self, key: str) -> None:
        if self._active_item is not None:
            try:
                self._active_item.set_active(False)
            except RuntimeError:
                pass
        self._active_item = self._items.get(key)
        if self._active_item:
            self._active_item.set_active(True)

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        w = COLLAPSED_W if self._collapsed else EXPANDED_W
        self.setFixedWidth(w)
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

    # ── Internal ───────────────────────────────────────────────────────────────

    def _on_item_clicked(self, key: str) -> None:
        self.select(key)
        self.nav_selected.emit(key)
