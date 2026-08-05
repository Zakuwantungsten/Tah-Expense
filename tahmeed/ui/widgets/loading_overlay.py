"""Semi-transparent loading overlay with spinner + progress bar."""

from __future__ import annotations

from typing import Optional

import qtawesome as qta

from PySide6.QtCore import Qt, QTimer, QEvent, QObject
from PySide6.QtGui import QTransform
from PySide6.QtWidgets import (
    QApplication, QFrame, QLabel, QProgressBar, QVBoxLayout, QWidget,
)


class LoadingOverlay(QFrame):
    """Covers ``parent`` while async work is in progress."""

    def __init__(self, parent: QWidget, message: str = "Loading…") -> None:
        super().__init__(parent)
        self._angle = 0
        self.setObjectName("loadingOverlay")
        self.setStyleSheet(
            "QFrame#loadingOverlay {"
            "  background: rgba(244, 246, 248, 0.88);"
            "  border: none;"
            "}"
        )
        self.hide()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(12)

        self._icon = QLabel()
        self._icon.setFixedSize(36, 36)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet("background: transparent;")
        layout.addWidget(self._icon, alignment=Qt.AlignCenter)

        self._message = QLabel(message)
        self._message.setAlignment(Qt.AlignCenter)
        self._message.setStyleSheet(
            "color: #6B7280; font-size: 13px; font-weight: 600;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        layout.addWidget(self._message, alignment=Qt.AlignCenter)

        self._bar = QProgressBar()
        self._bar.setFixedWidth(220)
        self._bar.setFixedHeight(10)
        self._bar.setTextVisible(False)
        self._bar.setRange(0, 0)  # indeterminate by default
        self._bar.setStyleSheet(
            "QProgressBar {"
            "  background: #E5E7EB; border: none; border-radius: 5px;"
            "}"
            "QProgressBar::chunk {"
            "  background: #0077C5; border-radius: 5px;"
            "}"
        )
        layout.addWidget(self._bar, alignment=Qt.AlignCenter)

        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._rotate)

        parent.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[name-defined]
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._sync_geometry()
        return False

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def _rotate(self) -> None:
        self._angle = (self._angle + 24) % 360
        try:
            base = qta.icon("mdi.loading", color="#0077C5").pixmap(32, 32)
            rotated = base.transformed(
                QTransform().rotate(self._angle),
                Qt.SmoothTransformation,
            )
            self._icon.setPixmap(rotated)
        except Exception:
            self._icon.setText("…")

    def show_loading(
        self,
        message: Optional[str] = None,
        *,
        value: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> None:
        if message:
            self._message.setText(message)
        if maximum is not None and maximum > 0:
            self._bar.setRange(0, maximum)
            self._bar.setValue(max(0, min(maximum, value or 0)))
        else:
            self._bar.setRange(0, 0)
        self._sync_geometry()
        self._rotate()
        self.show()
        self.raise_()
        self._timer.start()
        QApplication.processEvents()

    def hide_loading(self) -> None:
        self._timer.stop()
        self.hide()
