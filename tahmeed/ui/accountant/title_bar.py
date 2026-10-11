"""QuickBooks-style custom title bar for the accountant shell."""

from __future__ import annotations

from typing import Optional

import qtawesome as qta
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from tahmeed.config import APP_NAME, APP_VERSION, COMPANY_NAME
from tahmeed.models.user import User

_NAVY = "#1B2B4B"
_NAVY_HOVER = "#253A5C"
_CLOSE_HOVER = "#E81123"
_WHITE = "#F9FAFB"
_MUTED = "#94A3B8"
_TITLE_H = 32


class AccountantTitleBar(QFrame):
    """Dark title bar: company/product text + minimize / maximize / close."""

    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()

    def __init__(
        self,
        user: User,
        parent: Optional[QWidget] = None,
        *,
        edition: str = "Accountant Edition",
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._edition = edition
        self._press_global: Optional[QPoint] = None
        self._build()

    def _build(self) -> None:
        self.setObjectName("accountantTitleBar")
        self.setFixedHeight(_TITLE_H)
        self.setStyleSheet(
            f"QFrame#accountantTitleBar {{"
            f"  background: {_NAVY};"
            f"  border: none;"
            f"}}"
        )
        self.setCursor(Qt.ArrowCursor)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(10, 0, 0, 0)
        hl.setSpacing(0)

        title = QLabel(self._title_text())
        title.setObjectName("accountantTitleText")
        title.setStyleSheet(
            f"QLabel#accountantTitleText {{"
            f"  color: {_WHITE};"
            f"  font-size: 12px;"
            f"  font-family: 'Segoe UI';"
            f"  background: transparent;"
            f"}}"
        )
        title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        hl.addWidget(title, 1)

        hl.addWidget(self._win_btn(
            "mdi.minus", "Minimize", self.minimize_requested.emit, close=False,
        ))
        self._max_btn = self._win_btn(
            "mdi.window-maximize",
            "Maximize",
            self.maximize_requested.emit,
            close=False,
        )
        hl.addWidget(self._max_btn)
        hl.addWidget(self._win_btn(
            "mdi.close", "Close", self.close_requested.emit, close=True,
        ))

    def _title_text(self) -> str:
        name = (self._user.full_name or self._user.username or "").strip()
        who = f" ({name})" if name else ""
        return (
            f"{COMPANY_NAME} — {APP_NAME}: {self._edition} "
            f"{APP_VERSION}{who}"
        )

    def _win_btn(
        self,
        icon: str,
        tip: str,
        slot,
        *,
        close: bool,
    ) -> QToolButton:
        btn = QToolButton(self)
        btn.setIcon(qta.icon(icon, color=_MUTED))
        btn.setIconSize(QSize(14, 14))
        btn.setFixedSize(46, _TITLE_H)
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        hover = _CLOSE_HOVER if close else _NAVY_HOVER
        icon_hover = "#FFFFFF" if close else _WHITE
        btn.setStyleSheet(
            "QToolButton {"
            "  background: transparent; border: none;"
            "}"
            f"QToolButton:hover {{ background: {hover}; }}"
        )
        btn.clicked.connect(slot)
        btn.setProperty("closeBtn", close)
        btn.setProperty("iconHover", icon_hover)
        return btn

    def set_maximized(self, maximized: bool) -> None:
        icon = "mdi.window-restore" if maximized else "mdi.window-maximize"
        tip = "Restore" if maximized else "Maximize"
        self._max_btn.setIcon(qta.icon(icon, color=_MUTED))
        self._max_btn.setToolTip(tip)

    def _begin_system_move(self) -> bool:
        """Hand the drag to the OS (Aero Snap, drag-to-maximize, etc.)."""
        win = self.window()
        if win is None:
            return False
        handle = win.windowHandle()
        if handle is None:
            return False
        return bool(handle.startSystemMove())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            # Delay startSystemMove until the cursor moves so double-click
            # maximize still works. On Windows, MainWindow.nativeEvent often
            # returns HTCAPTION and owns the drag instead.
            self._press_global = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._press_global is not None
            and event.buttons() & Qt.LeftButton
        ):
            app = QApplication.instance()
            threshold = app.startDragDistance() if app is not None else 10
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() >= threshold:
                self._press_global = None
                if self._begin_system_move():
                    event.accept()
                    return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press_global = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._press_global = None
            self.maximize_requested.emit()
        super().mouseDoubleClickEvent(event)
