"""AccountantDashboard — Top header bar (52 px)."""

from __future__ import annotations
from typing import Callable, Optional

import qtawesome as qta
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QToolButton, QWidget,
)
from PySide6.QtCore import Qt, QSize

from tahmeed.models.user import User

_BLUE  = "#0077C5"
_GRAY  = "#6B7280"
_LIGHT = "#F4F6F8"
_BORDER = "#E5E7EB"


class HeaderBar(QFrame):
    def __init__(
        self,
        user: User,
        sidebar_toggle_fn: Optional[Callable] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._toggle_fn = sidebar_toggle_fn
        self._build()

    def _build(self) -> None:
        self.setObjectName("accountantHeader")
        self.setFixedHeight(52)
        self.setStyleSheet(
            "QFrame#accountantHeader {"
            "  background: #FFFFFF;"
            f"  border-bottom: 1px solid {_BORDER};"
            "}"
        )

        hl = QHBoxLayout(self)
        hl.setContentsMargins(14, 0, 16, 0)
        hl.setSpacing(0)

        # ── Hamburger ──
        hamburger = QToolButton()
        hamburger.setIcon(qta.icon("mdi.menu", color=_GRAY))
        hamburger.setIconSize(QSize(20, 20))
        hamburger.setFixedSize(36, 36)
        hamburger.setToolTip("Toggle sidebar")
        hamburger.setStyleSheet(
            "QToolButton { background: transparent; border: none; border-radius: 4px; }"
            "QToolButton:hover { background: #F4F6F8; }"
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
            " font-family: 'Segoe UI', sans-serif;"
        )
        hl.addWidget(logo)
        hl.addSpacing(9)

        # ── App name ──
        app_name = QLabel("Tahmeed Expense")
        app_name.setStyleSheet(
            "color: #111827; font-size: 15px; font-weight: 700;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        hl.addWidget(app_name)

        hl.addStretch()

        # ── Search stub ──
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
            "  font-family: 'Segoe UI', sans-serif;"
            "}"
        )
        sw.addWidget(search_icon)
        sw.addWidget(search_input)

        hl.addWidget(search_wrap)
        hl.addSpacing(12)

        # ── Bell with static badge ──
        bell_wrap = _BellWidget()
        hl.addWidget(bell_wrap)
        hl.addSpacing(10)

        # ── Avatar + initials ──
        initials = "".join(p[0].upper() for p in self._user.full_name.split()[:2]) or "AC"
        avatar = QLabel(initials)
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"background: {_BLUE}; color: #ffffff; font-size: 12px;"
            " font-weight: 700; border-radius: 16px;"
            " font-family: 'Segoe UI', sans-serif;"
        )
        avatar.setCursor(Qt.PointingHandCursor)
        hl.addWidget(avatar)


class _BellWidget(QWidget):
    """Bell icon with a red badge overlaid in the top-right corner."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(36, 36)

        btn = QToolButton(self)
        btn.setIcon(qta.icon("mdi.bell-outline", color=_GRAY))
        btn.setIconSize(QSize(20, 20))
        btn.setFixedSize(36, 36)
        btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; border-radius: 18px; }"
            "QToolButton:hover { background: #F4F6F8; }"
        )
        btn.setCursor(Qt.PointingHandCursor)
        btn.move(0, 0)

        self._badge = QLabel("3", self)
        self._badge.setFixedSize(16, 16)
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.setStyleSheet(
            "background: #DC2626; color: #ffffff; font-size: 9px;"
            " font-weight: 700; border-radius: 8px;"
            " font-family: 'Segoe UI', sans-serif;"
        )
        self._badge.move(19, 2)

    def set_count(self, count: int) -> None:
        self._badge.setText(str(count))
        self._badge.setVisible(count > 0)
