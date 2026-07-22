"""AccountantDashboard — Top header bar (52 px)."""

from __future__ import annotations
from typing import Callable, Optional

import qtawesome as qta
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QToolButton, QWidget,
)
from PySide6.QtCore import Qt, QSize, Signal, QPoint
from PySide6.QtGui import QColor, QMouseEvent

from tahmeed.models.user import User

_NAVY  = "#1B2B4B"
_BLUE  = "#0077C5"
_GRAY  = "#6B7280"
_LIGHT = "#F4F6F8"
_HEADER_LIGHT = "#F5F7FA"
_BORDER = "#E0E0E0"


def _drop_shadow(widget: QWidget, blur: int = 12, dy: int = 2, alpha: int = 22) -> None:
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(15, 23, 42, alpha))
    widget.setGraphicsEffect(eff)


class _ProfileButton(QFrame):
    """Avatar + chevron cluster that opens a dropdown menu on click."""

    def __init__(
        self,
        menu: QMenu,
        tooltip: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._menu = menu
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setStyleSheet("QFrame { background: transparent; border: none; }")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._menu.exec(self.mapToGlobal(QPoint(0, self.height())))
        super().mousePressEvent(event)


class HeaderBar(QFrame):
    logout_requested = Signal()
    change_password_requested = Signal()

    def __init__(
        self,
        user: User,
        sidebar_toggle_fn: Optional[Callable] = None,
        dark: bool = False,
        show_search: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._toggle_fn = sidebar_toggle_fn
        self._dark = dark
        self._show_search = show_search
        self._build()

    def _build(self) -> None:
        dark = self._dark
        bg        = _NAVY if dark else _HEADER_LIGHT
        border    = "rgba(148,163,184,0.22)" if dark else _BORDER
        icon_col  = "#94A3B8" if dark else _GRAY
        hover_bg  = "#253A5C" if dark else "#EBEEF2"
        text_col  = "#F9FAFB" if dark else "#1B2B4B"
        avatar_ring = "rgba(255,255,255,0.30)" if dark else "rgba(0,119,197,0.22)"
        search_bg   = "#253A5C" if dark else "#FFFFFF"

        self.setObjectName("accountantHeader")
        self.setFixedHeight(52)
        self.setStyleSheet(
            f"QFrame#accountantHeader {{"
            f"  background: {bg};"
            f"  border-bottom: 1px solid {border};"
            f"}}"
        )
        _drop_shadow(self)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 18, 0)
        hl.setSpacing(0)

        # ── Hamburger ──
        hamburger = QToolButton()
        hamburger.setIcon(qta.icon("mdi.menu", color=icon_col))
        hamburger.setIconSize(QSize(20, 20))
        hamburger.setFixedSize(36, 36)
        hamburger.setToolTip("Toggle sidebar")
        hamburger.setStyleSheet(
            "QToolButton { background: transparent; border: none; border-radius: 4px; }"
            f"QToolButton:hover {{ background: {hover_bg}; }}"
        )
        hamburger.setCursor(Qt.PointingHandCursor)
        if self._toggle_fn:
            hamburger.clicked.connect(self._toggle_fn)
        hl.addWidget(hamburger)
        hl.addSpacing(12)

        # ── Divider after hamburger ──
        divider = QFrame()
        divider.setFixedSize(1, 24)
        divider.setStyleSheet(f"background: {border}; border: none;")
        hl.addWidget(divider)
        hl.addSpacing(12)

        # ── Logo circle ──
        logo = QLabel("T")
        logo.setFixedSize(30, 30)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(
            f"background: {_BLUE}; color: #ffffff; font-size: 14px;"
            " font-weight: 700; border-radius: 15px;"
            " font-family:'Segoe UI';"
        )
        hl.addWidget(logo)
        hl.addSpacing(10)

        # ── App name ──
        app_name = QLabel("Tahmeed Expense")
        app_name.setStyleSheet(
            f"color: {text_col}; font-size: 14px; font-weight: 600;"
            " letter-spacing: 0.4px; font-family:'Segoe UI'; background: transparent;"
        )
        hl.addWidget(app_name)

        hl.addStretch()

        # ── Search stub (optional) ──
        if self._show_search:
            search_wrap = QFrame()
            search_wrap.setObjectName("searchWrap")
            search_wrap.setFixedSize(300, 34)
            search_wrap.setStyleSheet(
                "QFrame#searchWrap {"
                f"  background: {search_bg};"
                f"  border: 1px solid {border};"
                "   border-radius: 6px;"
                "}"
            )
            sw = QHBoxLayout(search_wrap)
            sw.setContentsMargins(8, 0, 8, 0)
            sw.setSpacing(6)

            search_icon = QLabel()
            search_icon.setFixedSize(16, 16)
            search_icon.setPixmap(qta.icon("mdi.magnify", color="#9CA3AF").pixmap(16, 16))
            search_icon.setStyleSheet("background: transparent;")

            search_input = QLineEdit()
            search_input.setPlaceholderText("Search trucks, descriptions, amounts…")
            search_input.setEnabled(False)
            search_input.setFrame(False)
            search_input.setStyleSheet(
                "QLineEdit {"
                "  background: transparent;"
                "  border: none;"
                "  font-size: 12px;"
                "  color: #6B7280;"
                "  font-family:'Segoe UI';"
                "}"
            )
            sw.addWidget(search_icon)
            sw.addWidget(search_input)

            hl.addWidget(search_wrap)
            hl.addSpacing(12)

        # ── Avatar + chevron (opens profile menu) ──
        initials = "".join(p[0].upper() for p in self._user.full_name.split()[:2]) or "AC"

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu {"
            f"  background: {bg}; border: 1px solid {border};"
            "   border-radius: 6px; padding: 4px;"
            "}"
            "QMenu::item {"
            f"  color: {text_col}; font-size: 13px; font-family:'Segoe UI';"
            "   padding: 7px 18px 7px 12px; border-radius: 4px;"
            "}"
            f"QMenu::item:selected {{ background: {hover_bg}; }}"
            f"QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}"
        )

        change_pw_action = menu.addAction(
            qta.icon("mdi.lock-reset", color=icon_col), "Change Password"
        )
        change_pw_action.triggered.connect(self.change_password_requested)

        menu.addSeparator()

        logout_action = menu.addAction(
            qta.icon("mdi.logout", color="#EF4444"), "Log Out"
        )
        logout_action.triggered.connect(self.logout_requested)

        profile = _ProfileButton(menu, self._user.full_name)
        phl = QHBoxLayout(profile)
        phl.setContentsMargins(0, 0, 0, 0)
        phl.setSpacing(6)

        avatar = QLabel(initials)
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"background: {_BLUE}; color: #ffffff; font-size: 12px;"
            f" font-weight: 700; border: 2px solid {avatar_ring}; border-radius: 16px;"
            " font-family:'Segoe UI';"
        )

        chevron = QLabel()
        chevron.setFixedSize(14, 14)
        chevron.setPixmap(qta.icon("mdi.chevron-down", color=icon_col).pixmap(14, 14))
        chevron.setStyleSheet("background: transparent;")

        phl.addWidget(avatar)
        phl.addWidget(chevron)
        hl.addWidget(profile)
