"""Semi-transparent loading overlay with branded horse fade pulse + context text."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QEvent, QObject
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from tahmeed.ui.widgets.brand_loader import BrandLoaderPanel


class LoadingOverlay(QFrame):
    """Covers ``parent`` while async work is in progress.

    Must stay an embedded child widget. On Windows, stylesheet ``rgba()``
    backgrounds (and ``WA_TranslucentBackground``) promote a native HWND and
    briefly flash a tiny top-level window with the app title + min/max/close.
    The dim fill is therefore painted in ``paintEvent`` onto the parent surface.
    """

    def __init__(
        self,
        parent: QWidget,
        message: str = "Loading…",
        *,
        show_progress: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("loadingOverlay")
        self._ensure_child_widget()
        # No rgba()/translucent stylesheet — that is what flashed the HWND.
        self.setStyleSheet(
            "QFrame#loadingOverlay { background: transparent; border: none; }"
        )
        self.hide()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)

        # Tab/page overlays stay message-only; import dialogs pass show_progress=True.
        self._panel = BrandLoaderPanel(
            self, message, mark_width=160, show_progress=show_progress
        )
        layout.addWidget(self._panel, alignment=Qt.AlignCenter)

        parent.installEventFilter(self)

    def _ensure_child_widget(self) -> None:
        """Force embedded-child chrome — never a top-level title-bar window."""
        self.setWindowFlags(Qt.Widget)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_NativeWindow, False)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[name-defined]
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._sync_geometry()
        return False

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        # Soft white veil without native translucency / layered HWND.
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(255, 255, 255, 140))
        painter.end()

    def show_loading(
        self,
        message: Optional[str] = None,
        *,
        value: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> None:
        self._ensure_child_widget()
        if message:
            self._panel.set_message(message)
        # Progress + geometry BEFORE show — first paint must be full-size overlay
        # (never a zero-size / title-bar-only HWND flash).
        self._panel.set_progress(value=value, maximum=maximum)
        self._sync_geometry()
        parent = self.parentWidget()
        if parent is not None and (self.width() <= 1 or self.height() <= 1):
            self.setGeometry(parent.rect())
        self._panel.start()
        self.show()
        self.raise_()
        # If Windows still promoted us, yank back under the parent immediately.
        if self.isWindow() and self.parentWidget() is not None:
            parent = self.parentWidget()
            self.setParent(parent)
            self._ensure_child_widget()
            self._sync_geometry()
            self.show()
            self.raise_()
        # Paint now without pumping the event queue: processEvents() here ran
        # queued timers re-entrantly (e.g. a modal truck dialog mid-navigation).
        self.repaint()
        self._panel.repaint()

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
