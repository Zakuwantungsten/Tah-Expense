"""Semi-transparent loading overlay with branded horse fade pulse + context text."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QEvent, QObject
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from tahmeed.ui.widgets.brand_loader import BrandLoaderPanel


class LoadingOverlay(QFrame):
    """Covers ``parent`` while async work is in progress."""

    def __init__(self, parent: QWidget, message: str = "Loading…") -> None:
        super().__init__(parent)
        self.setObjectName("loadingOverlay")
        # Must remain an embedded child — never a top-level HWND flash.
        self.setWindowFlags(Qt.Widget)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_NativeWindow, False)
        self.setStyleSheet(
            "QFrame#loadingOverlay {"
            "  background: rgba(255, 255, 255, 0.55);"
            "  border: none;"
            "}"
        )
        self.hide()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)

        # Horse + message only — no progress bar on tab/page overlays.
        self._panel = BrandLoaderPanel(self, message, mark_width=160, show_progress=False)
        layout.addWidget(self._panel, alignment=Qt.AlignCenter)

        parent.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[name-defined]
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._sync_geometry()
        return False

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def show_loading(
        self,
        message: Optional[str] = None,
        *,
        value: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> None:
        if message:
            self._panel.set_message(message)
        self._panel.set_progress(value=value, maximum=maximum)
        self._sync_geometry()
        self.show()
        self.raise_()
        self._panel.start()
        from tahmeed.ui.async_utils import safe_process_events
        safe_process_events()

    def hide_loading(self) -> None:
        self._panel.reset_progress()
        self._panel.stop()
        self.hide()


def show_widget_loading(widget: QWidget, message: str = "Loading…") -> LoadingOverlay:
    """Attach (if needed) and show a branded horse overlay on ``widget``."""
    overlay = getattr(widget, "_loading_overlay", None)
    if not isinstance(overlay, LoadingOverlay):
        overlay = LoadingOverlay(widget, message)
        widget._loading_overlay = overlay  # type: ignore[attr-defined]
    overlay.show_loading(message)
    return overlay


def hide_widget_loading(widget: QWidget) -> None:
    overlay = getattr(widget, "_loading_overlay", None)
    if isinstance(overlay, LoadingOverlay):
        overlay.hide_loading()
