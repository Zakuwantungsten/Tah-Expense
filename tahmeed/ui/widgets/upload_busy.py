"""Modal branded busy dialog for file upload / Excel parse work."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, Optional, TypeVar

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tahmeed.ui.widgets.brand_loader import BrandLoaderPanel

_T = TypeVar("_T")


class UploadCancelled(Exception):
    """User cancelled an UploadBusy operation."""


class _BusyDialog(QDialog):
    """Lightweight stand-in for QProgressDialog with branded visuals."""

    canceled = Signal()

    def __init__(
        self,
        parent: Optional[QWidget],
        message: str,
        *,
        title: str,
        cancellable: bool,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumWidth(360)
        self.setStyleSheet(
            "QDialog { background: #FFFFFF; color: #111827; }"
            "QPushButton {"
            "  background: #FFFFFF; color: #111827; border: 1px solid #D1D5DB;"
            "  border-radius: 5px; padding: 6px 16px; min-width: 72px;"
            "  min-height: 28px; font-size: 13px;"
            "}"
            "QPushButton:hover { background: #F3F4F6; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(8)

        self._panel = BrandLoaderPanel(self, message, mark_width=160, show_progress=True)
        root.addWidget(self._panel)

        self._cancel_btn: Optional[QPushButton] = None
        if cancellable:
            row = QHBoxLayout()
            row.addStretch(1)
            self._cancel_btn = QPushButton("Cancel", self)
            self._cancel_btn.clicked.connect(self.canceled.emit)
            row.addWidget(self._cancel_btn)
            root.addLayout(row)

        self._was_canceled = False
        self._maximum = 0
        self._value = 0

    def labelText(self) -> str:
        return self._panel.display_text()

    def setLabelText(self, text: str) -> None:
        self._panel.set_message(text)

    def setMaximum(self, maximum: int) -> None:
        self._maximum = max(0, int(maximum))
        self._panel.set_progress(value=self._value, maximum=self._maximum)

    def maximum(self) -> int:
        return self._maximum

    def setValue(self, value: int) -> None:
        self._value = max(0, int(value))
        if self._maximum > 0:
            self._panel.set_progress(value=self._value, maximum=self._maximum)

    def wasCanceled(self) -> bool:
        return self._was_canceled

    def setCancelButton(self, button: Optional[QPushButton]) -> None:
        if button is None and self._cancel_btn is not None:
            self._cancel_btn.hide()
            self._cancel_btn.setEnabled(False)

    def setCancelButtonText(self, text: str) -> None:
        if self._cancel_btn is not None:
            self._cancel_btn.setText(text)

    def mark_canceled(self) -> None:
        self._was_canceled = True

    def reset(self) -> None:
        self._value = 0
        self._maximum = 0
        self._panel.reset_progress()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._panel.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._panel.stop()
        super().hideEvent(event)


class UploadBusy:
    """Show a branded progress dialog while an upload is processed.

    Use as a context manager around parse / staging work so the user always
    sees that the system is busy before any preview or result appears.

    Pass ``cancellable=True`` for a Cancel button. ``should_cancel`` is safe
    to call from a worker thread; ``raise_if_cancelled`` / ``await_or_cancel``
    belong on the UI / asyncio thread.
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        message: str = "Processing upload…",
        *,
        title: str = "Upload",
        cancellable: bool = False,
    ) -> None:
        self._cancellable = cancellable
        self._cancel_event = threading.Event()
        self._closing = False
        self._dlg = _BusyDialog(
            parent,
            message,
            title=title,
            cancellable=cancellable,
        )
        if cancellable:
            self._dlg.canceled.connect(self._on_canceled)

    def _on_canceled(self) -> None:
        if self._closing:
            return
        self._cancel_event.set()
        self._dlg.mark_canceled()
        self._dlg.setLabelText("Cancelling…")
        self._dlg.setCancelButton(None)

    def should_cancel(self) -> bool:
        """Thread-safe: True after the user clicks Cancel."""
        return self._cancel_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.should_cancel() or (
            self._cancellable and not self._closing and self._dlg.wasCanceled()
        ):
            self._cancel_event.set()
            raise UploadCancelled()

    async def tick(self) -> None:
        """Yield to Qt so the overlay can paint; raise if cancelled."""
        self.raise_if_cancelled()
        await asyncio.sleep(0)
        self.raise_if_cancelled()

    async def await_or_cancel(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Await *coro*, aborting soon after Cancel is clicked."""
        from tahmeed.ui.async_utils import create_task

        try:
            self.raise_if_cancelled()
        except UploadCancelled:
            coro.close()
            raise
        task = create_task(coro)
        try:
            while True:
                if self.should_cancel():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                    raise UploadCancelled()
                done, _ = await asyncio.wait({task}, timeout=0.12)
                if task in done:
                    return task.result()
        except UploadCancelled:
            raise
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            raise

    def __enter__(self) -> "UploadBusy":
        self._dlg.show()
        from tahmeed.ui.async_utils import safe_process_events
        safe_process_events()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def update(self, message: str, *, value: Optional[int] = None, maximum: Optional[int] = None) -> None:
        if self.should_cancel():
            return
        if maximum is not None:
            self._dlg.setMaximum(max(0, maximum))
        if value is not None and self._dlg.maximum() > 0:
            self._dlg.setValue(max(0, value))
        self._dlg.setLabelText(message)
        from tahmeed.ui.async_utils import safe_process_events
        safe_process_events()

    def close(self) -> None:
        self._closing = True
        if self._cancellable:
            try:
                self._dlg.canceled.disconnect(self._on_canceled)
            except (RuntimeError, TypeError):
                pass
        self._dlg.reset()
        self._dlg.hide()
        self._dlg.close()
