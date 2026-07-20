"""AccountantDashboard — Top header bar (52 px)."""

from __future__ import annotations
from typing import Callable, Optional

import qtawesome as qta
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QToolButton, QWidget,
)
from PySide6.QtCore import Qt, QSize, Signal

from tahmeed.models.user import User

_BLUE  = "#0077C5"
_GRAY  = "#6B7280"
_LIGHT = "#F4F6F8"
_BORDER = "#E5E7EB"


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
        bg        = "#1B2B4B" if dark else "#FFFFFF"
        border    = "rgba(148,163,184,0.15)" if dark else _BORDER
        icon_col  = "#94A3B8" if dark else _GRAY
        hover_bg  = "#253A5C" if dark else _LIGHT
        text_col  = "#F9FAFB" if dark else "#111827"

        self.setObjectName("accountantHeader")
        self.setFixedHeight(52)
        self.setStyleSheet(
            f"QFrame#accountantHeader {{"
            f"  background: {bg};"
            f"  border-bottom: 1px solid {border};"
            f"}}"
        )

        hl = QHBoxLayout(self)
        hl.setContentsMargins(14, 0, 16, 0)
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
        hl.addSpacing(10)

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
        hl.addSpacing(9)

        # ── App name ──
        app_name = QLabel("Tahmeed Expense")
        app_name.setStyleSheet(
            f"color: {text_col}; font-size: 15px; font-weight: 700;"
            " font-family:'Segoe UI'; background: transparent;"
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
                f"  background: {_LIGHT};"
                f"  border: 1px solid {_BORDER};"
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

        # ── Avatar + initials (opens profile menu) ──
        initials = "".join(p[0].upper() for p in self._user.full_name.split()[:2]) or "AC"
        avatar = QToolButton()
        avatar.setText(initials)
        avatar.setFixedSize(32, 32)
        avatar.setCursor(Qt.PointingHandCursor)
        avatar.setToolTip(self._user.full_name)
        avatar.setPopupMode(QToolButton.InstantPopup)
        avatar.setStyleSheet(
            "QToolButton {"
            f"  background: {_BLUE}; color: #ffffff; font-size: 12px;"
            "   font-weight: 700; border: none; border-radius: 16px;"
            "   font-family:'Segoe UI';"
            "}"
            "QToolButton::menu-indicator { image: none; width: 0; }"
        )

        menu = QMenu(avatar)
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

        avatar.setMenu(menu)
        hl.addWidget(avatar)
