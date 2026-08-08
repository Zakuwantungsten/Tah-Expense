"""Offline / degraded-connection banner shown above dashboard body content."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from tahmeed.services.connectivity_service import ConnectivityStatus
from tahmeed.signals import app_signals


class ConnectivityBanner(QFrame):
    """Amber/red strip; hidden when both API and Mongo probes succeed."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("connectivityBanner")
        self.setFixedHeight(36)
        self.hide()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)

        self._icon = QLabel("●")
        self._icon.setStyleSheet(
            "color:#FFFFFF;font-size:10px;background:transparent;"
        )
        lay.addWidget(self._icon)

        self._label = QLabel("")
        self._label.setWordWrap(False)
        self._label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self._label.setStyleSheet(
            "color:#FFFFFF;font-size:12px;font-weight:600;"
            "font-family:'Segoe UI',sans-serif;background:transparent;"
        )
        lay.addWidget(self._label, 1)

        app_signals.connectivity_changed.connect(self.apply_status)

    def apply_status(self, status: object) -> None:
        if not isinstance(status, ConnectivityStatus):
            return
        if status.online:
            self.hide()
            return
        msg = status.banner_message()
        self._label.setText(msg)
        bg = "#B45309" if status.degraded else "#B91C1C"
        self.setStyleSheet(
            f"QFrame#connectivityBanner {{"
            f"  background:{bg}; border:none;"
            f"}}"
        )
        self.show()
