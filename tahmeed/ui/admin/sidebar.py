"""Admin panel — light-mode collapsible sidebar with icon nav."""

from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# ── Light design tokens ───────────────────────────────────────────────────────
_BG = "#FFFFFF"
_BORDER = "#E5E7EB"
_ACTIVE_BG = "#EFF6FF"
_ACTIVE_TEXT = "#1D4ED8"
_INDICATOR = "#2563EB"
_MUTED = "#6B7280"
_HOVER_BG = "#F3F4F6"
_TITLE = "#111827"

EXPANDED_W = 220
COLLAPSED_W = 64

_NAV: list[tuple[str, str, str]] = [
    ("users", "Users", "mdi.account-multiple-outline"),
    ("settings", "Settings", "mdi.cog-outline"),
    ("security", "Security", "mdi.shield-lock-outline"),
    ("audit", "Audit Log", "mdi.clipboard-text-outline"),
    ("backups", "Backups", "mdi.cloud-upload-outline"),
]


def _icon(name: str, color: str):
    try:
        return qta.icon(name, color=color)
    except Exception:
        return qta.icon("mdi.circle-small", color=color)


class _NavItem(QWidget):
    activated = Signal(str)

    def __init__(self, key: str, label: str, icon_name: str, parent=None):
        super().__init__(parent)
        self._key = key
        self._icon_name = icon_name
        self._active = False
        self._collapsed = False
        self.setFixedHeight(40)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(0)

        self._left_stretch = QSpacerItem(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        lay.addSpacerItem(self._left_stretch)

        self._indicator = QFrame()
        self._indicator.setFixedWidth(3)
        self._indicator.setStyleSheet("background: transparent;")
        lay.addWidget(self._indicator)

        lay.addSpacing(12)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(20, 20)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._icon_lbl)

        lay.addSpacing(10)

        self._text = QLabel(label)
        self._text.setStyleSheet(
            f"color:{_MUTED};font-size:13px;font-weight:500;"
            "font-family:'Segoe UI',sans-serif;background:transparent;"
        )
        self._text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(self._text)

        self._paint(hover=False)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint(hover=False)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._text.setVisible(not collapsed)
        if collapsed:
            self._left_stretch.changeSize(1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum)
        else:
            self._left_stretch.changeSize(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.layout().invalidate()
        self._paint(hover=False)

    def _paint(self, *, hover: bool) -> None:
        if self._active:
            bg = _ACTIVE_BG
            color = _ACTIVE_TEXT
            ind = _INDICATOR
            weight = "600"
        elif hover:
            bg = _HOVER_BG
            color = _TITLE
            ind = "transparent"
            weight = "500"
        else:
            bg = "transparent"
            color = _MUTED
            ind = "transparent"
            weight = "500"

        self.setStyleSheet(f"background:{bg};border-radius:8px;")
        self._indicator.setStyleSheet(f"background:{ind};border-radius:1px;")
        self._text.setStyleSheet(
            f"color:{color};font-size:13px;font-weight:{weight};"
            "font-family:'Segoe UI',sans-serif;background:transparent;"
        )
        self._icon_lbl.setPixmap(_icon(self._icon_name, color).pixmap(18, 18))

    def enterEvent(self, event) -> None:
        if not self._active:
            self._paint(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._paint(hover=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self._key)
        super().mousePressEvent(event)


class AdminSidebar(QWidget):
    """Light sidebar: Users, Categories, Rules, Settings, Security, Audit, Backups."""

    navigate = Signal(str)
    logout_requested = Signal()

    def __init__(self, user_label: str, parent=None):
        super().__init__(parent)
        self._items: dict[str, _NavItem] = {}
        self._collapsed = False
        self.setFixedWidth(EXPANDED_W)
        self.setStyleSheet(
            f"AdminSidebar{{background:{_BG};border-right:1px solid {_BORDER};}}"
        )
        self._build(user_label)

    def _build(self, user_label: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 14, 10, 12)
        root.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 0, 8)
        header.setSpacing(8)

        self._brand_icon = QLabel()
        self._brand_icon.setFixedSize(22, 22)
        self._brand_icon.setPixmap(_icon("mdi.shield-account", _INDICATOR).pixmap(22, 22))
        self._brand_icon.setStyleSheet("background:transparent;")
        header.addWidget(self._brand_icon)

        self._brand = QLabel("Admin")
        self._brand.setStyleSheet(
            f"color:{_TITLE};font:700 15px 'Segoe UI';background:transparent;"
        )
        header.addWidget(self._brand)
        header.addStretch()

        self._collapse_btn = QToolButton()
        self._collapse_btn.setIcon(_icon("mdi.menu-open", _MUTED))
        self._collapse_btn.setIconSize(QSize(18, 18))
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.setStyleSheet(
            "QToolButton{background:transparent;border:none;padding:4px;}"
            f"QToolButton:hover{{background:{_HOVER_BG};border-radius:6px;}}"
        )
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        header.addWidget(self._collapse_btn)
        root.addLayout(header)

        self._user = QLabel(user_label)
        self._user.setWordWrap(True)
        self._user.setStyleSheet(
            f"color:{_MUTED};font:12px 'Segoe UI';background:transparent;"
            "padding:0 6px 10px 6px;"
        )
        root.addWidget(self._user)

        for key, label, icon_name in _NAV:
            item = _NavItem(key, label, icon_name)
            item.activated.connect(self._on_nav)
            self._items[key] = item
            root.addWidget(item)

        root.addStretch(1)

        self._logout = QToolButton()
        self._logout.setIcon(_icon("mdi.logout", "#DC2626"))
        self._logout.setText("  Log Out")
        self._logout.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._logout.setCursor(Qt.PointingHandCursor)
        self._logout.setStyleSheet(
            "QToolButton{background:transparent;border:none;color:#DC2626;"
            "font:600 12px 'Segoe UI';padding:8px 10px;text-align:left;}"
            "QToolButton:hover{background:#FEF2F2;border-radius:8px;}"
        )
        self._logout.clicked.connect(self.logout_requested)
        root.addWidget(self._logout)

    def _on_nav(self, key: str) -> None:
        self.set_active(key)
        self.navigate.emit(key)

    def set_active(self, key: str) -> None:
        for k, item in self._items.items():
            item.set_active(k == key)

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self.setFixedWidth(COLLAPSED_W if self._collapsed else EXPANDED_W)
        self._brand.setVisible(not self._collapsed)
        self._user.setVisible(not self._collapsed)
        self._collapse_btn.setIcon(
            _icon("mdi.menu" if self._collapsed else "mdi.menu-open", _MUTED)
        )
        if self._collapsed:
            self._logout.setText("")
            self._logout.setToolButtonStyle(Qt.ToolButtonIconOnly)
        else:
            self._logout.setText("  Log Out")
            self._logout.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for item in self._items.values():
            item.set_collapsed(self._collapsed)
