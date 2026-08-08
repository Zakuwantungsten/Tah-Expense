"""Bottom status strip that reflects live ConnectivityStatus (not static chrome)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from tahmeed.services.connectivity_service import ConnectivityStatus, connectivity_monitor
from tahmeed.signals import app_signals


class LiveStatusBar(QFrame):
    """Shared connection status bar for cashier (dark) and accountant (light)."""

    def __init__(
        self,
        *,
        object_name: str,
        mode_label: str = "",
        dark: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFixedHeight(24)
        self._mode_label = mode_label
        self._dark = dark

        if dark:
            self.setStyleSheet(
                f"QFrame#{object_name} {{"
                "  background:#1B2B4B; border-top:1px solid rgba(148,163,184,0.15);"
                "}"
            )
            self._text_color = "#94A3B8"
        else:
            self.setStyleSheet(
                f"QFrame#{object_name} {{"
                "  background:#FFFFFF; border-top:1px solid #E5E7EB;"
                "}"
            )
            self._text_color = "#6B7280"

        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(0)

        self._dot = QLabel("●")
        self._dot.setStyleSheet(
            "color:#9CA3AF;font-size:9px;background:transparent;"
        )
        hl.addWidget(self._dot)
        hl.addSpacing(5)

        self._status = QLabel("Checking connection…")
        self._status.setStyleSheet(
            f"color:{self._text_color};font-size:11px;"
            "font-family:'Segoe UI',sans-serif;background:transparent;"
        )
        hl.addWidget(self._status)
        hl.addStretch()

        app_signals.connectivity_changed.connect(self.apply_status)
        existing = connectivity_monitor.status
        if existing is not None:
            self.apply_status(existing)

    def apply_status(self, status: object) -> None:
        if not isinstance(status, ConnectivityStatus):
            return
        self._dot.setStyleSheet(
            f"color:{status.dot_color()};font-size:9px;background:transparent;"
        )
        self._status.setText(status.status_bar_text(mode=self._mode_label))
